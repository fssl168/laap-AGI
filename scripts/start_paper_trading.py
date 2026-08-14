"""启动 paper_trading：初始化数据库 + 跑一次闭环演示 + 提示 API 服务。

用法:
    python scripts/start_paper_trading.py                 # 默认落 D:\\laap-AGI\\data\\paper_trading.db
    python scripts/start_paper_trading.py --db <path>     # 指定数据库路径
    python scripts/start_paper_trading.py --no-demo       # 仅初始化建库，不跑演示交易

启动后 API 服务:
    python -m laap_brain.api                              # 端口 11546，路由 /v1/quant/*
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _init_db(db_path: str | None):
    from laap.paper_trading.db import PaperDB, _default_db_path
    path = db_path or _default_db_path()
    db = PaperDB(db_path=path)
    print(f"[OK] 数据库已初始化: {path}")
    return db


def _run_demo(db):
    from laap.agi.unified_memory import UnifiedMemory
    from laap.paper_trading.market_source import resolve_source
    from laap.paper_trading.paper_service import PaperClosedLoop
    from laap.paper_trading.models import DecisionAction

    market = resolve_source(prefer_live=True)  # 真实源优先 + Stub 降级
    memory = UnifiedMemory()
    loop = PaperClosedLoop(db=db, market=market, memory=memory,
                           initial_cash=1_000_000.0, enforce_t1=False)
    print(f"[OK] 行情源: {type(market).__name__}")

    # 演示交易：买入 → 平仓 → 教训沉淀
    r1 = loop.decide_and_trade(
        "600519", DecisionAction.BUY, 100, 1355.0,
        rationale="启动演示", expected="+1%")
    print(f"[OK] 买入: decision={r1['decision_id']} trade={r1['trade_id']} "
          f"price={r1['fill_price']}")

    r2 = loop.close_and_learn(r1["trade_id"], "600519", exit_price=r1["fill_price"],
                              decision_id=r1["decision_id"])
    print(f"[OK] 平仓+教训沉淀: lesson_type={r2['outcome']['lesson_type']} "
          f"episode={r2['episode_id']}")

    print(f"[OK] 账本: {loop.ledger.stats()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 paper_trading")
    parser.add_argument("--db", help="数据库路径（默认项目根 data/paper_trading.db）")
    parser.add_argument("--no-demo", action="store_true", help="仅建库，不跑演示交易")
    args = parser.parse_args()

    try:
        db = _init_db(args.db)
    except Exception as e:
        print(f"[FAIL] 建库失败: {e}")
        print("  (沙箱/挂载盘环境请用 --db /tmp/pt.db；Windows 正常环境无需此参数)")
        return 1

    if not args.no_demo:
        try:
            _run_demo(db)
        except Exception as e:
            print(f"[FAIL] 演示交易失败: {e}")
            return 1

    print("\npaper_trading 启动成功。")
    print("启动 API 服务 (路由 /v1/quant/decisions|lessons|evolve|evolve/approve|reject|audit):")
    print("  python -m laap_brain.api")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
