# -*- coding: utf-8 -*-
"""真实K线多标的×多时段 RSI 策略 OOS 回测（论文实证）

数据源: data/watchlist_kline/watchlist_kline_store.db（真实历史K线）
  - 42 只自选股 × 64 天 (2026-05-18 ~ 2026-08-14)
  - 上证指数 sh000001 × 801 天 (2023-04-24 ~ 2026-08-14) → 多时段滚动验证

用法:
    python scripts/backtest_multi_symbol.py
    python scripts/backtest_multi_symbol.py --window 64 --train 0.6
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

KLINE_DB = Path(__file__).resolve().parents[1] / "data" / "watchlist_kline" / "watchlist_kline_store.db"


def load_kline_db() -> Dict[str, Dict[str, Any]]:
    """从 kline.db 加载全部标的 close 序列（升序）。"""
    conn = sqlite3.connect(str(KLINE_DB))
    symbols: Dict[str, Dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT d.code, d.date, d.close, n.name "
        "FROM daily_kline d LEFT JOIN stock_names n ON d.code = n.code "
        "ORDER BY d.code, d.date"):
        code, date, close, name = row
        if code not in symbols:
            symbols[code] = {"name": name or code, "dates": [], "closes": []}
        symbols[code]["dates"].append(date)
        symbols[code]["closes"].append(float(close))
    conn.close()
    return symbols


def split_series(prices: List[float], train: float = 0.6, oos: float = 0.2) -> Tuple[List[float], List[float]]:
    n = len(prices)
    train_end = int(n * train)
    oos_end = train_end + int(n * oos)
    return prices[:train_end], prices[oos_end:]


def compute_rsi(prices: List[float], period: int = 14) -> List[float]:
    """Wilder 平滑 RSI 序列（长度 = len(prices) - period）。"""
    if len(prices) < period + 1:
        return []
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_values = []
    for i in range(len(deltas)):
        if i >= period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss > 1e-10:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - 100 / (1 + rs))
        else:
            rsi_values.append(100.0)
    return rsi_values


def backtest_rsi_trend(prices: List[float], period: int = 14,
                       threshold: float = 50,
                       initial_cash: float = 1_000_000.0) -> Dict[str, Any]:
    """RSI 趋势跟随：RSI(昨) > threshold 持有，<= threshold 清仓。"""
    if len(prices) < period + 2:
        return {"cumulative_return": 0.0, "sharpe_ratio": 0.0,
                "max_drawdown": 0.0, "trades": 0, "final_value": initial_cash}
    rsi_vals = compute_rsi(prices, period)

    cash = initial_cash
    position = 0.0
    trades = 0
    net_values = []

    # RSI[i] 对应 prices[i+1..i+period+1] 的窗口；用 RSI 信号驱动次日持仓
    for i in range(len(rsi_vals)):
        price = prices[i + 1]  # 信号产生日收盘（无未来函数：RSI 只用 <= i+1 数据）
        if rsi_vals[i] > threshold and position == 0.0:
            position = cash / price
            cash = 0.0
            trades += 1
        elif rsi_vals[i] <= threshold and position > 0.0:
            cash = position * price
            position = 0.0
            trades += 1
        net_values.append(cash + position * price)

    final_value = net_values[-1] if net_values else initial_cash
    cumulative_return = (final_value - initial_cash) / initial_cash

    daily_returns = []
    for i in range(1, len(net_values)):
        prev = net_values[i - 1]
        if prev > 0:
            daily_returns.append((net_values[i] - prev) / prev)

    if daily_returns:
        mean_ret = sum(daily_returns) / len(daily_returns)
        var_ret = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
        sharpe = math.sqrt(252) * mean_ret / math.sqrt(var_ret) if var_ret > 1e-10 else 0.0
    else:
        sharpe = 0.0

    peak = initial_cash
    max_dd = 0.0
    for v in net_values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return {
        "cumulative_return": cumulative_return,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_dd,
        "trades": trades,
        "final_value": final_value,
    }


def grid_optimize(train_prices: List[float]) -> Tuple[Tuple[int, float], Dict[str, Any]]:
    """训练集网格搜索 RSI(period, threshold)。"""
    best_score = -999.0
    best_params = (14, 50.0)
    best_metrics = None
    for period in (7, 10, 14, 20):
        for threshold in (30.0, 40.0, 50.0, 60.0):
            m = backtest_rsi_trend(train_prices, period, threshold)
            if m["sharpe_ratio"] > best_score:
                best_score = m["sharpe_ratio"]
                best_params = (period, threshold)
                best_metrics = m
    return best_params, best_metrics


def buy_hold(prices: List[float]) -> float:
    if len(prices) < 2:
        return 0.0
    return (prices[-1] - prices[0]) / prices[0]


def random_baseline(oos_prices: List[float], n_samples: int = 50) -> Dict[str, float]:
    """随机参数基线（同分布）。"""
    sharpes, returns = [], []
    for _ in range(n_samples):
        period = random.choice((5, 7, 10, 14, 20, 30))
        threshold = random.choice((30.0, 40.0, 50.0, 60.0, 70.0))
        m = backtest_rsi_trend(oos_prices, period, threshold)
        sharpes.append(m["sharpe_ratio"])
        returns.append(m["cumulative_return"])
    mean_s = sum(sharpes) / len(sharpes)
    std_s = (sum((s - mean_s) ** 2 for s in sharpes) / len(sharpes)) ** 0.5
    return {"mean_sharpe": mean_s, "std_sharpe": std_s, "samples": n_samples,
            "mean_return": sum(returns) / len(returns)}


def run_symbol(code: str, name: str, closes: List[float], train_ratio: float,
               oos_ratio: float) -> Dict[str, Any]:
    n = len(closes)
    train, oos = split_series(closes, train_ratio, oos_ratio)
    buy_hold_oos = buy_hold(oos)

    best_params, train_metrics = grid_optimize(train)
    oos_metrics = backtest_rsi_trend(oos, *best_params)
    baseline = random_baseline(oos)

    z = ((oos_metrics["sharpe_ratio"] - baseline["mean_sharpe"]) /
         baseline["std_sharpe"]) if baseline["std_sharpe"] > 1e-12 else 0.0

    return {
        "code": code, "name": name, "days": n,
        "train_days": len(train), "oos_days": len(oos),
        "best_params": list(best_params),
        "train_return": train_metrics["cumulative_return"],
        "train_sharpe": train_metrics["sharpe_ratio"],
        "oos_return": oos_metrics["cumulative_return"],
        "oos_sharpe": oos_metrics["sharpe_ratio"],
        "oos_max_drawdown": oos_metrics["max_drawdown"],
        "oos_trades": oos_metrics["trades"],
        "buy_hold_oos": buy_hold_oos,
        "excess_return": oos_metrics["cumulative_return"] - buy_hold_oos,
        "baseline_mean_sharpe": baseline["mean_sharpe"],
        "baseline_std_sharpe": baseline["std_sharpe"],
        "z_score": z,
    }


def main():
    ap = argparse.ArgumentParser(description="多标的×多时段 RSI OOS 回测")
    ap.add_argument("--train", type=float, default=0.6)
    ap.add_argument("--oos", type=float, default=0.2)
    ap.add_argument("--min-days", type=int, default=30, help="少于该天数跳过")
    ap.add_argument("--output", default="rsi_multi_oos_results.json")
    args = ap.parse_args()

    symbols = load_kline_db()
    print("=" * 72)
    print(f"多标的 RSI 趋势跟随 OOS 回测（真实K线: {len(symbols)} 个标的）")
    print("=" * 72)

    results = []
    for code, info in sorted(symbols.items()):
        closes = info["closes"]
        if len(closes) < args.min_days:
            print(f"  [skip] {code} {info['name']}: 仅 {len(closes)} 天")
            continue
        r = run_symbol(code, info["name"], closes, args.train, args.oos)
        results.append(r)
        print(f"  {code} {r['name']:<6} {r['days']:>4}天 | "
              f"param RSI({r['best_params'][0]:.0f},T={r['best_params'][1]:.0f}) | "
              f"train {r['train_return']:>7.2%} sharpe {r['train_sharpe']:.2f} | "
              f"OOS {r['oos_return']:>8.2%} sharpe {r['oos_sharpe']:.2f} | "
              f"买入持有 {r['buy_hold_oos']:>7.2%} | 超额 {r['excess_return']:>7.2%} | "
              f"z={r['z_score']:.2f}")

    if not results:
        print("无可用标的")
        return

    # 汇总统计
    wins = sum(1 for r in results if r["excess_return"] > 0)
    pos_oos = sum(1 for r in results if r["oos_return"] > 0)
    avg_oos = sum(r["oos_return"] for r in results) / len(results)
    avg_excess = sum(r["excess_return"] for r in results) / len(results)
    avg_sharpe = sum(r["oos_sharpe"] for r in results) / len(results)
    sig_count = sum(1 for r in results if abs(r["z_score"]) >= 1.96)

    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    print(f"有效标的数: {len(results)}")
    print(f"OOS 为正的标的: {pos_oos}/{len(results)} ({pos_oos/len(results):.0%})")
    print(f"跑赢买入持有的标的: {wins}/{len(results)} ({wins/len(results):.0%})")
    print(f"平均 OOS 收益: {avg_oos:.2%}")
    print(f"平均超额收益: {avg_excess:.2%}")
    print(f"平均 OOS 夏普: {avg_sharpe:.3f}")
    print(f"|z|>=1.96 显著标的: {sig_count}/{len(results)}")

    # 上证指数多时段滚动（801天）
    idx = symbols.get("sh000001")
    if idx and len(idx["closes"]) >= 200:
        print("\n" + "=" * 72)
        print("上证指数 (sh000001) 多时段滚动回测（801天）")
        print("=" * 72)
        closes = idx["closes"]
        window = 120
        step = 60
        rolling = []
        for start in range(0, len(closes) - window, step):
            seg = closes[start:start + window]
            r = run_symbol("sh000001", "上证指数", seg, args.train, args.oos)
            r["window"] = f"{idx['dates'][start]}~{idx['dates'][start+window-1]}"
            rolling.append(r)
            print(f"  {r['window']} | param RSI({r['best_params'][0]:.0f},T={r['best_params'][1]:.0f}) | "
                  f"train {r['train_return']:>7.2%} | OOS {r['oos_return']:>8.2%} | "
                  f"买入持有 {r['buy_hold_oos']:>7.2%} | z={r['z_score']:.2f}")
        win_roll = sum(1 for r in rolling if r["excess_return"] > 0)
        print(f"  滚动窗口数: {len(rolling)}, 跑赢买入持有: {win_roll}/{len(rolling)} ({win_roll/len(rolling):.0%})")
        results.extend(rolling)

    out = {
        "data_source": str(KLINE_DB),
        "train_ratio": args.train,
        "oos_ratio": args.oos,
        "summary": {
            "symbols": len(results),
            "oos_positive": pos_oos,
            "beat_buy_hold": wins,
            "avg_oos_return": avg_oos,
            "avg_excess_return": avg_excess,
            "avg_oos_sharpe": avg_sharpe,
            "significant": sig_count,
        },
        "results": results,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {args.output}")


if __name__ == "__main__":
    main()
