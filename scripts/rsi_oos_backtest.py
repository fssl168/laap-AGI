# -*- coding: utf-8 -*-
"""真实K线驱动的RSI/均线策略OOS回测

针对上升趋势使用趋势跟踪（均线交叉），用于论文实证评估。

用法:
    python scripts/rsi_oos_backtest.py              # 默认
    python scripts/rsi_oos_backtest.py --strategy ma  # 均线策略
    python scripts/rsi_oos_backtest.py --strategy rsi --trend  # RSI趋势跟随
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_kline(path: str = "real_data/kline.json") -> List[float]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def split_series(prices: List[float], train: float = 0.6, oos: float = 0.2) -> Tuple[List[float], List[float]]:
    n = len(prices)
    train_end = int(n * train)
    oos_end = train_end + int(n * oos)
    return prices[:train_end], prices[oos_end:]


def moving_average(prices: List[float], window: int) -> List[float]:
    """计算简单移动平均。"""
    result = []
    for i in range(len(prices)):
        if i < window - 1:
            result.append(None)
        else:
            result.append(sum(prices[i - window + 1:i + 1]) / window)
    return result


def backtest_ma_cross(prices: List[float], short: int = 5, long: int = 20,
                      initial_cash: float = 1_000_000.0) -> Dict[str, Any]:
    """均线交叉策略：金叉买入、死叉卖出。"""
    short_ma = moving_average(prices, short)
    long_ma = moving_average(prices, long)
    
    cash = initial_cash
    position = 0.0
    prev_diff = 0.0
    trades = 0
    
    net_values = []
    
    for i in range(len(prices)):
        price = prices[i]
        sma = short_ma[i]
        lma = long_ma[i]
        
        if sma is None or lma is None:
            net_values.append(cash + position * (prices[i-1] if i > 0 else price))
            continue
        
        diff = sma - lma
        
        # 金叉买入
        if prev_diff <= 0 < diff and position == 0.0:
            position = cash / price
            cash = 0.0
            trades += 1
        # 死叉卖出
        elif prev_diff >= 0 > diff and position > 0.0:
            cash = position * price
            position = 0.0
            trades += 1
        
        prev_diff = diff
        equity = position * price
        net_values.append(cash + equity)
    
    final_value = net_values[-1] if net_values else initial_cash
    cumulative_return = (final_value - initial_cash) / initial_cash
    
    # 计算夏普比率
    daily_returns = []
    for i in range(1, len(net_values)):
        if net_values[i - 1] > 0:
            daily_returns.append((net_values[i] - net_values[i - 1]) / net_values[i - 1])
    
    if daily_returns:
        mean_ret = sum(daily_returns) / len(daily_returns)
        var_ret = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
        sharpe = math.sqrt(252) * mean_ret / math.sqrt(var_ret) if var_ret > 1e-10 else 0
    else:
        sharpe = 0
    
    # 最大回撤
    max_drawdown = 0.0
    peak = initial_cash
    for v in net_values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_drawdown:
            max_drawdown = dd
    
    return {
        "cumulative_return": cumulative_return,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "trades": trades,
        "final_value": final_value,
    }


def backtest_rsi_trend(prices: List[float], rsi_period: int = 14,
                       threshold: float = 50, initial_cash: float = 1_000_000.0) -> Dict[str, Any]:
    """RSI趋势跟随：RSI > 50 持有，RSI < 50 清仓。"""
    # 计算RSI
    if len(prices) < rsi_period + 1:
        return {"error": "too short"}
    
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    
    avg_gain = sum(gains[:rsi_period]) / rsi_period
    avg_loss = sum(losses[:rsi_period]) / rsi_period
    
    rsi_values = [100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100]
    
    for i in range(rsi_period, len(deltas)):
        avg_gain = (avg_gain * (rsi_period - 1) + gains[i]) / rsi_period
        avg_loss = (avg_loss * (rsi_period - 1) + losses[i]) / rsi_period
        if avg_loss > 1e-10:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))
        else:
            rsi_values.append(100)
    
    # RSI偏移1天（避免未来函数）
    cash = initial_cash
    position = 0.0
    trades = 0
    
    net_values = []
    
    for i in range(len(rsi_values)):
        price = prices[i + 1] if i + 1 < len(prices) else prices[-1]
        rsi = rsi_values[i]
        
        if rsi > threshold and position == 0.0:
            position = cash / price
            trades += 1
        elif rsi <= threshold and position > 0.0:
            cash = position * price
            position = 0.0
            trades += 1
        
        equity = position * price
        net_values.append(cash + equity)
    
    final_value = net_values[-1] if net_values else initial_cash
    cumulative_return = (final_value - initial_cash) / initial_cash
    
    # 计算夏普
    daily_returns = []
    for i in range(1, len(net_values)):
        if net_values[i - 1] > 0:
            daily_returns.append((net_values[i] - net_values[i - 1]) / net_values[i - 1])
    
    if daily_returns:
        mean_ret = sum(daily_returns) / len(daily_returns)
        var_ret = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
        sharpe = math.sqrt(252) * mean_ret / math.sqrt(var_ret) if var_ret > 1e-10 else 0
    else:
        sharpe = 0
    
    max_drawdown = 0.0
    peak = initial_cash
    for v in net_values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_drawdown:
            max_drawdown = dd
    
    return {
        "cumulative_return": cumulative_return,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "trades": trades,
        "final_value": final_value,
    }


def random_baseline(prices: List[float], strategy: str = "ma", n_samples: int = 100) -> Dict[str, float]:
    """生成随机策略基线。"""
    returns = []
    sharpes = []
    
    for _ in range(n_samples):
        if strategy == "ma":
            short = random.randint(3, 10)
            long = random.randint(15, 30)
            m = backtest_ma_cross(prices, short, long)
        else:
            threshold = random.choice([40, 50, 60])
            m = backtest_rsi_trend(prices, threshold=threshold)
        
        returns.append(m["cumulative_return"])
        sharpes.append(m["sharpe_ratio"])
    
    return {
        "mean_return": sum(returns) / len(returns),
        "std_return": (sum((r - sum(returns)/len(returns)) ** 2 for r in returns) / len(returns)) ** 0.5,
        "mean_sharpe": sum(sharpes) / len(sharpes),
        "std_sharpe": (sum((s - sum(sharpes)/len(sharpes)) ** 2 for s in sharpes) / len(sharpes)) ** 0.5,
    }


def main():
    ap = argparse.ArgumentParser(description="RSI/MA OOS回测")
    ap.add_argument("--kline", default="real_data/kline.json", help="K线数据文件")
    ap.add_argument("--days", type=int, default=120, help="使用最近N天")
    ap.add_argument("--train", type=float, default=0.6, help="训练集比例")
    ap.add_argument("--oos", type=float, default=0.2, help="OOS比例")
    ap.add_argument("--strategy", choices=["ma", "rsi"], default="ma", help="策略类型")
    ap.add_argument("--output", default="rsi_oos_results.json", help="输出文件")
    args = ap.parse_args()
    
    prices = load_kline(args.kline)[-args.days:]
    train_prices, oos_prices = split_series(prices, args.train, args.oos)
    
    print("=" * 60)
    print(f"真实K线 OOS回测 ({args.strategy.upper()}策略)")
    print("=" * 60)
    print(f"K线数据: {len(prices)} 天")
    print(f"训练集: {len(train_prices)} 天 ({args.train:.0%})")
    print(f"OOS验证: {len(oos_prices)} 天 ({args.oos:.0%})")
    print(f"价格范围: {min(prices):.2f} ~ {max(prices):.2f} (+{(prices[-1]/prices[0]-1)*100:.1f}%)")
    print()
    
    # 网格搜索最优参数
    if args.strategy == "ma":
        params = [(s, l) for s in [3, 5, 7, 10] for l in [15, 20, 25, 30] if s < l]
        best_score = -999
        best_params = None
        results = []
        
        for short, long in params:
            m = backtest_ma_cross(train_prices, short, long)
            score = m["sharpe_ratio"]
            results.append((score, short, long, m))
            if score > best_score:
                best_score = score
                best_params = (short, long)
        
        ranked = sorted(results, key=lambda x: -x[0])
        
        print("训练集参数优化 (Top 5):")
        for i, (score, short, long, m) in enumerate(ranked[:5], 1):
            print(f"  {i}. MA({short},{long}): sharpe={score:.3f}, cumret={m['cumulative_return']:.2%}")
        
        # OOS验证
        oos_metrics = backtest_ma_cross(oos_prices, *best_params)
        
    else:  # rsi
        params = [(14, t) for t in [40, 50, 60]]
        best_score = -999
        best_params = None
        results = []
        
        for period, threshold in params:
            m = backtest_rsi_trend(train_prices, period, threshold)
            score = m["sharpe_ratio"]
            results.append((score, period, threshold, m))
            if score > best_score:
                best_score = score
                best_params = (period, threshold)
        
        ranked = sorted(results, key=lambda x: -x[0])
        
        print("训练集参数优化 (Top 5):")
        for i, (score, period, threshold, m) in enumerate(ranked[:5], 1):
            print(f"  {i}. RSI({period}), threshold={threshold}: sharpe={score:.3f}, cumret={m['cumulative_return']:.2%}")
        
        oos_metrics = backtest_rsi_trend(oos_prices, *best_params)
    
    print("\n" + "=" * 60)
    print("OOS验证结果")
    print("=" * 60)
    
    if args.strategy == "ma":
        print(f"最佳参数: MA({best_params[0]},{best_params[1]})")
        # 重新获取训练集指标
        train_metrics = backtest_ma_cross(train_prices, *best_params)
    else:
        print(f"最佳参数: RSI({best_params[0]}), threshold={best_params[1]}")
        train_metrics = backtest_rsi_trend(train_prices, *best_params)
    
    print(f"\n{'指标':<20} {'训练集':>12} {'OOS':>12}")
    print("-" * 44)
    print(f"{'累计收益':<20} {train_metrics['cumulative_return']:>11.2%} {oos_metrics['cumulative_return']:>11.2%}")
    print(f"{'夏普比率':<20} {train_metrics['sharpe_ratio']:>12.3f} {oos_metrics['sharpe_ratio']:>12.3f}")
    print(f"{'最大回撤':<20} {train_metrics['max_drawdown']:>11.2%} {oos_metrics['max_drawdown']:>11.2%}")
    print(f"{'交易次数':<20} {train_metrics['trades']:>12d} {oos_metrics['trades']:>12d}")
    print()
    
    # 随机基线
    baseline = random_baseline(oos_prices, args.strategy)
    print("=" * 60)
    print("统计显著性检验")
    print("=" * 60)
    print(f"随机基线夏普: {baseline['mean_sharpe']:.3f} ± {baseline['std_sharpe']:.3f}")
    print(f"最佳OOS夏普: {oos_metrics['sharpe_ratio']:.3f}")
    z_score = (oos_metrics['sharpe_ratio'] - baseline['mean_sharpe']) / baseline['std_sharpe'] if baseline['std_sharpe'] > 0 else 0
    print(f"Z分数: {z_score:.2f}σ")
    print()
    
    # 保存结果
    result = {
        "kline_days": len(prices),
        "train_days": len(train_prices),
        "oos_days": len(oos_prices),
        "strategy": args.strategy,
        "best_params": best_params,
        "train_metrics": train_metrics,
        "oos_metrics": oos_metrics,
        "random_baseline": {
            "mean_sharpe": baseline['mean_sharpe'],
            "std_sharpe": baseline['std_sharpe'],
            "z_score": z_score,
            "samples": 100,
        },
        "top_configs": [
            {"sharpe": r[0], "params": r[1:3]}
            for r in ranked[:5]
        ],
    }
    
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"结果已保存: {args.output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
