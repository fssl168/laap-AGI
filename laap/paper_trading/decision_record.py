"""LAAP Paper Trading — 决策留痕 + 结果回填 + 教训提炼（闭环 A 前半）。

流程: record_decision（决策留痕）→ 下单 → 成交 → close_position（平仓回填 + 教训）。
存储: SQLite decisions / outcomes 表（db.py）。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from laap.paper_trading.db import PaperDB
from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.models import (
    DecisionAction,
    DecisionRecord,
    OutcomeRecord,
    PaperTrade,
)

logger = logging.getLogger("laap.paper_trading.decision_record")


def record_decision(db: PaperDB, symbol: str, action: DecisionAction,
                    rationale: str = "", basis_memories: Optional[List[str]] = None,
                    risk_note: str = "", expected: str = "",
                    trade_id: Optional[str] = None) -> DecisionRecord:
    """决策留痕（落 decisions 表）。

    Args:
        trade_id: 决策关联键（可后续下单 client_request_id 复用），缺省自动生成。
    """
    rec = DecisionRecord(
        trade_id=trade_id or "dec_" + uuid.uuid4().hex[:12],
        symbol=symbol, action=action,
        rationale=rationale,
        basis_memories=list(basis_memories or []),
        risk_note=risk_note, expected=expected,
    )
    conn = db.conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO decisions "
            "(trade_id, symbol, action, ts, rationale, basis_memories, risk_note, expected) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rec.trade_id, rec.symbol, rec.action.value, rec.ts, rec.rationale,
             str(rec.basis_memories), rec.risk_note, rec.expected),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"record_decision: {symbol} {action.value} -> {rec.trade_id}")
    return rec


def close_position(db: PaperDB, ledger: PaperLedger, trade_id: str,
                   exit_price: float, expected: str = "") -> OutcomeRecord:
    """平仓 + 回填 outcome + 提炼教训（落 outcomes 表）。

    Args:
        trade_id: PaperTrade.id（平仓目标）
        exit_price: 平仓价
        expected: 决策时的预期（用于 vs_expected 判定），缺省 neutral
    """
    trade: PaperTrade = ledger.close_trade(trade_id, exit_price)
    outcome = OutcomeRecord(
        trade_id=trade_id,
        pnl_pct=trade.pnl_pct or 0.0,
        hold_days=trade.hold_days or 0,
        vs_expected=_vs_expected(trade.pnl_pct or 0.0, expected),
    )
    lesson, lesson_type = _derive_lesson(outcome)
    outcome.lesson = lesson
    outcome.lesson_type = lesson_type

    conn = db.conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO outcomes "
            "(trade_id, pnl_pct, hold_days, vs_expected, lesson, lesson_type, verified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (outcome.trade_id, outcome.pnl_pct, outcome.hold_days,
             outcome.vs_expected, outcome.lesson, outcome.lesson_type,
             int(outcome.verified)),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"close_position: {trade_id} pnl={outcome.pnl_pct:.2%} "
                f"lesson_type={lesson_type}")
    return outcome


def _vs_expected(pnl_pct: float, expected: str) -> str:
    """粗粒度 vs_expected 判定（预期含"+"则期待盈利，含"-"期待亏损）。"""
    if not expected:
        return "neutral"
    if "+" in expected and pnl_pct > 0:
        return "hit"
    if "-" in expected and pnl_pct < 0:
        return "hit"
    return "missed"


def _derive_lesson(outcome: OutcomeRecord) -> Tuple[str, str]:
    """规则化教训提炼（lesson_type 为稳定检索键）。"""
    if outcome.pnl_pct < 0 and outcome.hold_days <= 3:
        return ("短期追高亏损：持仓过短且亏损，需确认入场信号与止损纪律",
                "short_term_chase")
    if outcome.pnl_pct < 0:
        return "持仓亏损且未及时止损：需检查止损规则与离场纪律", "no_stop_loss"
    if outcome.pnl_pct > 0:
        return "盈利交易：入场与退出时机合理，可总结可复用的入场模式", "profitable"
    return "持平交易：无明显盈亏，关注机会成本", "flat"
