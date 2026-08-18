# -*- coding: utf-8 -*-
"""data_sources.py 多源配置化测试。"""
import pytest

from laap.paper_trading.data_sources import (
    source_chain, resolve_first, resolve_first_with_meta)
from laap.paper_trading import quant_config as qc


def test_source_chain_default(monkeypatch):
    # 清掉 env 覆盖（全量回归时其他模块可能已加载 .env），验证配置默认链
    for k in ("MARKET_SOURCES", "KLINE_SOURCES", "NEWS_SOURCES",
              "PROFILE_SOURCES", "REPORT_SOURCES", "CALENDAR_SOURCES",
              "LLM_SOURCES"):
        monkeypatch.delenv(k, raising=False)
    assert source_chain("PROFILE") == ["individual_info", "em_profile", "cninfo"]
    assert source_chain("MARKET") == ["tx", "em", "tickplus", "xq", "akshare", "stub"]
    assert source_chain("KLINE") == ["db", "tushare", "tencent", "akshare",
                                     "synthetic"]
    assert source_chain("NEWS") == ["eastmoney", "sina", "cls", "tushare",
                                    "bocha", "tavily", "minimax", "tencent"]
    assert source_chain("CALENDAR") == ["external", "cache", "weekday"]


def test_source_chain_unknown_domain():
    assert source_chain("UNKNOWN") == []


def test_source_chain_runtime_adjustable(monkeypatch):
    # 改 env 后 chain 实时变化（运行时可调）
    import os
    monkeypatch.setenv("PROFILE_SOURCES", "cninfo,individual_info")
    assert source_chain("PROFILE") == ["cninfo", "individual_info"]
    monkeypatch.delenv("PROFILE_SOURCES", raising=False)


def test_resolve_first_first_success(monkeypatch):
    calls = []

    def a():
        calls.append("a")
        return "A"

    def b():
        calls.append("b")
        return "B"
    # 固定链，避免 .env 的 MARKET_SOURCES 覆盖（tx,em,xq,stub 无 akshare）
    monkeypatch.setenv("MARKET_SOURCES", "akshare,stub")
    result, source = resolve_first("MARKET", {"akshare": a, "stub": b})
    assert (result, source) == ("A", "akshare")
    assert calls == ["a"]  # 只用第一个成功源


def test_resolve_first_fallback(monkeypatch):
    calls = []

    def a():
        calls.append("a")
        raise ConnectionError("down")

    def b():
        calls.append("b")
        return "B"
    monkeypatch.setenv("MARKET_SOURCES", "akshare,stub")
    result, source = resolve_first("MARKET", {"akshare": a, "stub": b})
    assert (result, source) == ("B", "stub")
    assert calls == ["a", "b"]  # 第一个失败 → 回退第二个


def test_resolve_first_all_fail_raises():
    def a():
        raise RuntimeError("a fail")

    def b():
        raise RuntimeError("b fail")
    with pytest.raises(RuntimeError, match="b fail"):
        resolve_first("MARKET", {"akshare": a, "stub": b})


def test_resolve_first_with_meta_fail_closed():
    def a():
        raise RuntimeError("down")
    result, meta = resolve_first_with_meta(
        "PROFILE", {"individual_info": a})
    assert result is None
    assert meta["used_fallback"] is True
    assert "fallback_reason" in meta


def test_resolve_first_with_meta_no_data_flag():
    """NoDataError（源可用但无数据）→ meta.no_data=True，区别于网络降级。"""
    from laap.paper_trading.data_sources import NoDataError

    def a():
        raise NoDataError("no data")
    result, meta = resolve_first_with_meta(
        "PROFILE", {"individual_info": a})
    assert result is None
    assert meta["used_fallback"] is True
    assert meta["no_data"] is True


def test_resolve_first_with_meta_network_error_not_no_data():
    """网络/服务错误 → meta.no_data=False（供上层区分提示）。"""
    def a():
        raise ConnectionError("net down")
    result, meta = resolve_first_with_meta(
        "PROFILE", {"individual_info": a})
    assert result is None
    assert meta["used_fallback"] is True
    assert meta["no_data"] is False


def test_resolve_first_skips_unimplemented_source():
    # 配置里有但 handlers 没有的源 → 跳过
    result, source = resolve_first("MARKET", {"stub": lambda: "S"})
    assert (result, source) == ("S", "stub")


def test_news_source_disabled_via_config(monkeypatch):
    """NEWS_SOURCES 只含未注册源 → fetch_stock_news 返回空+fallback（无可用源，fail-closed）。"""
    import os
    from laap.paper_trading.news_intel import fetch_stock_news
    monkeypatch.setenv("NEWS_SOURCES", "unknown_source")  # 无 handler → 无可用源
    items, meta = fetch_stock_news("600519")
    assert items == []
    assert meta["used_fallback"] is True
    monkeypatch.delenv("NEWS_SOURCES", raising=False)


def test_market_composite_fallback_to_stub():
    """CompositeMarketSource：所有实时源失败 → stub 兜底 + used_fallback。"""
    from laap.paper_trading.market_source import (
        CompositeMarketSource, StubMarketSource, MarketSource)

    class _FailSource(MarketSource):
        def get_price(self, symbol, ts=None):
            raise RuntimeError("down")

    stub = StubMarketSource(seed=1, base_prices={"600519": 100.0})
    c = CompositeMarketSource([_FailSource()], stub)
    price, meta = c.get_price("600519")
    assert meta["used_fallback"] is True
    assert meta["source"] == "stub"


def test_market_composite_skips_degraded():
    """实时源返回 used_fallback=True（内部降级）→ 视为不可用，尝试下一源。"""
    from laap.paper_trading.market_source import (
        CompositeMarketSource, StubMarketSource, MarketSource)

    class _DegradedSource(MarketSource):
        def get_price(self, symbol, ts=None):
            return 100.0, {"source": "stub", "used_fallback": True}  # 降级

    class _GoodSource(MarketSource):
        def get_price(self, symbol, ts=None):
            return 101.0, {"source": "tx", "used_fallback": False}

    c = CompositeMarketSource([_DegradedSource(), _GoodSource()],
                              StubMarketSource())
    price, meta = c.get_price("600519")
    assert price == 101.0
    assert meta["source"] == "tx"
    assert meta["used_fallback"] is False


def test_resolve_source_prefer_live_false_returns_stub():
    """prefer_live=False → StubMarketSource（向后兼容，测试旧断言）。"""
    from laap.paper_trading.market_source import resolve_source, StubMarketSource
    src = resolve_source(prefer_live=False)
    assert isinstance(src, StubMarketSource)


def test_resolve_source_prefer_live_returns_composite():
    """prefer_live=True 且链含实时源 → CompositeMarketSource。

    注意: 不断言 used_fallback 具体值 — 沙箱/本机环境实时源可达性不同
    (tx 源可能成功返回, 也可能回落 stub)。只验证结构正确与诚实标记存在。
    """
    from laap.paper_trading.market_source import (
        resolve_source, CompositeMarketSource)
    src = resolve_source(prefer_live=True)
    assert isinstance(src, CompositeMarketSource)
    _price, meta = src.get_price("600519")
    assert "used_fallback" in meta  # 诚实标记键必须存在
    assert isinstance(meta.get("source"), str)  # 来源可识别


def test_kline_chain_includes_akshare_and_synthetic_gate():
    """KLINE 链含 akshare 备用；synthetic 不在链中时禁止合成回退。"""
    import os
    assert "akshare" in source_chain("KLINE")
    import laap.paper_trading.kline_source as ks
    # synthetic 关闭 → load_ohlcv(fallback=True) 返回空（不产合成假数据）
    os.environ["KLINE_SOURCES"] = "db,akshare"
    try:
        ohlcv, q = ks.load_ohlcv("NONEXIST", days=30, fallback=True, with_quality=True)
        assert ohlcv == []
        assert q["used_fallback"] is True
    finally:
        os.environ.pop("KLINE_SOURCES", None)


def test_kline_fallback_to_tushare(monkeypatch):
    """db 无数据且 KLINE 链含 tushare → load_ohlcv 走 Tushare 直连（used_fallback=True）。"""
    import os
    import watchlist_kline_store as ws
    from laap.paper_trading import kline_source as ks
    monkeypatch.setattr(ws, "get_kline", lambda symbol, days=0: [])  # 模拟 db 无数据
    monkeypatch.setattr(ks, "_load_tushare_ohlcv",
                        lambda symbol, days: [(100.0, 101.0, 102.0, 99.0, 1000.0)])
    monkeypatch.setattr(ks, "_load_akshare_ohlcv", lambda symbol, days: [])
    os.environ["KLINE_SOURCES"] = "db,tushare"
    try:
        ohlcv, q = ks.load_ohlcv("600519", days=30, fallback=True, with_quality=True)
        assert len(ohlcv) == 1
        assert q["source"] == "tushare"
        assert q["used_fallback"] is True
    finally:
        os.environ.pop("KLINE_SOURCES", None)


def test_kline_no_synthetic_when_not_in_chain(monkeypatch):
    """KLINE 链无 tushare/akshare 时禁止合成回退（fail-closed 不产假数据）。"""
    import os
    import watchlist_kline_store as ws
    from laap.paper_trading import kline_source as ks
    monkeypatch.setattr(ws, "get_kline", lambda symbol, days=0: [])  # 模拟 db 无数据
    os.environ["KLINE_SOURCES"] = "db"
    try:
        ohlcv, q = ks.load_ohlcv("600519", days=30, fallback=True, with_quality=True)
        assert ohlcv == []
        assert q["used_fallback"] is True
    finally:
        os.environ.pop("KLINE_SOURCES", None)
