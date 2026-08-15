# -*- coding: utf-8 -*-
"""真实800天数据 OOS 门禁验证（诚实评估：网格寻优参数能否通过 OOS 门禁）

OOS 门禁标准（与系统 QuantEvolutionGate 一致）:
  oos_cumret >= 0 且 oos_sharpe >= train_sharpe * 0.8
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_closes(sym: str) -> list:
    p = Path(f"D:/laap-AGI/real_data/kline_{sym}.json")
    return json.loads(p.read_text(encoding="utf-8"))


def split_series(prices, train=0.6, oos=0.2):
    n = len(prices)
    te = int(n * train)
    oe = te + int(n * oos)
    return prices[:te], prices[oe:]


def compute_rsi(prices, period=14):
    if len(prices) < period + 1:
        return []
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    out = []
    for i in range(len(deltas)):
        if i >= period:
            ag = (ag * (period - 1) + gains[i]) / period
            al = (al * (period - 1) + losses[i]) / period
        out.append(100 - 100/(1 + ag/al) if al > 1e-10 else 100.0)
    return out


def backtest_rsi(prices, period=14, threshold=50, cash0=1_000_000.0):
    rsi = compute_rsi(prices, period)
    if len(rsi) == 0:
        return {"cumret": 0.0, "sharpe": 0.0, "trades": 0}
    cash, pos, trades = cash0, 0.0, 0
    nv = []
    for i in range(len(rsi)):
        px = prices[i+1] if i+1 < len(prices) else prices[-1]
        if rsi[i] > threshold and pos == 0.0:
            pos, cash, trades = cash/px, 0.0, trades+1
        elif rsi[i] <= threshold and pos > 0.0:
            cash, pos, trades = pos*px, 0.0, trades+1
        nv.append(cash + pos*px)
    cumret = (nv[-1] - cash0)/cash0
    rets = [(nv[i]-nv[i-1])/nv[i-1] for i in range(1, len(nv)) if nv[i-1] > 0]
    if rets:
        m = sum(rets)/len(rets)
        v = sum((r-m)**2 for r in rets)/len(rets)
        sharpe = math.sqrt(252)*m/math.sqrt(v) if v > 1e-10 else 0.0
    else:
        sharpe = 0.0
    return {"cumret": cumret, "sharpe": sharpe, "trades": trades}


def main():
    symbols = ["600519", "000001", "000858"]
    names = {"600519": "贵州茅台", "000001": "平安银行", "000858": "五粮液"}
    print("=" * 72)
    print("真实 800 天数据 OOS 门禁验证（RSI 网格寻优 → OOS 验证）")
    print("OOS 门禁: oos_cumret>=0 且 oos_sharpe >= train_sharpe*0.8")
    print("=" * 72)

    all_pass = True
    for sym in symbols:
        closes = load_closes(sym)
        train, oos = split_series(closes)
        print(f"\n[{sym} {names[sym]}] {len(closes)}天 (train {len(train)} / oos {len(oos)})")

        # 网格寻优（与系统一致）
        best = None
        for period in (7, 10, 14, 20):
            for thr in (30.0, 40.0, 50.0, 60.0):
                m = backtest_rsi(train, period, thr)
                if best is None or m["sharpe"] > best[2]["sharpe"]:
                    best = (period, thr, m)
        period, thr, tm = best
        om = backtest_rsi(oos, period, thr)

        # OOS 门禁
        gate_ok = om["cumret"] >= 0 and om["sharpe"] >= tm["sharpe"] * 0.8
        all_pass = all_pass and gate_ok
        print(f"  最优参数 RSI({period},T={thr:.0f})")
        print(f"  train: cumret {tm['cumret']:>7.2%} sharpe {tm['sharpe']:.3f} trades {tm['trades']}")
        print(f"  oos:   cumret {om['cumret']:>7.2%} sharpe {om['sharpe']:.3f} trades {om['trades']}")
        print(f"  门禁: {'✅ 通过' if gate_ok else '❌ 未通过'} "
              f"(oos_sharpe {om['sharpe']:.3f} vs 阈值 {tm['sharpe']*0.8:.3f})")

        # 随机基线
        random.seed(42)
        sharpes = []
        for _ in range(100):
            p2 = random.choice((5, 7, 10, 14, 20, 30))
            t2 = random.choice((30.0, 40.0, 50.0, 60.0, 70.0))
            sharpes.append(backtest_rsi(oos, p2, t2)["sharpe"])
        ms = sum(sharpes)/len(sharpes)
        ss = (sum((s-ms)**2 for s in sharpes)/len(sharpes)) ** 0.5
        z = (om["sharpe"] - ms)/ss if ss > 1e-12 else 0
        print(f"  随机基线: sharpe {ms:.3f}±{ss:.3f}, z={z:.2f} ({'显著' if abs(z)>=1.96 else '不显著'})")

    print("\n" + "=" * 72)
    print(f"结论: {'全部通过 OOS 门禁' if all_pass else '存在未通过 OOS 门禁的标的'}")
    print("=" * 72)


if __name__ == "__main__":
    main()
