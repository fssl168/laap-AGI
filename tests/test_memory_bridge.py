"""P1 记忆桥接测试（沉淀 / 检索 / 注入 / 校验）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.decision_record import close_position
from laap.paper_trading.memory_bridge import (
    lesson_to_experience,
    encode_lesson,
    retrieve_for_symbol,
    inject_memory_prompt,
    verify_lessons,
)
from laap.paper_trading.models import DecisionAction, OutcomeRecord, PaperSignal


@pytest.fixture()
def db(tmp_path):
    return PaperDB(db_path=str(tmp_path / "paper_trading.db"))


@pytest.fixture()
def ledger(db):
    return PaperLedger(db, initial_cash=100_000.0, enforce_t1=False)


@pytest.fixture()
def memory():
    from laap.agi.unified_memory import UnifiedMemory
    return UnifiedMemory()


def _outcome(trade_id="t1", pnl_pct=-0.05, hold_days=2, lesson_type="short_term_chase") -> OutcomeRecord:
    return OutcomeRecord(trade_id=trade_id, pnl_pct=pnl_pct, hold_days=hold_days,
                         vs_expected="missed", lesson="追高亏损", lesson_type=lesson_type)


def test_lesson_to_experience_contains_key():
    txt = lesson_to_experience(_outcome(), symbol="600519")
    assert "short_term_chase" in txt
    assert "600519" in txt
    assert "pnl" in txt


def test_encode_lesson_returns_episode_id(memory):
    ep = encode_lesson(memory, _outcome(), symbol="600519")
    assert ep  # 非空 episode_id
    # 可检索到
    results = retrieve_for_symbol(memory, "600519")
    assert any("short_term_chase" in str(r.get("content", "")) for r in results)


def test_retrieve_for_symbol_empty_when_no_memory(memory):
    assert retrieve_for_symbol(memory, "nonexistent") == []


def test_inject_memory_prompt(memory):
    encode_lesson(memory, _outcome(), symbol="600519")
    prompt = inject_memory_prompt(memory, "600519", "buy")
    assert "600519" in prompt or "Relevant" in prompt


def test_verify_lessons_threshold(db, ledger):
    """同 lesson_type 累计 >= min_confirm 才 verified=True。"""
    # 只 1 笔 → 未达阈值
    sig = PaperSignal(symbol="600519", action=DecisionAction.BUY,
                      quantity=10, trigger_price=100.0)
    order = ledger.submit_signal(sig)
    trade = ledger.fill_order(order.id, fill_price=100.0)
    close_position(db, ledger, trade.id, exit_price=95.0)  # short_term_chase

    r = verify_lessons(db, "short_term_chase", min_confirm=2)
    assert r["count"] == 1
    assert r["verified"] is False

    # 第 2 笔 → 达阈值
    sig2 = PaperSignal(symbol="600519", action=DecisionAction.BUY,
                       quantity=10, trigger_price=100.0)
    order2 = ledger.submit_signal(sig2)
    trade2 = ledger.fill_order(order2.id, fill_price=100.0)
    close_position(db, ledger, trade2.id, exit_price=95.0)

    r2 = verify_lessons(db, "short_term_chase", min_confirm=2)
    assert r2["count"] == 2
    assert r2["verified"] is True


def test_memory_injection_in_submit_signal(db, ledger, memory):
    """下单时注入记忆 prompt 到 rationale（A-2 参与推理）。"""
    encode_lesson(memory, _outcome(), symbol="600519")
    sig = PaperSignal(symbol="600519", action=DecisionAction.BUY,
                      quantity=10, trigger_price=100.0, rationale="测试买入")
    ledger.submit_signal(sig, memory=memory)
    conn = db.conn()
    row = conn.execute("SELECT rationale FROM signals WHERE symbol='600519'").fetchone()
    conn.close()
    assert "[memory]" in row["rationale"]
