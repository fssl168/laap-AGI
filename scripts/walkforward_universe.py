# -*- coding: utf-8 -*-
"""item 1：大样本 walk-forward（real_data/universe/，>=500 天，成本 + 多重检验 + 跨周期）。

用法:
    python scripts/walkforward_universe.py                    # 全部已拉取标的
    python scripts/walkforward_universe.py --n 50 --n-samples 50
    python scripts/walkforward_universe.py --mtc fdr --family long_short
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "real_data" / "universe"


def load_universe(min_days: int = 500, n: int = 0) -> dict:
    out = {}
    for f in sorted(UNIVERSE_DIR.glob("[0-9]*.json")):
        if f.name.startswith("_") or ".ohlcv" in f.name:
            continue
        code = f.stem
        try:
            closes = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if len(closes) < min_days:
            continue
        out[code] = {"closes": closes}
        if n and len(out) >= n:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="大样本 walk-forward 验证")
    ap.add_argument("--n", type=int, default=0, help="标的数上限（0=全部）")
    ap.add_argument("--min-days", type=int, default=500)
    ap.add_argument("--train", type=int, default=400)
    ap.add_argument("--test", type=int, default=80)
    ap.add_argument("--n-samples", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--family", choices=["long_only", "long_short"],
                    default="long_only")
    ap.add_argument("--costs", choices=["none", "ashare"], default="ashare")
    ap.add_argument("--mtc", choices=["none", "bonferroni", "fdr"],
                    default="bonferroni")
    ap.add_argument("--valid-ratio", type=float, default=0.8,
                    help="M1 选择偏差门禁：train 拆 select/verify（选参只用 select，"
                         "须在 verify 段正收益才进 OOS）")
    ap.add_argument("--price-limit", type=float, default=0.10,
                    help="M3 A股涨跌停幅度（0.10=±10%%）；0=关闭")
    ap.add_argument("--style", choices=["trend", "mean_reversion"],
                    default="trend", help="M4 信号家族")
    ap.add_argument("--out", default="real_data/walkforward_universe_report.json")
    args = ap.parse_args()

    from laap.paper_trading.walkforward import WalkForwardValidator

    data = load_universe(min_days=args.min_days, n=args.n)
    if not data:
        print("[FAIL] universe 为空；先运行 scripts/fetch_universe.py")
        return 1
    print(f"大样本 walk-forward：{len(data)} 只标的 × >= {args.min_days} 天 "
          f"| family={args.family} costs={args.costs} mtc={args.mtc}")

    costs = {}
    if args.costs == "ashare":
        costs = {"commission": 0.00025, "stamp": 0.0005, "slippage": 0.001}
    v = WalkForwardValidator()
    report = v.run(
        data, train_size=args.train, test_size=args.test,
        pass_threshold=0.6, mtc=args.mtc,
        method="random", n_samples=args.n_samples, seed=args.seed,
        significance=True, baseline_samples=args.n_samples,
        family=args.family, costs=costs,
        price_limit=args.price_limit or None,
        style=args.style, valid_ratio=args.valid_ratio,
    )
    s = report["summary"]
    print(f"\n总段数: {s['n_folds_total']} | 通过: {s['pass_count']} "
          f"({s['pass_rate']:.1%}) | 中位 z: {s['median_z']}")
    print(f"平均 OOS: {s['mean_oos_return']:.2%} | 正收益段: "
          f"{s['positive_folds']}/{s['n_folds_total']}")
    rs = s.get("regime_stats") or {}
    for cls in ("bull", "range", "bear"):
        r = rs.get(cls)
        if r:
            print(f"  {cls:<6} n={r['n']:>4} pass={r['pass']:>3} "
                  f"rate={r['pass_rate']:.1%} mean_oos={r['mean_oos']:>7.2%} "
                  f"excess={r['mean_excess']:>7.2%}")
    print(f"判定: {s['verdict']} | {s['verdict_reason']}")

    report["config"] = {
        "universe": str(UNIVERSE_DIR), "n_symbols": len(data),
        "min_days": args.min_days, "train": args.train, "test": args.test,
        "n_samples": args.n_samples, "seed": args.seed,
        "family": args.family, "costs": args.costs, "mtc": args.mtc,
        "valid_ratio": args.valid_ratio, "style": args.style,
    }
    out = Path(args.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n报告已保存: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
