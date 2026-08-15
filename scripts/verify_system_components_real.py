# -*- coding: utf-8 -*-
"""方向1+2 最小验证：用系统真实组件在真实800天数据上回测

方向1（策略真实化）: 用 BacktestRunner.run_backtest（均线交叉，即 strategy.py
  STRATEGY_PARAMS 语义）+ compute_trade_fitness + oos_gate 门禁，
  而非此前外挂的 RSI 代理。
方向2（时间稳健化）: walk-forward 滚动切分（多窗口），验证结论跨时段稳定。

结论定位: 仅作探索性验证，如实报告；不宣称"实证通过"。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from laap.paper_trading.backtest_runner import BacktestRunner, split_series
from laap.paper_trading.trade_fitness import compute_trade_fitness

RUNNER = BacktestRunner()
REAL_DIR = Path(r"D:\laap-AGI\real_data")
SYMBOLS = {"600519": "贵州茅台", "000001": "平安银行", "000858": "五粮液"}


def load_closes(sym: str) -> List[float]:
    p = REAL_DIR / f"kline_{sym}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def run_gate(prices: List[float], train_ratio: float = 0.6,
             oos_ratio: float = 0.2) -> Dict[str, Any]:
    """系统真实门禁链: 网格寻优(train) → OOS 门禁(oos_gate)。"""
    n = len(prices)
    dates = list(range(n))
    train_d, valid_d, oos_d = split_series(dates, train_ratio, oos_ratio)
    oos_start = len(train_d) + len(valid_d)

    # 网格寻优 short/long（与 STRATEGY_PARAMS 语义一致）
    best = None
    grid = [(s, l) for s in (3, 5, 7, 10) for l in (15, 20, 25, 30) if s < l]
    for short, long in grid:
        tm = RUNNER.run_backtest(prices, {"short": short, "long": long},
                                 split=(0, len(train_d)))
        if best is None or tm["score"] > best[2]["score"]:
            best = (short, long, tm)
    short, long, train_m = best

    oos_m = RUNNER.run_backtest(prices, {"short": short, "long": long},
                                split=(oos_start, n))
    ok, reason = RUNNER.oos_gate(train_m, oos_m)
    return {
        "params": (short, long),
        "train": train_m,
        "oos": oos_m,
        "gate_ok": ok,
        "gate_reason": reason,
    }


def walk_forward(prices: List[float], window: int = 240, step: int = 60,
                 train_ratio: float = 0.6, oos_ratio: float = 0.2) -> List[Dict[str, Any]]:
    """walk-forward 滚动: 每个窗口独立寻优+门禁。"""
    out = []
    for start in range(0, len(prices) - window, step):
        seg = prices[start:start + window]
        r = run_gate(seg, train_ratio, oos_ratio)
        r["window_start"] = start
        r["window_len"] = window
        out.append(r)
    return out


def main():
    print("=" * 78)
    print("方向1+2 最小验证：系统真实组件 × 真实800天数据")
    print("  组件: BacktestRunner(均线交叉) + compute_trade_fitness + oos_gate")
    print("  门禁: oos_cumret>=0 且 oos_sharpe >= train_sharpe*0.8")
    print("=" * 78)

    results = {}
    for sym, name in SYMBOLS.items():
        closes = load_closes(sym)
        r = run_gate(closes)
        results[sym] = r
        tm, om = r["train"], r["oos"]
        print(f"\n[{sym} {name}] {len(closes)}天")
        print(f"  最优参数 MA({r['params'][0]},{r['params'][1]})")
        print(f"  train: cumret {tm['cumulative_return']:>7.2%} "
              f"sharpe {tm['sharpe_ratio']:.3f} dd {tm['max_drawdown']:.2%}")
        print(f"  oos:   cumret {om['cumulative_return']:>7.2%} "
              f"sharpe {om['sharpe_ratio']:.3f} dd {om['max_drawdown']:.2%}")
        print(f"  门禁:   {'✅ 通过' if r['gate_ok'] else '❌ 未通过'} ({r['gate_reason']})")

        # 方向2: walk-forward
        wf = walk_forward(closes)
        passed = sum(1 for w in wf if w["gate_ok"])
        print(f"  walk-forward: {len(wf)} 窗口, 门禁通过 {passed}/{len(wf)} "
              f"({passed/len(wf):.0%})")
        results[sym]["walk_forward"] = {
            "windows": len(wf), "passed": passed,
            "per_window": [{"params": w["params"], "gate_ok": w["gate_ok"],
                            "oos_cumret": w["oos"]["cumulative_return"],
                            "oos_sharpe": w["oos"]["sharpe_ratio"]} for w in wf],
        }

    # 汇总
    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    for sym, name in SYMBOLS.items():
        r = results[sym]
        wf = r["walk_forward"]
        print(f"  {sym} {name}: 单次门禁 {'通过' if r['gate_ok'] else '未通过'} | "
              f"walk-forward {wf['passed']}/{wf['windows']} 通过")

    out_path = Path(r"D:\laap-AGI\rsi_walkforward_minimal.json")
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
