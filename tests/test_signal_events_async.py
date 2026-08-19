"""验证 2026-08-19: signal_events 异步预取+缓存 (async_fetch_stock_names)。

laap/paper_trading/signal_events.py 新增:
- async_fetch_stock_names(symbols): 非阻塞, 返回当前缓存(可能空) + 触发后台预取
- _background_prefetch(symbols): 后台线程抓 30s 防抖、24h 缓存

测试隔离: 用一个自定义 cache/lock/线程替身, 验证调度状态机, 不触网。
"""
import sys
import threading
import time as _time


def _sym(name):
    return getattr(sys.modules.get(__name__), name, None)


def test_async_fetch_returns_cached_without_network():
    """进程内短缓存命中 → 直接返回, 不触发后台线程。"""
    from laap.paper_trading import signal_events as se

    # 塞入进程内短缓存
    se._stock_names_cache = {"600519": "贵州茅台"}
    se._stock_names_cache_ts = _time.time()
    started = []
    orig = se._background_prefetch
    se._background_prefetch = lambda s: started.append(list(s))

    try:
        res = se.async_fetch_stock_names(["600519"])
        assert res == {"600519": "贵州茅台"}, res
        assert started == [], "短缓存命中不应触发后台预取"
    finally:
        se._background_prefetch = orig
        se._stock_names_cache = {}
        se._stock_names_cache_ts = 0.0


def test_async_fetch_spawns_prefetch_on_miss():
    """无缓存 → 立即返回空 + 触发放飞后台线程(30s 防抖)。"""
    from laap.paper_trading import signal_events as se

    # 强制无缓存 + 解锁防抖
    se._stock_names_cache = {}
    se._stock_names_cache_ts = 0.0
    se._stock_names_prefetch_until = 0.0
    started = []
    real_bg = se._background_prefetch

    def fake_bg(symbols):
        started.append(list(symbols))

    se._background_prefetch = fake_bg
    try:
        res = se.async_fetch_stock_names(["000410", "603663"])
        assert res == {}, "冷缓存应立即返回空, 不等待网络"
        assert started and started[0] == ["000410", "603663"], f"应触发放飞: {started}"
        assert se._stock_names_prefetch_until > _time.time(), "防抖应被锁定"
        # 30s 内再次调用不重复触发
        n = len(started)
        se.async_fetch_stock_names(["600519"])
        assert len(started) == n, "防抖窗口内不应重复触发"
    finally:
        se._background_prefetch = real_bg
