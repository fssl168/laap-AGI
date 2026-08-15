# -*- coding: utf-8 -*-
"""Step 2：TradingSelf A/B 对照实验（用户决策：最重要，不可省）。

同一数据 × 同一参数 × 同一信号引擎，逐日回放两条臂：
  Arm A（对照）：策略信号直接执行（无 TradingSelf）
  Arm B（处理）：信号经 TradingSelf.judge 审核，approve 才执行
比较净值/夏普/回撤/交易数 + TradingSelf 裁决分布 + 被拒交易的"幽灵仓"反事实。

判定规则（用户决策）：
  KEEP      — Arm B 风险调整得分明显更好（score 提升 > 0.02）且回撤不劣于 Arm A
  BORDERLINE— 有改善但不显著
  DOWNGRADE — 无改善或更差 → TradingSelf 降级为纯记录层（只写 [self]/[benefit] 留痕），
              硬风控单独抽离保持 fail-closed

用法:
    python scripts/run_self_review_ab.py
    python scripts/run_self_review_ab.py --preset conservative
    python scripts/run_self_review_ab.py --symbols 600519
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_SYMBOLS = ["600519", "000001", "000858"]
REAL_DATA_DIR = Path(__file__).resolve().parents[1] / "real_data"

# 判定阈值（决策：score 提升 > 0.02 且回撤不劣化 → KEEP）
KEEP_SCORE_IMPROVEMENT = 0.02


def load_closes(symbol: str, days: int) -> list:
    f = REAL_DATA_DIR / f"kline_{symbol}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))[-days:]
    from laap.paper_trading.kline_source import load_price_series
    return load_price_series(symbol, days=days)


def main() -> int:
    ap = argparse.ArgumentParser(description="TradingSelf A/B 对照实验")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--days", type=int, default=800)
    ap.add_argument("--preset", choices=["conservative", "balanced", "aggressive"],
                    default="balanced", help="TradingSelf 人格预设（决策 3）")
    ap.add_argument("--start-day", type=int, default=60, help="指标预热后开始交易")
    ap.add_argument("--out", default="real_data/self_review_ab_report.json")
    args = ap.parse_args()

    from laap.paper_trading.paper_replay import PaperReplay
    from laap.paper_trading.strategy import STRATEGY_PARAMS
    from laap.paper_trading.trading_self import TradingSelf
    from laap.agi.unified_memory import UnifiedMemory

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    params = dict(STRATEGY_PARAMS)
    replay = PaperReplay(params=params, start_day=args.start_day)

    print("=" * 84)
    print(f"Step 2：TradingSelf A/B 对照实验（{args.preset} 人格）")
    print("=" * 84)
    print(f"标的: {symbols} | 数据: 最近 {args.days} 天 (real_data, qfq) | 参数: STRATEGY_PARAMS")
    print(f"Arm A: 信号直接执行 | Arm B: TradingSelf 审核后执行")
    print()

    rows = []
    for sym in symbols:
        closes = load_closes(sym, args.days)
        arm_a = replay.replay(sym, closes, trading_self=None)
        ts = TradingSelf(preset=args.preset, memory=UnifiedMemory(),
                         strategy_position_scale=params.get("position_scale", 0.5))
        arm_b = replay.replay(sym, closes, trading_self=ts)

        a, b = arm_a["metrics"], arm_b["metrics"]
        score_impr = b["score"] - a["score"]
        dd_impr = a["max_drawdown"] - b["max_drawdown"]  # >0 → B 回撤更小
        rows.append({
            "symbol": sym,
            "arm_a": a, "arm_b": b,
            "a_trades": arm_a["n_trades"], "b_trades": arm_b["n_trades"],
            "score_impr": round(score_impr, 4),
            "dd_impr": round(dd_impr, 4),
            "verdicts": arm_b["verdicts"],
            "phantom": arm_b["phantom_stats"],
        })
        print(f"── {sym} ──")
        print(f"  A: cumret={a['cumulative_return']:>8.2%} sharpe={a['sharpe_ratio']:.2f} "
              f"max_dd={a['max_drawdown']:>7.2%} trades={arm_a['n_trades']}")
        print(f"  B: cumret={b['cumulative_return']:>8.2%} sharpe={b['sharpe_ratio']:.2f} "
              f"max_dd={b['max_drawdown']:>7.2%} trades={arm_b['n_trades']}")
        print(f"     裁决 approve/abstain/reject = "
              f"{arm_b['verdicts']['approve']}/{arm_b['verdicts']['abstain']}/"
              f"{arm_b['verdicts']['reject']}"
              f" | scoreΔ={score_impr:+.3f} ddΔ={dd_impr:+.3f}")
        ph = arm_b["phantom_stats"]
        if ph["count"]:
            print(f"     幽灵仓(被拒/弃权): {ph['count']} 笔 总盈亏 {ph['total_pnl']:,.0f} "
                  f"胜率 {ph['win_rate']:.0%} 均收益 {ph['avg_pnl_pct']:.2%}"
                  if ph["win_rate"] is not None else
                  f"     幽灵仓(被拒/弃权): {ph['count']} 笔 总盈亏 {ph['total_pnl']:,.0f}")
        else:
            print("     幽灵仓: 无（TradingSelf 未拦下任何买入）")
        print()

    # ── 汇总判定 ──
    n = len(rows)
    avg_score_impr = statistics.mean(r["score_impr"] for r in rows)
    avg_dd_impr = statistics.mean(r["dd_impr"] for r in rows)
    improved_symbols = sum(1 for r in rows if r["score_impr"] > 0)
    dd_improved = sum(1 for r in rows if r["dd_impr"] > 0)
    phantom_total = sum(r["phantom"]["total_pnl"] for r in rows)
    phantom_n = sum(r["phantom"]["count"] for r in rows)

    print("=" * 84)
    print("汇总判定")
    print("=" * 84)
    print(f"score 平均变化: {avg_score_impr:+.3f} (改善标的 {improved_symbols}/{n})")
    print(f"回撤平均变化(A-B): {avg_dd_impr:+.3f} (回撤更小标的 {dd_improved}/{n})")
    print(f"被拒/弃权买入反事实: {phantom_n} 笔，总盈亏 {phantom_total:,.0f}")

    if avg_score_impr > KEEP_SCORE_IMPROVEMENT and avg_dd_impr >= 0:
        verdict = "KEEP"
        reason = (f"Arm B 风险调整得分提升 {avg_score_impr:+.3f} > "
                  f"{KEEP_SCORE_IMPROVEMENT} 且回撤未劣化（Δdd={avg_dd_impr:+.3f}）")
    elif avg_score_impr > 0:
        verdict = "BORDERLINE"
        reason = (f"Arm B 有改善（scoreΔ={avg_score_impr:+.3f}）但不达 "
                  f"{KEEP_SCORE_IMPROVEMENT} 阈值，建议加大样本复测")
    else:
        verdict = "DOWNGRADE"
        reason = (f"Arm B 无改善（scoreΔ={avg_score_impr:+.3f}）或更差"
                  f" → TradingSelf 降级为纯记录层，硬风控单独抽离保持 fail-closed")
    print(f"判定: {verdict}")
    print(f"  依据: {reason}")

    report = {
        "preset": args.preset,
        "params": params,
        "verdict": verdict,
        "verdict_reason": reason,
        "summary": {
            "avg_score_impr": round(avg_score_impr, 4),
            "avg_dd_impr": round(avg_dd_impr, 4),
            "improved_symbols": improved_symbols,
            "dd_improved_symbols": dd_improved,
            "phantom_total_pnl": phantom_total,
            "phantom_count": phantom_n,
            "keep_threshold": KEEP_SCORE_IMPROVEMENT,
        },
        "symbols": rows,
    }
    out = Path(args.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已保存: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
