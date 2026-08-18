"""
paper_trading 接入 Aris Phase 1 测试 (方案 v2.0 §4.2)
=====================================================
覆盖 Phase 1 三部分:
  1. pt_* 只读工具扩展 (lessons/signals/net_value/risk_events) + 规则触发
  2. 认知总线 QUANT_* 事件 (5 枚举 + sense_event + 事件聚合)
  3. 教训双写 (UnifiedMemory 主写 + 语义记忆从写, dedup 幂等, 失败降级)

运行:
    python -m pytest tests/test_quant_bridge_phase1.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from aris_brain.rules_defs import DEFAULT_RULES, ToolRegistry
from aris_brain.rules_tools import register_default_tools


@pytest.fixture(scope="module")
def registry():
    reg = ToolRegistry()
    register_default_tools(reg)
    return reg


# ════════════════════════════════════════════════════════════
# 1. pt_* 只读工具扩展 + 规则触发
# ════════════════════════════════════════════════════════════

def test_phase1_tools_registered(registry):
    for name in ("pt_lessons", "pt_signals", "pt_net_value", "pt_risk_events"):
        assert name in registry.list(), f"{name} 未注册"


def test_phase1_rules_present():
    names = {r.name for r in DEFAULT_RULES}
    for rule in ("pt_lessons_rule", "pt_signals_rule",
                 "pt_net_value_rule", "pt_risk_events_rule", "pt_portfolio_rule"):
        assert rule in names, f"{rule} 缺失"


def test_phase1_zero_dangling(registry):
    missing = [s.tool for r in DEFAULT_RULES for s in r.steps if s.tool not in registry.list()]
    assert missing == [], f"悬空工具: {missing}"


def test_phase1_tools_graceful_when_db_empty(registry):
    """DB 无数据时应返回友好文本而非抛异常 (fail-closed)。"""
    for name in ("pt_lessons", "pt_net_value", "pt_risk_events"):
        result = registry.get(name)()
        assert isinstance(result, str)
        assert len(result) > 0


# ════════════════════════════════════════════════════════════
# 2. 认知总线 QUANT_* 事件
# ════════════════════════════════════════════════════════════

def test_quant_event_types_added():
    from laap.agi.cognitive_bus import CognitiveEventType
    for name in ("QUANT_SIGNAL", "QUANT_TRADE_CLOSED", "QUANT_RISK_TRIGGERED",
                 "QUANT_DAILY_SETTLE", "QUANT_EVOLUTION_PROPOSED",
                 "SYSTEM_FAULT"):
        assert hasattr(CognitiveEventType, name), f"{name} 缺失"
    # 11 原有 + 5 QUANT_* + 1 SYSTEM_FAULT（2026-08-18 错误进化桥）
    assert len([e for e in CognitiveEventType]) == 17


def test_quant_event_publish():
    from laap.agi.cognitive_bus import CognitiveEventType, get_bus
    bus = get_bus()
    events_before = len(bus._event_log)
    bus.publish(CognitiveEventType.QUANT_TRADE_CLOSED, source="test",
                data={"symbol": "600519"})
    assert len(bus._event_log) == events_before + 1
    assert bus._event_log[-1].type == CognitiveEventType.QUANT_TRADE_CLOSED


def test_sense_event_injects():
    from laap.paper_trading.quant_bridge import get_bridge
    b = get_bridge()
    ok = b.sense_event("quant_trade_closed", payload={"symbol": "600519"}, pnl=-50.0)
    assert ok is True
    # 亏损 → sentiment_delta 为负
    assert b.stats()["sentiment_delta"] < 0


def test_sense_event_aggregates():
    from laap.paper_trading.quant_bridge import get_bridge
    b = get_bridge()
    for i in range(8):  # 超过 MAX_EVENTS_PER_TYPE=5
        b.sense_event("quant_signal", payload={"symbol": f"{i}"})
    events = b.recent_events("quant_signal")
    assert len(events) <= b.MAX_EVENTS_PER_TYPE


def test_sense_event_unknown_type_fail_closed():
    from laap.paper_trading.quant_bridge import get_bridge
    b = get_bridge()
    assert b.sense_event("bogus_event_type") is False


# ════════════════════════════════════════════════════════════
# 3. 教训双写 (主写 + 从写 + dedup)
# ════════════════════════════════════════════════════════════

class _FakeMemory:
    """UnifiedMemory 假实现 (主写目标)。"""

    def __init__(self):
        self.writes = []

    def encode_experience(self, content, **kw):
        self.writes.append(content)
        return {"episode_id": "ep_test"}


@pytest.fixture()
def outcome():
    from laap.paper_trading.models import OutcomeRecord
    return OutcomeRecord(
        trade_id=777, decision_id="dec_777", pnl_pct=-0.08,
        hold_days=3, vs_expected=-0.05,
        lesson="追高被套，下次等回踩再买", lesson_type="timing",
        verified=False,
    )


def test_double_write_main_and_semantic(outcome, monkeypatch):
    from laap.paper_trading import memory_bridge
    from laap.paper_trading.memory_bridge import _write_lesson_to_semantic_memory
    fake = _FakeMemory()

    written = []
    monkeypatch.setattr(memory_bridge, "_write_lesson_to_semantic_memory",
                        lambda o, s: written.append(s) or True)

    ep = memory_bridge.encode_lesson(fake, outcome, symbol="600519")
    assert ep == "ep_test"
    assert len(fake.writes) == 1          # 主写执行
    assert written == ["600519"]           # 从写触发


def test_semantic_write_dedup_key(outcome, monkeypatch):
    """从写应带 dedup_key 且幂等。"""
    from aris_brain import laap_semantic_memory as sem_mod

    added = []

    class _FakeSemMem:
        def add(self, text, meta=None):
            added.append((text, meta))

    monkeypatch.setattr(sem_mod, "get_memory", lambda: _FakeSemMem())
    monkeypatch.setattr(sem_mod, "recall_memory", lambda *a, **k: [])  # 无历史

    from laap.paper_trading.memory_bridge import _write_lesson_to_semantic_memory
    ok = _write_lesson_to_semantic_memory(outcome, symbol="600519")
    assert ok is True
    assert len(added) == 1
    text, meta = added[0]
    assert "交易教训" in text
    assert meta["dedup_key"] == "777"
    assert meta["symbol"] == "600519"


def test_semantic_write_skips_on_existing(outcome, monkeypatch):
    """dedup_key 已存在时从写跳过 (幂等)。"""
    from aris_brain import laap_semantic_memory as sem_mod
    added = []

    class _FakeSemMem:
        def add(self, text, meta=None):
            added.append(text)

    def fake_recall(*a, **k):
        return [{"text": "x", "meta": {"dedup_key": "777"}}]

    monkeypatch.setattr(sem_mod, "get_memory", lambda: _FakeSemMem())
    monkeypatch.setattr(sem_mod, "recall_memory", fake_recall)

    from laap.paper_trading.memory_bridge import _write_lesson_to_semantic_memory
    ok = _write_lesson_to_semantic_memory(outcome, symbol="600519")
    assert ok is True
    assert added == []  # 未重复写入


def test_main_write_failure_does_not_break(outcome):
    """主写失败 → 静默降级, 不抛异常 (fail-closed)。"""
    from laap.paper_trading import memory_bridge

    class _BrokenMemory:
        def encode_experience(self, *a, **k):
            raise RuntimeError("memory down")

    ep = memory_bridge.encode_lesson(_BrokenMemory(), outcome, symbol="600519")
    assert ep == ""  # 返回空但不抛异常
