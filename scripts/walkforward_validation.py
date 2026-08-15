# -*- coding: utf-8 -*-
"""Step 1：滚动 Walk-Forward 验证（真实数据，决策 1/2）。

用真实 800 天 K 线（real_data/kline_*.json，qfq 前复权，close only）
对 14 维多因子策略做滚动 walk-forward（默认 5 段：train=400 / test=80），
门禁 = 正收益 + 显著优于随机基线（z >= 1.96），产出诚实结论。

用法:
    python scripts/walkforward_validation.py
    python scripts/walkforward_validation.py --method genetic --n-samples 200
    python scripts/walkforward_validation.py --train 300 --test 60
    python scripts/walkforward_validation.py --no-significance
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_SYMBOLS = ["600519", "000001", "000858"]  # 自选股（800 天真实数据）
REAL_DATA_DIR = Path(__file__).resolve().parents[1] / "real_data"


def load_symbol_data(symbol: str, days: int) -> dict:
    """加载标的 close 序列：real_data/kline_<symbol>.json 优先，kline.db 兜底。"""
    f = REAL_DATA_DIR / f"kline_{symbol}.json"
    if f.exists():
        closes = json.loads(f.read_text(encoding="utf-8"))
    else:
        from laap.paper_trading.kline_source import load_price_series
        closes = load_price_series(symbol, days=days)
    return {"closes": closes[-days:]}


def main() -> int:
    ap = argparse.ArgumentParser(description="滚动 Walk-Forward 验证（真实数据）")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                    help="标的池（逗号分隔）")
    ap.add_argument("--days", type=int, default=800, help="使用最近 N 天")
    ap.add_argument("--train", type=int, default=400, help="train 窗口（天）")
    ap.add_argument("--test", type=int, default=80, help="test 窗（天），步长=test")
    ap.add_argument("--method", choices=["random", "genetic"], default="random")
    ap.add_argument("--variant", choices=["none", "regime60", "regime120"],
                    default="none",
                    help="策略变体（Track ①）：none=原策略 / regime60,120=趋势过滤（站上长期均线才交易）")
    ap.add_argument("--family", choices=["long_only", "long_short"],
                    default="long_only",
                    help="策略族（Track ①）：long_only=长期做多 / long_short=多空（空头在下跌段赚钱）")
    ap.add_argument("--costs", choices=["none", "ashare"], default="none",
                    help="交易成本（item 2）：ashare=佣金0.025%%+印花税0.05%%+滑点0.1%% / none=零成本")
    ap.add_argument("--mtc", choices=["none", "bonferroni", "fdr"], default="none",
                    help="多重检验控制（item 1）：bonferroni / fdr(BH q=0.05)")
    ap.add_argument("--valid-ratio", type=float, default=0.8,
                    help="M1 选择偏差门禁：train 拆 select/verify 比例（0.8=选参用前80%%，"
                         "选出的参数须在未参与选参的 verify 段正收益才进 OOS）")
    ap.add_argument("--price-limit", type=float, default=0.10,
                    help="M3 A股涨跌停幅度（0.10=±10%%：涨停禁买/跌停禁卖）；0=关闭")
    ap.add_argument("--style", choices=["trend", "mean_reversion"], default="trend",
                    help="M4 信号家族：trend（趋势）/ mean_reversion（均值回归，独立家族）")
    ap.add_argument("--n-samples", type=int, default=100, help="随机搜索采样数")
    ap.add_argument("--population", type=int, default=16, help="遗传种群（genetic）")
    ap.add_argument("--generations", type=int, default=10, help="遗传代数（genetic）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--baseline-samples", type=int, default=100, help="随机基线采样数")
    ap.add_argument("--no-significance", dest="significance", action="store_false",
                    help="关闭显著性层（只用 正收益+夏普 门禁）")
    ap.add_argument("--pass-threshold", type=float, default=0.6,
                    help="STABLE_PASS 通过率阈值")
    ap.add_argument("--out", default="real_data/walkforward_report.json")
    args = ap.parse_args()

    from laap.paper_trading.walkforward import WalkForwardValidator

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    data = {s: load_symbol_data(s, args.days) for s in symbols}

    print("=" * 76)
    print("Step 1：滚动 Walk-Forward 验证（真实数据）")
    print("=" * 76)
    print(f"标的: {symbols} | 数据: 最近 {args.days} 天 (real_data kline_*.json, qfq)")
    print(f"滚动: train={args.train} test={args.test} "
          f"→ {max(0, (args.days - args.train) // args.test)} 段")
    print(f"搜索: {args.method} (n_samples={args.n_samples}, seed={args.seed})")
    print(f"变体: {args.variant} "
          f"({'趋势过滤 regime_ma=' + str({'none': None, 'regime60': 60, 'regime120': 120}[args.variant]) if args.variant != 'none' else '原策略（无过滤）'})")
    print(f"策略族: {args.family} "
          f"({'多空（空头捕捉下跌收益）' if args.family == 'long_short' else '长期做多'})")
    print(f"成本: {args.costs} "
          f"({'佣金0.025%+印花税0.05%+滑点0.1%（A股）' if args.costs == 'ashare' else '零成本'})"
          f" | 多重检验: {args.mtc}")
    print(f"门禁: 正收益 + 显著优于随机基线 z>={1.96} "
          f"(significance={args.significance}, baseline_samples={args.baseline_samples})")
    print(f"判定: STABLE_PASS 需通过率 >= {args.pass_threshold:.0%} 且 中位 z >= 1.96")
    print()

    costs = {}
    if args.costs == "ashare":
        costs = {"commission": 0.00025, "stamp": 0.0005, "slippage": 0.001}
    v = WalkForwardValidator()
    report = v.run(
        data,
        train_size=args.train, test_size=args.test,
        pass_threshold=args.pass_threshold,
        method=args.method, n_samples=args.n_samples, seed=args.seed,
        significance=args.significance,
        baseline_samples=args.baseline_samples,
        population=args.population, generations=args.generations,
        variant=args.variant, family=args.family,
        costs=costs, mtc=args.mtc,
        price_limit=args.price_limit or None,
        style=args.style,
        valid_ratio=args.valid_ratio,
    )

    # ── 逐标的 × 逐段表 ──
    for sym in report["symbols"]:
        print(f"── {sym['symbol']} ({sym['n']} 天, {sym['n_folds']} 段) ──")
        for r in sym["folds"]:
            tm = r["train_metrics"]
            xm = r["test_metrics"]
            zs = f"z={r['z']:.2f}" if r.get("z") is not None else "z=n/a"
            mark = "PASS" if r.get("ok") else "FAIL"
            print(f"  seg{r['fold_index']}: {r.get('window','')}"
                  f" train_score={tm['score']:.3f} train_sharpe={tm['sharpe_ratio']:.2f}"
                  f" | OOS cumret={xm['cumulative_return']:>8.2%} sharpe={xm['sharpe_ratio']:.2f}"
                  f" | bh={r['buy_hold']:>7.2%} excess={r['excess']:>7.2%} {zs}"
                  f" [{mark}]")

    s = report["summary"]
    print()
    print("=" * 76)
    print("汇总（诚实结论，不宣称实证通过）")
    print("=" * 76)
    print(f"总段数: {s['n_folds_total']} (标的 {s['n_symbols']})")
    print(f"通过门禁: {s['pass_count']}/{s['n_folds_total']} ({s['pass_rate']:.0%})")
    print(f"OOS 正收益段: {s['positive_folds']}/{s['n_folds_total']}")
    print(f"跑赢买入持有段: {s['beat_buy_hold_folds']}/{s['n_folds_total']}")
    print(f"平均 OOS 收益: {s['mean_oos_return']:.2%} | 中位: {s['median_oos_return']:.2%}")
    print(f"平均超额收益: {s['mean_excess']:.2%} | 中位 z: {s['median_z']}")
    # item 3：跨周期分段
    rs = s.get("regime_stats") or {}
    if rs:
        print("跨周期分段（牛/熊/震荡，按 OOS 窗买入持有 ±5% 分类）:")
        for cls in ("bull", "range", "bear"):
            r = rs.get(cls)
            if r:
                print(f"  {cls:<6} n={r['n']:>3} pass={r['pass']:>2} "
                      f"rate={r['pass_rate']:.0%} mean_oos={r['mean_oos']:>7.2%} "
                      f"excess={r['mean_excess']:>7.2%}")
    print(f"判定: {s['verdict']}")
    print(f"  依据: {s['verdict_reason']}")

    report["config"] = {
        "data_source": "real_data/kline_*.json (qfq, close only)",
        "days": args.days, "train": args.train, "test": args.test,
        "method": args.method, "n_samples": args.n_samples, "seed": args.seed,
        "variant": args.variant, "family": args.family,
        "costs": args.costs, "mtc": args.mtc,
        "valid_ratio": args.valid_ratio, "price_limit": args.price_limit,
        "style": args.style,
        "significance": args.significance,
        "baseline_samples": args.baseline_samples,
        "pass_threshold": args.pass_threshold,
        "z_threshold": 1.96,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n报告已保存: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
