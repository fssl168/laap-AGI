"""运行量化每日管线（真实数据端到端，M4）。

流程（对齐 QuantDailyPipeline）：参数搜索 → 代码落回（M4 治理 + 交易自我审核）
→ 日终执行（真实 kline.db 数据）。全程 paper，不碰真实资金。

用法:
    python scripts/run_quant_daily_pipeline.py
    python scripts/run_quant_daily_pipeline.py --symbols 600519,000001
    python scripts/run_quant_daily_pipeline.py --method genetic --n-samples 200
    python scripts/run_quant_daily_pipeline.py --db /tmp/pt.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description="运行量化每日管线")
    parser.add_argument("--symbols", default="600519,000001,000858",
                        help="标的池（逗号分隔）")
    parser.add_argument("--method", default="random",
                        choices=["random", "grid", "genetic"])
    parser.add_argument("--n-samples", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--self-review", dest="self_review", action="store_true",
                        default=True, help="启用交易自我审核（默认 true）")
    parser.add_argument("--db", default=None, help="paper_trading.db 路径（默认项目根 data/）")
    args = parser.parse_args()

    try:
        from laap.paper_trading.daily_pipeline import QuantDailyPipeline
        from laap.paper_trading.quant_evolution import QuantEvolutionEngine
        from laap.paper_trading.backtest_runner import BacktestRunner
        from laap.paper_trading.kline_source import load_price_series
        from laap.paper_trading.trading_self import TradingSelf
        from laap.paper_trading.paper_service import build_paper_closed_loop
        from laap.agi.unified_memory import UnifiedMemory
        from laap.agi.self_model import create_self_model
        from laap.agi.code_evolution import CodeEvolutionEngine
    except ImportError as e:
        print(f"[FAIL] 依赖缺失: {e}")
        return 1

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    repo_root = str(Path(__file__).resolve().parents[1])

    # 真实组件
    price_series = load_price_series(symbol=symbols[0], days=200)  # 真实 kline.db 优先
    runner = BacktestRunner()
    ts = TradingSelf(memory=UnifiedMemory(), self_model=create_self_model("quant", "TradingSelf"))
    engine = CodeEvolutionEngine(repo_root=repo_root)
    qe = QuantEvolutionEngine(engine, runner, price_series,
                              db=None, trading_self=ts).attach()
    loop = build_paper_closed_loop(repo_root=repo_root, memory=UnifiedMemory(),
                                   trading_self=ts)
    pipe = QuantDailyPipeline(qe, loop, symbols=symbols)

    print(f"[OK] 每日管线: symbols={symbols} method={args.method} "
          f"n_samples={args.n_samples} seed={args.seed} "
          f"self_review={args.self_review}")
    result = pipe.run(method=args.method, n_samples=args.n_samples,
                      seed=args.seed, self_review=args.self_review)
    s = result["summary"]
    print(f"[OK] 参数来源: {s['params_source']} | 应用状态: {s['apply_status']}")
    print(f"[OK] 信号: {s['signals']}")
    print(f"[OK] 净值: {s['net_value']}")
    print(f"[OK] 数据质量: {s['data_quality']}")
    print("daily_cycle 完成（全程 paper，无真实资金）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
