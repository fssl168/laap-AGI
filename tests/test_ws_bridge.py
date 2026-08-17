"""EventBus → WebSocket 桥接测试 (EventWsBridge, 2026-08-18)。

覆盖: 注册/注销、默认主题覆盖 8 场景、事件推送、主题过滤、动态改订阅、
慢客户端丢最旧、跨线程发布（call_soon_threadsafe）、路由注册。
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from laap.paper_trading.event_bus import EventBus, Event, any_topic_matches
from laap.paper_trading.ws_bridge import EventWsBridge, _DEFAULT_TOPICS, _Client


class _FakeWs:
    """最小假 WS：记录 send_json 收到的消息。"""

    def __init__(self):
        self.sent: list = []
        self.closed = False

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        for t in asyncio.all_tasks(loop):
            t.cancel()
        try:
            loop.run_until_complete(asyncio.sleep(0))
        except Exception:
            pass
        loop.close()


# ── 主题过滤 (event_bus 公共入口) ─────────────────────────────

class TestTopicMatches:
    def test_exact(self):
        assert any_topic_matches(["system.status"], "system.status")

    def test_wildcard(self):
        assert any_topic_matches(["market.tick.*"], "market.tick.600519.price")

    def test_prefix(self):
        assert any_topic_matches(["market.limitup"], "market.limitup.600519")

    def test_empty_filters_match_nothing(self):
        assert not any_topic_matches([], "anything.any")

    def test_star_matches_all(self):
        assert any_topic_matches(["*"], "anything.any")

    def test_no_match(self):
        assert not any_topic_matches(["system.status"], "market.tick.600519.price")


# ── EventWsBridge ─────────────────────────────────────────────

class TestDefaultTopics:
    def test_covers_all_scenarios(self):
        """默认订阅主题应覆盖 8 个要求场景 + 交易/风控。"""
        expected = {
            "market.tick.*",       # 3 实时 tick
            "market.auction.*",    # 4 集合竞价
            "market.orderbook.*",  # 5 五档
            "market.limitup.*",    # 5 涨停板
            "market.fault.*",      # 6 故障
            "system.status",       # 7 系统状态
            "system.internal.*",   # 8 内部消息
            "trade.*",             # 交易
        }
        assert expected.issubset(set(_DEFAULT_TOPICS))


class TestRegisterLifecycle:
    def test_register_unregister(self):
        async def scenario():
            bridge = EventWsBridge(bus=EventBus())
            cid = bridge.register(_FakeWs())
            assert bridge.client_count() == 1
            assert bridge.unregister(cid) is True
            assert bridge.client_count() == 0
            assert bridge.unregister(cid) is False  # 已注销
        _run(scenario())

    def test_default_topics_applied(self):
        async def scenario():
            bridge = EventWsBridge(bus=EventBus())
            cid = bridge.register(_FakeWs())
            client = bridge._clients[cid]
            assert set(client.filters) == set(_DEFAULT_TOPICS)
            bridge.unregister(cid)
        _run(scenario())


class TestPush:
    def test_event_pushed_to_client(self):
        async def scenario():
            bus = EventBus()
            bridge = EventWsBridge(bus=bus)
            fake = _FakeWs()
            cid = bridge.register(fake)
            bus.publish(Event("market.limitup.600519", {"symbol": "600519"}))
            bus.publish(Event("system.status", {"running": True}))
            await asyncio.sleep(0.05)
            bridge.unregister(cid)
            return fake
        fake = _run(scenario())
        types = {e["type"] for e in fake.sent}
        assert "market.limitup.600519" in types
        assert "system.status" in types
        # 下行结构 = Event.to_dict()
        ev = next(e for e in fake.sent if e["type"] == "market.limitup.600519")
        assert ev["payload"]["symbol"] == "600519"

    def test_topic_filtering(self):
        async def scenario():
            bus = EventBus()
            bridge = EventWsBridge(bus=bus)
            fake = _FakeWs()
            cid = bridge.register(fake, topics=["market.limitup.*"])
            bus.publish(Event("market.limitup.600519", {"symbol": "600519"}))
            bus.publish(Event("market.tick.600519.price", {"symbol": "600519"}))
            await asyncio.sleep(0.05)
            bridge.unregister(cid)
            return fake
        fake = _run(scenario())
        types = {e["type"] for e in fake.sent}
        assert "market.limitup.600519" in types
        assert "market.tick.600519.price" not in types

    def test_set_remove_topics(self):
        async def scenario():
            bus = EventBus()
            bridge = EventWsBridge(bus=bus)
            fake = _FakeWs()
            cid = bridge.register(fake)
            bridge.set_topics(cid, ["system.status"])
            bus.publish(Event("system.status", {"running": True}))
            bus.publish(Event("market.tick.600519.price", {"symbol": "600519"}))
            await asyncio.sleep(0.05)
            got1 = [e["type"] for e in fake.sent]
            fake.sent.clear()
            bridge.remove_topics(cid, ["system.status"])
            bus.publish(Event("system.status", {"running": False}))
            await asyncio.sleep(0.05)
            got2 = [e["type"] for e in fake.sent]
            bridge.unregister(cid)
            return got1, got2
        got1, got2 = _run(scenario())
        assert "system.status" in got1
        assert "market.tick.600519.price" not in got1
        assert got2 == []  # 已移除订阅

    def test_thread_safe_publish_from_other_thread(self):
        """真实场景: 行情源线程(非事件循环线程) publish → 客户端收到。"""
        async def scenario():
            bus = EventBus()
            bridge = EventWsBridge(bus=bus)
            fake = _FakeWs()
            cid = bridge.register(fake)

            def pub():
                bus.publish(Event("system.status", {"running": True},
                                  source="test-thread"))
            t = threading.Thread(target=pub)
            t.start()
            t.join()
            await asyncio.sleep(0.05)
            bridge.unregister(cid)
            return fake
        fake = _run(scenario())
        assert any(e["type"] == "system.status" for e in fake.sent)

    def test_slow_client_drops_oldest(self):
        """慢客户端（队列满）丢最旧事件，不丢新事件、不阻塞总线。"""
        async def scenario():
            bridge = EventWsBridge(bus=EventBus(), queue_size=2)
            client = _Client("c1", _FakeWs(), filters=[], queue_size=2)
            bridge._enqueue(client, {"type": "a", "n": 1})
            bridge._enqueue(client, {"type": "b", "n": 2})
            assert client.queue.qsize() == 2
            bridge._enqueue(client, {"type": "c", "n": 3})  # 满 → 丢最旧 a
            assert client.queue.qsize() == 2
            got = []
            while not client.queue.empty():
                got.append(client.queue.get_nowait()["type"])
            assert got == ["b", "c"]
            assert client.dropped == 1
        _run(scenario())


class TestBusSubscription:
    def test_subscribed_once(self):
        async def scenario():
            bus = EventBus()
            bridge = EventWsBridge(bus=bus)
            cid1 = bridge.register(_FakeWs())
            cid2 = bridge.register(_FakeWs())
            assert bridge._bus_sid is not None
            f1 = bridge._clients[cid1]
            f2 = bridge._clients[cid2]
            bus.publish(Event("system.status", {"running": True}))
            await asyncio.sleep(0.05)
            bridge.unregister(cid1)
            bridge.unregister(cid2)
            return (f1.sender_task is not None, f2.sender_task is not None)
        ok1, ok2 = _run(scenario())
        assert ok1 and ok2

    def test_close_all(self):
        async def scenario():
            bus = EventBus()
            bridge = EventWsBridge(bus=bus)
            bridge.register(_FakeWs())
            bridge.register(_FakeWs())
            assert bridge.client_count() == 2
            bridge.close_all()
            assert bridge.client_count() == 0
        _run(scenario())


# ── API 路由注册 ─────────────────────────────────────────────

def test_ws_route_registered():
    from laap_brain.api import create_app
    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/events/ws" in routes
    assert "/v1/quant/events/status" in routes
