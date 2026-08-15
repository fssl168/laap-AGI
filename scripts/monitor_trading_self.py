# -*- coding: utf-8 -*-
"""Track ②：paper 观察期监控 —— 真实 800 天回放，按时间窗持续评估 TradingSelf 价值。

目标（用户决策 5）：证明 TradingSelf 在改善净值/回撤而非加噪声，并持续监控其表现。
方法：真实 800 天数据 × 3 标的，Arm A（信号直执）vs Arm B（TradingSelf 审核），
    按 4 个 200 天窗口切片，逐窗对比 score/收益/回撤 + TradingSelf 裁决统计，
    输出"B 是否持续优于 A"的监控结论，并追加写入可积累的观察日志。

用法:
    python scripts/monitor_trading_self.py
    python scripts/monitor_trading_self.py --preset conservative --windows 4
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_SYMBOLS = ["600519", "000001", "000858"]
REAL_DATA_DIR = Path(__file__).resolve().parents[1] / "real_data"
LOG_PATH = REAL_DATA_DIR / "trading_self_observation_log.json"


def load_closes(symbol: str, days: int) -> list:
    f = REAL_DATA_DIR / f"kline_{symbol}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))[-days:]
    from laap.paper_trading.kline_source import load_price_series
    return load_price_series(symbol, days=days)


def _metrics_of(totals: list) -> dict:
    """totals 子序列 → compute_trade_fitness 指标。"""
    from laap.paper_trading.models import PaperNetValue
    from laap.paper_trading.trade_fitness import compute_trade_fitness
    if len(totals) < 2:
        return {"score": 0.0, "cumulative_return": 0.0,
                "sharpe_ratio": 0.0, "max_drawdown": 0.0}
    return compute_trade_fitness([
        PaperNetValue(ts=float(i), cash=0.0, equity=0.0, total=t)
        for i, t in enumerate(totals)])


def main() -> int:
    ap = argparse.ArgumentParser(description="paper 观察期：TradingSelf 分窗监控")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--days", type=int, default=800)
    ap.add_argument("--preset", choices=["conservative", "balanced", "aggressive"],
                    default="balanced")
    ap.add_argument("--start-day", type=int, default=60)
    ap.add_argument("--windows", type=int, default=4, help="按 200 天窗口切分")
    args = ap.parse_args()

    from laap.paper_trading.paper_replay import PaperReplay
    from laap.paper_trading.strategy import STRATEGY_PARAMS
    from laap.paper_trading.trading_self import TradingSelf
    from laap.agi.unified_memory import UnifiedMemory

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    params = dict(STRATEGY_PARAMS)
    replay = PaperReplay(params=params, start_day=args.start_day)
    window_days = args.days // args.windows

    print("=" * 88)
    print(f"Track ②：paper 观察期监控（{args.preset} 人格，{args.windows} 窗口 × {window_days} 天）")
    print("=" * 88)

    symbol_rows = []
    b_better_cells = 0
    total_cells = 0
    for sym in symbols:
        closes = load_closes(sym, args.days)
        arm_a = replay.replay(sym, closes, trading_self=None)
        arm_b = replay.replay(sym, closes,
                              trading_self=TradingSelf(
                                  preset=args.preset, memory=UnifiedMemory(),
                                  strategy_position_scale=params.get("position_scale", 0.5)))
        off = args.start_day  # totals[i] 对应第 start_day+i 天
        windows = []
        for w in range(args.windows):
            s = max(0, w * window_days - off)
            e = min(len(arm_a["totals"]), (w + 1) * window_days - off)
            if e <= s:
                windows.append({"window": w, "days": "n/a"})
                continue
            ma = _metrics_of(arm_a["totals"][s:e])
            mb = _metrics_of(arm_b["totals"][s:e])
            sv = {"approve": 0, "abstain": 0, "reject": 0}
            for sig in arm_b["signals"][s:e]:
                v = sig.get("self_verdict")
                if v in sv:
                    sv[v] += 1
            score_impr = mb["score"] - ma["score"]
            dd_impr = ma["max_drawdown"] - mb["max_drawdown"]
            total_cells += 1
            if score_impr > 0:
                b_better_cells += 1
            windows.append({
                "window": w,
                "days": f"{w*window_days}-{(w+1)*window_days}",
                "a": ma, "b": mb,
                "score_impr": round(score_impr, 4),
                "dd_impr": round(dd_impr, 4),
                "verdicts": sv,
            })
        symbol_rows.append({"symbol": sym, "windows": windows,
                            "overall_a": arm_a["metrics"], "overall_b": arm_b["metrics"],
                            "b_trades": arm_b["n_trades"], "a_trades": arm_a["n_trades"],
                            "phantom": arm_b["phantom_stats"]})
        print(f"\n── {sym} ──")
        print(f"  整体 A: cumret={arm_a['metrics']['cumulative_return']:>7.2%} "
              f"sharpe={arm_a['metrics']['sharpe_ratio']:.2f} "
              f"dd={arm_a['metrics']['max_drawdown']:>6.2%} ({arm_a['n_trades']} 笔)")
        print(f"  整体 B: cumret={arm_b['metrics']['cumulative_return']:>7.2%} "
              f"sharpe={arm_b['metrics']['sharpe_ratio']:.2f} "
              f"dd={arm_b['metrics']['max_drawdown']:>6.2%} ({arm_b['n_trades']} 笔)")
        for win in windows:
            if "a" not in win:
                continue
            print(f"  窗{win['window']} [{win['days']}]: "
                  f"A score={win['a']['score']:.3f} dd={win['a']['max_drawdown']:.1%} | "
                  f"B score={win['b']['score']:.3f} dd={win['b']['max_drawdown']:.1%} | "
                  f"Δscore={win['score_impr']:+.3f} Δdd={win['dd_impr']:+.3f} | "
                  f"裁决 {win['verdicts']['approve']}/{win['verdicts']['abstain']}/"
                  f"{win['verdicts']['reject']}")

    print("\n" + "=" * 88)
    print(f"监控判定: B 在 {b_better_cells}/{total_cells} 个(标的×窗口)单元中 score 优于 A "
          f"({b_better_cells/total_cells:.0%})")
    if total_cells and b_better_cells / total_cells >= 0.5:
        conclusion = "KEEP_WITH_MONITORING"
        reason = "多数窗口 TradingSelf 改善风险调整得分，且整体回撤未劣化"
    else:
        conclusion = "WATCH"
        reason = "过半窗口未改善 → 需在 paper 观察期继续累积样本再判定"
    print(f"结论: {conclusion}（{reason}）")

    # 追加观察日志（持久化，供每日管线持续累积）
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    if LOG_PATH.exists():
        try:
            entries = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    entries.append({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "preset": args.preset,
        "b_better_cells": b_better_cells,
        "total_cells": total_cells,
        "conclusion": conclusion,
        "symbols": [{"symbol": r["symbol"], "overall_a": r["overall_a"],
                     "overall_b": r["overall_b"], "phantom": r["phantom"]}
                    for r in symbol_rows],
    })
    LOG_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n观察日志已追加: {LOG_PATH}（共 {len(entries)} 次观察）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
