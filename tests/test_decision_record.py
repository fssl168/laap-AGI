"""P1 决策留痕 + 结果回填 + 教训提炼测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.decision_record import (
    record_decision,
    close_position,
    _derive_lesson,
    _vs_expected,
)
from laap.paper_trading.models import DecisionAction, OutcomeRecord, PaperSignal


@pytest.fixture()
def db(tmp_path):
    return PaperDB(db_path=str(tmp_path / "paper_trading.db"))


@pytest.fixture()
def ledger(db):
    return PaperLedger(db, initial_cash=100_000.0)


def test_record_decision_persists(db):
    rec = record_decision(db, "600519", DecisionAction.BUY,
                          rationale="放量突破", basis_memories=["m1"],
                          risk_note="仓位≤5%", expected="+3%")
    conn = db.conn()
    row = conn.execute("SELECT * FROM decisions WHERE decision_id=?",
                       (rec.decision_id,)).fetchone()
    conn.close()
    assert row["symbol"] == "600519"
    assert row["action"] == "buy"
    assert row["rationale"] == "放量突破"
    assert row["risk_note"] == "仓位≤5%"


def test_close_position_links_decision_id(db, ledger):
    """追溯链闭环：outcome.decision_id == record_decision.decision_id。"""
    rec = record_decision(db, "600519", DecisionAction.BUY, rationale="x")
    sig = PaperSignal(symbol="600519", action=DecisionAction.BUY,
                      quantity=10, trigger_price=100.0)
    order = ledger.submit_signal(sig, client_request_id=rec.decision_id)
    trade = ledger.fill_order(order.id, fill_price=100.0)
    outcome = close_position(db, ledger, trade.id, exit_price=95.0,
                             decision_id=rec.decision_id)
    assert outcome.decision_id == rec.decision_id
    assert outcome.trade_id == trade.id  # PaperTrade.id 保留
    conn = db.conn()
    row = conn.execute("SELECT * FROM outcomes WHERE trade_id=?",
                       (trade.id,)).fetchone()
    conn.close()
    assert row["decision_id"] == rec.decision_id


def test_close_position_derives_lesson(db, ledger):
    """平仓回填 + 教训提炼 + 落 outcomes 表。"""
    sig = PaperSignal(symbol="600519", action=DecisionAction.BUY,
                      quantity=100, trigger_price=100.0)
    order = ledger.submit_signal(sig)
    trade = ledger.fill_order(order.id, fill_price=100.0)
    # 3 天内亏损 → short_term_chase
    outcome = close_position(db, ledger, trade.id, exit_price=95.0, expected="+3%")
    assert outcome.pnl_pct == pytest.approx(-0.05)
    assert outcome.lesson_type == "short_term_chase"
    assert outcome.vs_expected == "missed"
    conn = db.conn()
    row = conn.execute("SELECT * FROM outcomes WHERE trade_id=?",
                       (trade.id,)).fetchone()
    conn.close()
    assert row["lesson_type"] == "short_term_chase"
    assert row["verified"] == 0


def test_derive_lesson_types():
    # 亏损 + 长持仓 → no_stop_loss
    o = OutcomeRecord(trade_id="t1", pnl_pct=-0.1, hold_days=10)
    lesson, lt = _derive_lesson(o)
    assert lt == "no_stop_loss"
    # 盈利 → profitable
    o2 = OutcomeRecord(trade_id="t2", pnl_pct=0.05, hold_days=5)
    _, lt2 = _derive_lesson(o2)
    assert lt2 == "profitable"
    # 持平 → flat
    o3 = OutcomeRecord(trade_id="t3", pnl_pct=0.0, hold_days=1)
    _, lt3 = _derive_lesson(o3)
    assert lt3 == "flat"


def test_vs_expected():
    assert _vs_expected(0.05, "+3%") == "hit"
    assert _vs_expected(-0.05, "+3%") == "missed"
    assert _vs_expected(-0.05, "") == "neutral"
