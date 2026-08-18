"""事件驱动层单元测试 (event_bus / market_events / scenario_handlers / orchestrator)。"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from laap.paper_trading.event_bus import EventBus, Event, subscribe, publish, _topic_match
from laap.paper_trading.scenario_handlers import (
    ScenarioHub, TickMonitor, LimitUpMonitor, AuctionMonitor,
    FaultReporter, SystemStatus, InternalMessenger, TradeNotifier)


@pytest.fixture(autouse=True)
def _isolate_bus():
    """每测试重置 EventBus 单例。"""
    EventBus._instance = None
    yield
    EventBus._instance = None


# ── EventBus ─────────────────────────────────────────────

class TestEventBus:
    def test_publish_subscribe(self):
        bus = EventBus()
        got = []
        bus.subscribe("market.tick.*", lambda ev: got.append(ev.type))
        bus.publish(Event("market.tick.600519.price", {"price": 1.0}))
        assert got == ["market.tick.600519.price"]

    def test_prefix_match(self):
        assert _topic_match("market.tick", "market.tick.600519.price")
        assert _topic_match("market.*", "market.fault.tx")
        assert not _topic_match("market.tick", "market.fault.tx")
        assert _topic_match("trade.600519", "trade.600519.buy")

    def test_wildcard(self):
        assert _topic_match("market.tick.*.price", "market.tick.600519.price")
        assert not _topic_match("market.tick.*.price", "market.tick.600519.other")

    def test_unsubscribe(self):
        bus = EventBus()
        got = []
        sid = bus.subscribe("a.b", lambda ev: got.append(1))
        bus.publish(Event("a.b"))
        assert len(got) == 1
        bus.unsubscribe(sid)
        bus.publish(Event("a.b"))
        assert len(got) == 1  # 不再收到

    def test_handler_exception_isolation(self):
        bus = EventBus()
        got = []

        def bad(ev):
            raise RuntimeError("boom")

        bus.subscribe("a", bad)
        bus.subscribe("a", lambda ev: got.append(1))
        bus.publish(Event("a"))
        assert got == [1]  # 坏 handler 不影响好的

    def test_history(self):
        bus = EventBus()
        bus.publish(Event("a.b", {"n": 1}))
        bus.publish(Event("a.c", {"n": 2}))
        h = bus.history("a.*")
        assert len(h) == 2
        h2 = bus.history("a.b")
        assert len(h2) == 1

    def test_module_level_api(self):
        EventBus._instance = None
        got = []
        subscribe("test.*", lambda ev: got.append(ev.payload))
        publish("test.hello", {"x": 1})
        assert got == [{"x": 1}]


# ── ScenarioHandlers ─────────────────────────────────────

class TestScenarioHandlers:
    def test_tick_monitor(self):
        bus = EventBus()
        tm = TickMonitor(bus, alert_pct=5.0)
        tm.attach()
        # 构造盘中时间戳（10:00），避免测试依赖运行时段（午休/盘前盘后门控）
        from datetime import datetime
        ts = datetime(2026, 8, 18, 10, 0, 0).timestamp()
        # 正常 tick
        bus.publish(Event("market.tick.600519.price",
                          {"symbol": "600519", "price": 100.0, "prev_price": 99.0,
                           "source": "tx", "ts": ts}))
        # 大波动 tick (触发告警)
        bus.publish(Event("market.tick.600519.price",
                          {"symbol": "600519", "price": 110.0, "prev_price": 100.0,
                           "source": "tx", "ts": ts}))
        assert tm.tick_count == 2
        assert len(tm.alerts) == 1
        assert tm.alerts[0]["change_pct"] == 10.0

    def test_limit_up_monitor(self):
        bus = EventBus()
        lm = LimitUpMonitor(bus)
        lm.attach()
        captured = []
        bus.subscribe("market.limitup.*", lambda ev: captured.append(ev.payload))
        # NAS 更新后 LimitUpMonitor 消费富化载荷（up_limit/is_limit_up/prev_close），
        # 判定对齐 limit_utils 单源（close ≥ up_limit×0.9999）
        bus.publish(Event("market.tick.000001.price",
                          {"symbol": "000001", "price": 11.0, "prev_price": 10.0,
                           "prev_close": 10.0, "up_limit": 11.0,
                           "is_limit_up": True, "source": "tx"}))
        assert len(lm.captured) == 1
        assert len(captured) == 1
        assert captured[0]["change_pct"] == 10.0

    def test_auction_monitor(self):
        bus = EventBus()
        am = AuctionMonitor(bus)
        am.attach()
        bus.publish(Event("market.auction.600519", {"symbol": "600519", "price": 99.0}))
        assert len(am.auctions) == 1

    def test_fault_reporter(self):
        bus = EventBus()
        fr = FaultReporter(bus)
        fr.attach()
        bus.publish(Event("market.fault.tx", {"source": "tx", "reason": "conn lost"}))
        assert len(fr.faults) == 1
        assert fr.faults[0]["source"] == "tx"

    def test_system_status(self):
        bus = EventBus()
        ss = SystemStatus(bus)
        ss.attach()
        ss.report(running=True, symbols=3)
        # 2026-08-17 修复：自身 report 的事件不回环重复记录 → 1 条
        assert len(ss.statuses) == 1
        assert ss.statuses[0]["running"] is True

    def test_internal_messenger(self):
        bus = EventBus()
        im = InternalMessenger(bus)
        im.attach()
        im.send("daily_cycle", {"ts": 123})
        assert len(im.messages) == 1
        assert im.messages[0]["channel"] == "system.internal.daily_cycle"

    def test_trade_notifier(self):
        bus = EventBus()
        tn = TradeNotifier(bus)
        tn.attach()
        bus.publish(Event("trade.600519.buy", {"symbol": "600519", "qty": 100}))
        assert len(tn.trades) == 1
        assert tn.trades[0]["type"] == "trade.600519.buy"

    def test_scenario_hub_attach_detach(self):
        bus = EventBus()
        hub = ScenarioHub(bus)
        hub.attach_all()
        assert bus.subscriber_count() >= 7
        hub.detach_all()
        assert bus.subscriber_count() == 0


# ── MarketEventSource (用 stub 行情, 不连网) ────────────────

class TestMarketEventSource:
    def test_tick_publish_and_cache(self):
        from laap.paper_trading.market_events import MarketEventSource
        bus = EventBus()
        got = []

        class _FakeMarket:
            def get_price(self, symbol, ts=None):
                return 100.0, {"source": "stub", "used_fallback": True}

        src = MarketEventSource(symbols=["600519"], interval=1.0, bus=bus)
        src._market = _FakeMarket()  # 注入 stub
        src._tick()  # 手动跑一轮
        # 缓存
        latest = src.latest("600519")
        assert latest["price"] == 100.0
        assert latest["source"] == "stub"
        # 订阅者收到 tick
        bus.subscribe("market.tick.*.price", lambda ev: got.append(ev))
        src._tick()
        assert len(got) >= 1
        assert got[0].payload["price"] == 100.0

    def test_snapshot(self):
        from laap.paper_trading.market_events import MarketEventSource
        bus = EventBus()

        class _FakeMarket:
            def get_price(self, symbol, ts=None):
                return 10.0, {"source": "stub", "used_fallback": True}

        src = MarketEventSource(symbols=["000001", "600519"], interval=1.0, bus=bus)
        src._market = _FakeMarket()
        src._tick()
        snap = src.snapshot()
        assert set(snap.keys()) == {"000001", "600519"}


# ── EventOrchestrator ────────────────────────────────────

class TestEventOrchestrator:
    def test_start_stop(self):
        from laap.paper_trading.event_orchestrator import EventOrchestrator
        bus = EventBus()
        orch = EventOrchestrator(symbols=["600519"], interval=1.0, bus=bus)
        assert orch.start() is True
        assert orch.is_running
        assert orch.start() is False  # 已启动
        assert orch.stop() is True
        assert not orch.is_running

    def test_internal_message_triggers_callback(self):
        from laap.paper_trading.event_orchestrator import EventOrchestrator
        bus = EventBus()
        calls = []
        orch = EventOrchestrator(
            symbols=[], interval=1.0, bus=bus,
            on_daily_cycle=lambda: calls.append("daily"),
            on_position_monitor=lambda: calls.append("position"))
        orch.start()
        orch.trigger_daily_cycle()
        orch.trigger_position_monitor()
        assert "daily" in calls
        assert "position" in calls
        orch.stop()

    def test_publish_trade_event(self):
        from laap.paper_trading.event_orchestrator import EventOrchestrator
        bus = EventBus()
        orch = EventOrchestrator(symbols=[], interval=1.0, bus=bus)
        got = []
        bus.subscribe("trade.*", lambda ev: got.append(ev.type))
        orch.start()
        orch.publish_trade("600519", "buy", {"qty": 100})
        assert "trade.600519.buy" in got
        orch.stop()
