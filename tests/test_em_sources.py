# -*- coding: utf-8 -*-
"""数据源优化测试：em_sources 直连 + cache_backend 两级缓存。

覆盖:
  - cache_backend: 内存降级（redis 不可用）、redis 读写、前缀清除
  - em_sources: 参数解析（jsonp 解包、市场代码映射）、失败 fail-closed
  - em_reports: 缓存键读写
全部 stub 网络（不真连外网）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from laap.paper_trading import cache_backend as cb
from laap.paper_trading.em_sources import _em_market_code, fetch_news_direct


# ════════════════════════════════════════════════════════════
# cache_backend
# ════════════════════════════════════════════════════════════

def test_mem_cache_fallback_when_redis_disabled(monkeypatch):
    """redis 禁用/不可用时内存兜底正常工作。"""
    monkeypatch.setattr(cb, "_REDIS_ENABLED", False)
    monkeypatch.setattr(cb, "_redis", None)
    cb._MEM_CACHE.clear()
    cb.cache_set("t:1", {"v": 1}, ttl=60)
    assert cb.cache_get("t:1") == {"v": 1}
    assert cb.cache_get("t:none") is None


def test_mem_cache_expiry(monkeypatch):
    monkeypatch.setattr(cb, "_REDIS_ENABLED", False)
    monkeypatch.setattr(cb, "_redis", None)
    cb._MEM_CACHE.clear()
    cb.cache_set("t:exp", "x", ttl=1)
    assert cb.cache_get("t:exp") == "x"
    # 模拟过期
    cb._MEM_CACHE["t:exp"] = (0.0, 1, "x")  # 过去的时间戳
    assert cb.cache_get("t:exp") is None


def test_cache_clear_prefix(monkeypatch):
    monkeypatch.setattr(cb, "_REDIS_ENABLED", False)
    monkeypatch.setattr(cb, "_redis", None)
    cb._MEM_CACHE.clear()
    cb.cache_set("em:news:600519:10", [1], ttl=60)
    cb.cache_set("em:profile:600519", [2], ttl=60)
    cb.cache_set("other:1", [3], ttl=60)
    n = cb.cache_clear_prefix("em:")
    assert n == 2
    assert cb.cache_get("em:news:600519:10") is None
    assert cb.cache_get("other:1") == [3]


def test_cache_stats_shape():
    s = cb.cache_stats()
    assert "redis_enabled" in s
    assert "mem_entries" in s
    assert "redis_connected" in s


# ════════════════════════════════════════════════════════════
# em_sources
# ════════════════════════════════════════════════════════════

def test_em_market_code():
    assert _em_market_code("600519") == "SH600519"
    assert _em_market_code("000001") == "SZ000001"
    assert _em_market_code("300750") == "SZ300750"
    assert _em_market_code("688981") == "SH688981"


def test_fetch_news_direct_fail_closed(monkeypatch):
    """网络失败 → 返回 []（fail-closed，不抛异常）。"""
    import laap.paper_trading.em_sources as ems

    def _boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(ems, "_with_retry", _boom)
    monkeypatch.setattr(cb, "_REDIS_ENABLED", False)
    monkeypatch.setattr(cb, "_redis", None)
    cb._MEM_CACHE.clear()
    assert fetch_news_direct("600519", max_results=3) == []


def test_fetch_news_direct_jsonp_parse(monkeypatch):
    """jsonp 响应正确解包。"""
    import laap.paper_trading.em_sources as ems

    fake_resp = (
        'jQuery3510({"result": {"cmsArticleWebOld": ['
        '{"title": "<em>贵州</em>茅台提价", "date": "2026-08-15 10:00:00",'
        ' "content": "公司发布公告", "url": "http://x.com/1", "mediaName": "界面新闻"},'
        '{"title": "白酒板块走强", "date": "2026-08-14 09:00:00",'
        ' "content": "机构看好", "url": "http://x.com/2", "mediaName": "财联社"}'
        ']}})'
    )

    class _FakeResp:
        text = fake_resp

        def raise_for_status(self):
            pass

    def _fake_get(*a, **k):
        return _FakeResp()

    monkeypatch.setattr(ems.requests, "get", _fake_get)
    monkeypatch.setattr(cb, "_REDIS_ENABLED", False)
    monkeypatch.setattr(cb, "_redis", None)
    cb._MEM_CACHE.clear()
    out = fetch_news_direct("600519", max_results=5)
    assert len(out) == 2
    assert out[0]["title"] == "贵州茅台提价"  # <em> 标签已去除
    assert out[0]["source"] == "界面新闻"
    assert out[1]["title"] == "白酒板块走强"


def test_fetch_profile_direct_cache_roundtrip(monkeypatch):
    """资料直连结果入 redis/内存缓存，二次读取命中。"""
    import laap.paper_trading.em_sources as ems

    calls = {"n": 0}

    def _fake_do(*a, **k):
        calls["n"] += 1
        return {"jbzl": [{"ORG_NAME": "贵州茅台", "REG_CAPITAL": "125008"}],
                "fxxg": [{"LISTING_DATE": "2001-08-27 00:00:00"}]}

    monkeypatch.setattr(ems, "_with_retry", _fake_do)
    monkeypatch.setattr(cb, "_REDIS_ENABLED", False)
    monkeypatch.setattr(cb, "_redis", None)
    cb._MEM_CACHE.clear()

    p1 = ems.fetch_profile_direct("600519")
    p2 = ems.fetch_profile_direct("600519")
    assert calls["n"] == 1  # 第二次命中缓存，未再请求
    assert p1 is not None and p1["company_name"] == "贵州茅台"
    assert p2 == p1


# ════════════════════════════════════════════════════════════
# em_reports 缓存键
# ════════════════════════════════════════════════════════════

def test_reports_cache_key(monkeypatch):
    """研报缓存键存在且第二次命中。"""
    import laap.paper_trading.em_reports as emr

    calls = {"n": 0}

    class _FakeResp:
        def __init__(self, n):
            self._n = n

        def raise_for_status(self):
            pass

        def json(self):
            calls["n"] += 1
            return {"TotalPage": 1, "data": [
                {"title": f"研报{i}_{self._n}", "stockCode": "600519",
                 "publishDate": "2026-08-15 10:00:00", "orgName": "中信证券"}
                for i in range(3)]}

    def _fake_get(*a, **k):
        return _FakeResp(calls["n"])

    monkeypatch.setattr(emr.requests, "get", _fake_get)
    monkeypatch.setattr(cb, "_REDIS_ENABLED", False)
    monkeypatch.setattr(cb, "_redis", None)
    cb._MEM_CACHE.clear()

    r1 = emr.fetch_reports("600519", max_results=3)
    r2 = emr.fetch_reports("600519", max_results=3)
    assert calls["n"] == 1  # 缓存命中，第二次未请求
    assert len(r1) == 3
    assert r1 == r2
