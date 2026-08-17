"""事件驱动层单元测试 (event_bus / market_events / scenario_handlers / orchestrator)。

2026-08-17 扩充: 涨停昨收基准 + limit_utils 单源、故障检测 (failed_sources)、
五档盘口、DailyCycleHandler/PositionMonitor、竞价交易日校验、SystemStatus 去重。
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from laap.paper_trading.event_bus import EventBus, Event, subscribe, publish, _topic_match
from laap.paper_trading.scenario_handlers import (
    ScenarioHub, TickMonitor, LimitUpMonitor, AuctionMonitor, OrderBookMonitor,
    FaultReporter, DailyCycleHandler, PositionMonitor, SystemStatus,
    InternalMessenger, TradeNotifier)


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

    def test_has_subscribers(self):
        bus = EventBus()
        assert not bus.has_subscribers("market.orderbook.600519")
        bus.subscribe("market.orderbook.*", lambda ev: None)
        assert bus.has_subscribers("market.orderbook.600519")
        assert not bus.has_subscribers("market.orderbook")  # 前缀不反向匹配
        bus.subscribe("market.orderbook", lambda ev: None)
        assert bus.has_subscribers("market.orderbook.600519")


# ── ScenarioHandlers ─────────────────────────────────────

class TestScenarioHandlers:
    def test_tick_monitor(self):
        bus = EventBus()
        # alert_all_session=True: 测试不依赖当前时段（避免盘前/盘后无告警）
        tm = TickMonitor(bus, alert_pct=5.0, alert_all_session=True)
        tm.attach()
        # 正常 tick
        bus.publish(Event("market.tick.600519.price",
                          {"symbol": "600519", "price": 100.0, "prev_price": 99.0,
                           "prev_close": 99.0, "source": "tx"}))
        # 大波动 tick (触发告警, 相对昨收)
        bus.publish(Event("market.tick.600519.price",
                          {"symbol": "600519", "price": 110.0, "prev_price": 100.0,
                           "prev_close": 100.0, "change_pct": 10.0, "source": "tx"}))
        assert tm.tick_count == 2
        assert len(tm.alerts) == 1
        assert tm.alerts[0]["change_pct"] == 10.0

    def test_tick_alert_cooldown_dedup(self):
        """同标的冷却窗口内重复大波动只告警一次（2026-08-18 补齐）。"""
        bus = EventBus()
        tm = TickMonitor(bus, alert_pct=5.0, alert_all_session=True,
                         alert_cooldown=60.0)
        tm.attach()
        base = 1000.0
        for i in range(3):  # 冷却窗口内连续 3 个大波动 tick
            bus.publish(Event("market.tick.600519.price",
                              {"symbol": "600519", "price": 110.0,
                               "change_pct": 10.0, "ts": base + i,
                               "source": "tx"}))
        assert len(tm.alerts) == 1
        # 超过冷却窗口 → 再次告警
        bus.publish(Event("market.tick.600519.price",
                          {"symbol": "600519", "price": 112.0,
                           "change_pct": 12.0, "ts": base + 100.0,
                           "source": "tx"}))
        assert len(tm.alerts) == 2

    def test_tick_alert_session_gating(self):
        """非告警时段(盘前/盘后)大波动不发告警；时段内发（2026-08-18 补齐）。"""
        bus = EventBus()
        tm = TickMonitor(bus, alert_pct=5.0, alert_all_session=False)
        tm.attach()
        # 模拟非告警时段
        tm._alert_window_ok = lambda now=None: False
        bus.publish(Event("market.tick.600519.price",
                          {"symbol": "600519", "price": 110.0,
                           "change_pct": 10.0, "source": "tx"}))
        assert len(tm.alerts) == 0
        # 模拟盘中时段
        tm._alert_window_ok = lambda now=None: True
        bus.publish(Event("market.tick.600519.price",
                          {"symbol": "600519", "price": 110.0,
                           "change_pct": 10.0, "source": "tx"}))
        assert len(tm.alerts) == 1

    def test_tick_alert_pct_from_config(self):
        """告警阈值走 quant_config 单源（默认 5.0）。"""
        from laap.paper_trading import quant_config as qc
        tm = TickMonitor(EventBus())
        assert tm.alert_pct == qc.LAAP_TICK_ALERT_PCT
        assert tm.alert_cooldown == qc.LAAP_TICK_ALERT_COOLDOWN
        assert tm.alert_all_session == qc.LAAP_TICK_ALERT_ALL_SESSION

    def test_limit_up_monitor_prev_close_basis(self):
        """涨停以昨收/涨跌停价为基准（limit_utils 单源），非 prev_price。"""
        bus = EventBus()
        lm = LimitUpMonitor(bus)
        lm.attach()
        captured = []
        bus.subscribe("market.limitup.*", lambda ev: captured.append(ev.payload))
        # 封板: price == up_limit (昨收 10.0 → 涨停价 11.0), 且 prev_price 无波动
        bus.publish(Event("market.tick.000001.price",
                          {"symbol": "000001", "price": 11.0, "prev_price": 11.0,
                           "prev_close": 10.0, "up_limit": 11.0, "down_limit": 9.0,
                           "is_limit_up": True, "source": "tx"}))
        assert len(lm.captured) == 1
        assert len(captured) == 1
        assert captured[0]["change_pct"] == 10.0

    def test_limit_up_monitor_dedup_per_day(self):
        """同一标的当日只首触一次；次日重新计数。"""
        bus = EventBus()
        lm = LimitUpMonitor(bus)
        lm.attach()
        day1 = time.mktime(time.strptime("2026-08-17 10:00:00", "%Y-%m-%d %H:%M:%S"))
        day2 = time.mktime(time.strptime("2026-08-18 10:00:00", "%Y-%m-%d %H:%M:%S"))
        base = {"symbol": "600519", "price": 121.0, "prev_close": 110.0,
                "up_limit": 121.0, "down_limit": 99.0, "is_limit_up": True,
                "source": "tx"}
        for _ in range(3):  # 同日多次封板 tick
            ev = Event("market.tick.600519.price", dict(base))
            ev.ts = day1
            bus.publish(ev)
        assert len(lm.captured) == 1
        ev = Event("market.tick.600519.price", dict(base))
        ev.ts = day2
        bus.publish(ev)
        assert len(lm.captured) == 2

    def test_limit_up_monitor_no_false_positive(self):
        """无涨跌停价/无标记 → fail-closed 不误报。"""
        bus = EventBus()
        lm = LimitUpMonitor(bus)
        lm.attach()
        bus.publish(Event("market.tick.600519.price",
                          {"symbol": "600519", "price": 11.0, "prev_price": 10.0,
                           "prev_close": 10.0, "source": "tx"}))  # 无 up_limit
        assert len(lm.captured) == 0

    def test_auction_monitor(self):
        bus = EventBus()
        am = AuctionMonitor(bus)
        am.attach()
        bus.publish(Event("market.auction.600519", {"symbol": "600519", "price": 99.0}))
        assert len(am.auctions) == 1

    def test_orderbook_monitor(self):
        bus = EventBus()
        om = OrderBookMonitor(bus)
        om.attach()
        got = []
        bus.subscribe("market.orderbook.*", lambda ev: got.append(ev))
        bus.publish(Event("market.orderbook.600519",
                          {"symbol": "600519",
                           "bids": [{"price": 99.9, "volume": 100}],
                           "asks": [{"price": 100.1, "volume": 200}],
                           "source": "tx"}))
        assert om.updates == 1
        assert om.latest("600519")["bids"][0]["price"] == 99.9
        assert len(got) == 1

    def test_fault_reporter(self):
        bus = EventBus()
        fr = FaultReporter(bus)
        fr.attach()
        bus.publish(Event("market.fault.tx", {"source": "tx", "reason": "conn lost"}))
        assert len(fr.faults) == 1
        assert fr.faults[0]["source"] == "tx"

    def test_daily_cycle_handler(self):
        bus = EventBus()
        calls = []
        dc = DailyCycleHandler(bus, on_daily_cycle=lambda: calls.append("cycle"))
        dc.attach()
        bus.publish(Event("system.internal.daily_cycle", {"ts": 1}))
        assert calls == ["cycle"]
        assert dc.run_count == 1

    def test_position_monitor(self):
        bus = EventBus()
        calls = []
        pm = PositionMonitor(bus, on_monitor=lambda: calls.append("monitor"))
        pm.attach()
        bus.publish(Event("system.internal.position_monitor", {"ts": 1}))
        assert calls == ["monitor"]
        assert pm.run_count == 1

    def test_system_status_dedup(self):
        bus = EventBus()
        ss = SystemStatus(bus)
        ss.attach()
        ss.report(running=True, symbols=3)
        # report 发布 1 条 + 自身订阅回环事件按 source 去重 → 仅 1 条记录
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
        assert bus.subscriber_count() >= 10
        hub.detach_all()
        assert bus.subscriber_count() == 0


# ── MarketEventSource (用 stub 行情, 不连网) ────────────────

class _FakeMarket:
    """可编程行情源: get_price 返回固定价格/元信息。"""

    def __init__(self, price=100.0, meta=None):
        self.price = price
        self.meta = meta or {"source": "stub", "used_fallback": True}

    def get_price(self, symbol, ts=None):
        return self.price, dict(self.meta)


class TestMarketEventSource:
    def test_tick_publish_and_cache(self):
        from laap.paper_trading.market_events import MarketEventSource
        bus = EventBus()
        got = []
        src = MarketEventSource(symbols=["600519"], interval=1.0, bus=bus)
        src._market = _FakeMarket()
        src._tick()  # 手动跑一轮
        latest = src.latest("600519")
        assert latest["price"] == 100.0
        assert latest["source"] == "stub"
        bus.subscribe("market.tick.*.price", lambda ev: got.append(ev))
        src._tick()
        assert len(got) >= 1
        assert got[0].payload["price"] == 100.0

    def test_tick_payload_enriched(self):
        """富化字段随 tick 载荷发布（昨收/涨跌幅/涨跌停价/标记）。"""
        from laap.paper_trading.market_events import MarketEventSource
        bus = EventBus()
        got = []
        src = MarketEventSource(symbols=["600519"], interval=1.0, bus=bus)
        src._market = _FakeMarket(
            price=11.0,
            meta={"source": "tx", "used_fallback": False, "prev_close": 10.0})
        bus.subscribe("market.tick.*.price", lambda ev: got.append(ev))
        src._tick()
        p = got[-1].payload
        assert p["prev_close"] == 10.0
        assert p["change_pct"] == 10.0
        assert p["up_limit"] == 11.0
        assert p["down_limit"] == 9.0
        assert p["is_limit_up"] is True

    def test_fault_via_failed_sources(self):
        """Composite 回落 stub 时 failed_sources → 连续失败达阈值发布 market.fault.*。"""
        from laap.paper_trading.market_events import MarketEventSource
        from laap.paper_trading.market_source import TxMarketSource, EmMarketSource
        bus = EventBus()
        got = []
        bus.subscribe("market.fault.*", lambda ev: got.append(ev.type))
        src = MarketEventSource(symbols=["600519"], interval=1.0, bus=bus)
        # 模拟 Composite: 暴露 _sources 供 market.fault.all 判定
        src._market = _FakeMarket(price=99.0, meta={
            "source": "stub", "used_fallback": True,
            "failed_sources": ["TxMarketSource", "EmMarketSource"]})
        src._market._sources = [TxMarketSource(), EmMarketSource()]
        for _ in range(3):  # 3 轮连续失败
            src._tick()
        assert "market.fault.TxMarketSource" in got
        assert "market.fault.EmMarketSource" in got
        assert "market.fault.all" in got  # 所有实时源均达阈值
        # 恢复: 源恢复成功 → 发布 recovered
        got.clear()
        src._market = _FakeMarket(price=100.0,
                                  meta={"source": "tx", "used_fallback": False})
        src._tick()
        assert "market.fault.recovered" in got

    def test_orderbook_only_when_subscribed(self):
        """有订阅者且源支持五档时发布 market.orderbook.*。"""
        from laap.paper_trading.market_events import MarketEventSource
        bus = EventBus()
        got = []
        bus.subscribe("market.orderbook.*", lambda ev: got.append(ev))
        src = MarketEventSource(symbols=["600519"], interval=1.0, bus=bus)
        src._market = _FakeMarket(meta={"source": "tx", "used_fallback": False})

        class _BookMarket(_FakeMarket):
            def get_orderbook(self, symbol, ts=None):
                return {"bids": [{"price": 99.9, "volume": 100}],
                        "asks": [{"price": 100.1, "volume": 200}],
                        "source": "tx", "used_fallback": False}

        src._market = _BookMarket()
        src._tick()
        assert len(got) == 1
        assert got[0].type == "market.orderbook.600519"
        assert got[0].payload["bids"][0]["price"] == 99.9

    def test_snapshot(self):
        from laap.paper_trading.market_events import MarketEventSource
        bus = EventBus()
        src = MarketEventSource(symbols=["000001", "600519"], interval=1.0, bus=bus)
        src._market = _FakeMarket(price=10.0)
        src._tick()
        snap = src.snapshot()
        assert set(snap.keys()) == {"000001", "600519"}

    def test_auction_skips_non_trading_day(self, monkeypatch):
        """非交易日不发集合竞价事件；交易日 9:15-9:25 发。"""
        from laap.paper_trading import market_events as me
        bus = EventBus()
        got = []
        bus.subscribe("market.auction.*", lambda ev: got.append(ev))
        src = me.MarketEventSource(symbols=["600519"], interval=1.0, bus=bus)
        src._market = _FakeMarket(price=99.0, meta={"source": "tx", "used_fallback": False})
        src._tick()  # 先填缓存
        # 固定在 9:20（竞价窗口内）; 2026-08-17 为周一
        fake_now = time.mktime(time.strptime("2026-08-17 09:20:00", "%Y-%m-%d %H:%M:%S"))
        orig_localtime = me.time.localtime
        monkeypatch.setattr(me.time, "localtime",
                            lambda n: orig_localtime(fake_now))
        src._is_trading_day = lambda: False
        src._maybe_auction_event(fake_now)
        assert len(got) == 0
        src._is_trading_day = lambda: True
        src._maybe_auction_event(fake_now)
        assert len(got) == 1
        assert got[0].type == "market.auction.600519"


# ── limit_utils 板块涨跌停价（契约单源）────────────────────

class TestLimitPrices:
    def test_limit_prices_boards(self):
        from laap.paper_trading.limit_utils import limit_prices
        assert limit_prices(10.0, "600519") == (11.0, 9.0)          # 主板 10%
        assert limit_prices(10.0, "300750") == (12.0, 8.0)          # 创业板 20%
        assert limit_prices(10.0, "688981") == (12.0, 8.0)          # 科创板 20%
        assert limit_prices(10.0, "830799") == (13.0, 7.0)          # 北交所 30%
        assert limit_prices(10.0, "600519", name="ST某某") == (10.5, 9.5)  # ST 5%
        assert limit_prices(0) == (None, None)                      # 无效昨收


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
        """daily_cycle / position_monitor 事件经订阅器触发真实回调（P0-2 闭环）。"""
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

    def test_in_market_session(self):
        from laap.paper_trading.event_orchestrator import EventOrchestrator
        assert EventOrchestrator._in_market_session(9 * 60 + 30)   # 09:30
        assert EventOrchestrator._in_market_session(11 * 60 + 30)  # 11:30
        assert not EventOrchestrator._in_market_session(12 * 60)   # 午休
        assert EventOrchestrator._in_market_session(13 * 60)       # 13:00
        assert EventOrchestrator._in_market_session(15 * 60)       # 15:00
        assert not EventOrchestrator._in_market_session(15 * 60 + 1)

    def test_parse_hm(self):
        from laap.paper_trading.event_orchestrator import _parse_hm
        assert _parse_hm("15:35") == 15 * 60 + 35
        assert _parse_hm("bad") == 15 * 60 + 35  # fail-closed 退默认

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

    def test_status_shape(self):
        from laap.paper_trading.event_orchestrator import EventOrchestrator
        bus = EventBus()
        orch = EventOrchestrator(symbols=["600519"], interval=1.0, bus=bus)
        orch.start()
        st = orch.status()
        assert st["running"] is True
        assert "schedule" in st
        assert st["schedule"]["position_interval"] >= 5.0
        orch.stop()
