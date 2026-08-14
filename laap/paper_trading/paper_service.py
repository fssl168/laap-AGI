"""LAAP Paper Trading — 装配层（参考 DSA build_full_listener）。

把 ledger / market / memory / decision_record / memory_bridge 组装成
"决策→下单→成交→平仓→教训沉淀→参与推理"的完整闭环入口。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from laap.paper_trading.db import PaperDB
from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.market_source import MarketSource, resolve_source
from laap.paper_trading.settle import Settlement
from laap.paper_trading.models import DecisionAction, PaperSignal
from laap.paper_trading.decision_record import record_decision, close_position
from laap.paper_trading.memory_bridge import (
    encode_lesson,
    inject_memory_prompt,
    retrieve_for_symbol,
)

logger = logging.getLogger("laap.paper_trading.paper_service")


class PaperClosedLoop:
    """记忆 × 交易闭环装配（统一入口）。"""

    def __init__(self, db: PaperDB, market: MarketSource, memory: Any,
                 initial_cash: float = 1_000_000.0):
        self.db = db
        self.market = market
        self.memory = memory
        self.ledger = PaperLedger(db, initial_cash=initial_cash)
        self.settlement = Settlement()

    def decide_and_trade(self, symbol: str, action: DecisionAction,
                         quantity: int, trigger_price: float,
                         rationale: str = "", expected: str = "",
                         risk_note: str = "") -> Dict[str, Any]:
        """一次完整"决策→下单→成交"（含记忆注入 + 决策留痕）。

        1. 注入记忆 prompt（历史教训）
        2. 决策留痕（decisions 表，basis_memories 关联记忆检索）
        3. 下单（client_request_id = 决策键，幂等；注入记忆到 rationale）
        4. 成交（用 market 实时价）
        """
        # 1. 记忆检索 + 注入
        prompt = inject_memory_prompt(self.memory, symbol, action.value)
        basis = retrieve_for_symbol(self.memory, symbol, max_results=3)
        basis_ids = [str(b.get("id", "")) for b in basis if b.get("id")]

        # 2. 决策留痕
        rec = record_decision(
            self.db, symbol, action,
            rationale=rationale, basis_memories=basis_ids,
            risk_note=risk_note, expected=expected,
        )

        # 3. 下单（client_request_id = 决策键，幂等；注入记忆）
        signal = PaperSignal(symbol=symbol, action=action, quantity=quantity,
                             trigger_price=trigger_price, rationale=rationale)
        order = self.ledger.submit_signal(
            signal, client_request_id=rec.trade_id, memory=self.memory)

        # 4. 成交
        price, _meta = self.market.get_price(symbol)
        trade = self.ledger.fill_order(order.id, fill_price=price)

        return {
            "decision": rec.to_dict(),
            "order_id": order.id,
            "trade_id": trade.id,
            "fill_price": price,
            "memory_prompt": prompt,
        }

    def close_and_learn(self, trade_id: str, symbol: str,
                        exit_price: float, expected: str = "") -> Dict[str, Any]:
        """平仓 + 教训沉淀（outcomes 表 + UnifiedMemory）。"""
        outcome = close_position(self.db, self.ledger, trade_id, exit_price,
                                 expected=expected)
        episode_id = encode_lesson(self.memory, outcome, symbol=symbol)
        return {"outcome": outcome.to_dict(), "episode_id": episode_id}

    def settle(self) -> Dict[str, Any]:
        """日终结算，落净值快照。"""
        nv = self.settlement.daily_settle(self.ledger, self.market)
        return nv.to_dict()

    def stats(self) -> Dict[str, Any]:
        return {"ledger": self.ledger.stats()}


def build_paper_closed_loop(repo_root: str = "", market: Optional[MarketSource] = None,
                            memory: Any = None,
                            initial_cash: float = 1_000_000.0) -> PaperClosedLoop:
    """统一装配（参考 DSA build_full_listener）。

    Args:
        repo_root: 项目根（PaperDB 路径基准）
        market: 行情源（缺省 resolve_source 真实源优先 + Stub 降级）
        memory: UnifiedMemory（缺省新建）
    """
    from pathlib import Path
    root = repo_root or ""
    db_path = str(Path(root) / "data" / "paper_trading.db") if root else None
    db = PaperDB(db_path=db_path)
    market = market or resolve_source(prefer_live=True)
    if memory is None:
        from laap.agi.unified_memory import UnifiedMemory
        memory = UnifiedMemory()
    loop = PaperClosedLoop(db=db, market=market, memory=memory,
                           initial_cash=initial_cash)
    logger.info("PaperClosedLoop built (market=%s, memory=%s)",
                type(market).__name__, type(memory).__name__)
    return loop
