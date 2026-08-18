"""ARIS 错误进化桥单测 (error_evolution_bridge: L1 事件感知 + L2 记忆 + L3 学习)。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _isolate_bus():
    from laap.paper_trading.event_bus import EventBus
    EventBus._instance = None
    yield
    EventBus._instance = None


class _FakeCognitiveBus:
    def __init__(self):
        self.published = []

    def publish(self, event_type, source, data=None):
        self.published.append((event_type, source, data or {}))


class _FakeMemory:
    def __init__(self):
        self.experiences = []

    def encode_experience(self, content, **kw):
        self.experiences.append((content, kw))


class _FakeMeta:
    def __init__(self):
        self.sessions = []

    def record_session(self, **kw):
        self.sessions.append(kw)


def _make_event(payload):
    from laap.paper_trading.event_bus import Event
    return Event("system.internal.error_alert", payload, source="orchestrator")


class TestErrorEvolutionBridge:
    def _bridge(self):
        from laap.agi.error_evolution_bridge import ErrorEvolutionBridge
        cb = _FakeCognitiveBus()
        mem = _FakeMemory()
        meta = _FakeMeta()
        br = ErrorEvolutionBridge(cognitive_bus=cb, memory=mem, meta=meta)
        return br, cb, mem, meta

    def test_l1_publish_system_fault(self):
        from laap.agi.cognitive_bus import CognitiveEventType
        br, cb, mem, meta = self._bridge()
        br._handle_error_alert(_make_event({
            "summary": "test", "analyses": [
                {"category": "database", "priority": 2,
                 "root_cause": "db_read_error", "count": 3,
                 "action": "人工检查", "sample": "no such table"}],
            "counts": {"database": 3}}))
        assert br.faults_notified == 1
        assert len(cb.published) == 1
        et, src, data = cb.published[0]
        assert et == CognitiveEventType.SYSTEM_FAULT
        assert src == "error_evolution_bridge"
        assert data["high_priority"] == ["database"]

    def test_l2_memory_and_l3_meta(self):
        br, cb, mem, meta = self._bridge()
        br._handle_error_alert(_make_event({
            "summary": "s", "analyses": [
                {"category": "file", "priority": 2,
                 "root_cause": "file_missing", "count": 1,
                 "action": "检查路径", "sample": "FileNotFoundError"}],
            "counts": {"file": 1}}))
        # 需人工 → IMPORTANT 记忆 + operational 会话(successful=False)
        assert len(mem.experiences) == 1
        assert "系统故障教训" in mem.experiences[0][0]
        assert mem.experiences[0][1]["priority"].value >= 3  # IMPORTANT
        assert len(meta.sessions) == 1
        assert meta.sessions[0]["strategy"] == "operational"
        assert meta.sessions[0]["successful"] is False
        assert meta.sessions[0]["domain"] == "system_ops"

    def test_l2_auto_disposition_strategy(self):
        br, cb, mem, meta = self._bridge()
        br._handle_error_alert(_make_event({
            "summary": "s", "analyses": [
                {"category": "datasource", "priority": 1,
                 "root_cause": "partial_source_failure", "count": 10,
                 "action": "已自动切换", "sample": "fallback to stub"}],
            "counts": {"datasource": 10}}))
        assert len(meta.sessions) == 1
        assert meta.sessions[0]["strategy"] == "operational"
        assert meta.sessions[0]["successful"] is True  # 自动处置=成功

    def test_attach_detach_subscription(self):
        from laap.paper_trading.event_bus import EventBus
        from laap.agi.error_evolution_bridge import ErrorEvolutionBridge
        bus = EventBus()
        cb = _FakeCognitiveBus()
        br = ErrorEvolutionBridge(bus=bus, cognitive_bus=cb,
                                  memory=_FakeMemory(), meta=_FakeMeta())
        br.attach()
        assert bus.subscriber_count() == 1
        got = []
        bus.subscribe("system.internal.error_alert",
                      lambda ev: got.append(ev.type))
        bus.publish(_make_event({"summary": "x", "analyses": [],
                                 "counts": {}}))
        assert got == ["system.internal.error_alert"]
        assert br.faults_notified == 1
        br.detach()
        assert bus.subscriber_count() == 1  # 只剩测试订阅
