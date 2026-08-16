# -*- coding: utf-8 -*-
"""news_intel.py 数据获取层测试（akshare stub 注入）。"""
import pandas as pd
import pytest

from laap.paper_trading import news_intel
from laap.paper_trading.news_intel import (
    NewsItem, StockProfile, ResearchReport,
    fetch_stock_news, fetch_stock_profile, fetch_research_reports,
    summarize_news, _inject_ak, _cache_get, _cache_put, _CACHE,
    _fmt_published,
)


class TestFmtPublished:
    """发布时间归一化（publish_time 对齐基础，离线确定性）。"""

    def test_unix_timestamp(self):
        # 1786890948 ≈ 2026-08-16（新浪 ctime 格式）
        out = _fmt_published(1786890948)
        assert out.startswith("2026-08-16")

    def test_timestamp_string(self):
        out = _fmt_published("1786890948")
        assert out.startswith("2026-08-16")

    def test_datetime_string_unchanged(self):
        assert _fmt_published("2026-08-15 10:11:51") == "2026-08-15 10:11:51"

    def test_t_separator(self):
        assert _fmt_published("2026-08-15T10:11:51") == "2026-08-15 10:11:51"

    def test_empty_and_garbage(self):
        assert _fmt_published("") == ""
        assert _fmt_published(None) == ""
        assert _fmt_published("not-a-date") == "not-a-date"


class _StubAk:
    """stub akshare 模块（可配置各接口失败）。"""

    def __init__(self):
        self.individual_fail = False
        self.cninfo_fail = False
        self.news_fail = False
        self.report_fail = False

    def stock_news_em(self, symbol):
        if self.news_fail:
            raise RuntimeError("net fail")
        return pd.DataFrame([
            {"新闻标题": "茅台提价", "新闻内容": "贵州茅台宣布核心产品提价 10%",
             "发布时间": "2026-08-15 09:00", "文章来源": "证券时报",
             "新闻链接": "http://x/1"},
            {"新闻标题": "茅台提价", "新闻内容": "（重复标题）",
             "发布时间": "2026-08-15 09:00", "文章来源": "证券时报",
             "新闻链接": "http://x/1"},  # 去重
            {"新闻标题": "白酒板块回调", "新闻内容": "板块整体回落",
             "发布时间": "2026-08-14", "文章来源": "财联社",
             "新闻链接": "http://x/2"},
        ])

    def stock_individual_info_em(self, symbol):
        if self.individual_fail:
            raise RuntimeError("individual fail")
        return pd.DataFrame([
            {"item": "总市值", "value": "15000亿"},
            {"item": "流通市值", "value": "14500亿"},
            {"item": "总股本", "value": "12.56亿"},
            {"item": "流通股本", "value": "12.56亿"},
            {"item": "行业", "value": "酿酒行业"},
            {"item": "上市时间", "value": "2001-08-27"},
        ])

    def stock_profile_cninfo(self, symbol):
        if self.cninfo_fail:
            raise RuntimeError("cninfo fail")
        return pd.DataFrame([{
            "公司全称": "贵州茅台酒股份有限公司",
            "主营业务": "茅台酒及系列酒的生产与销售",
            "所属行业": "食品饮料",
            "上市日期": "2001-08-27",
            "注册资金": "1256197800",
        }])

    def stock_research_report_em(self, symbol):
        if self.report_fail:
            raise RuntimeError("report fail")
        return pd.DataFrame([
            {"报告名称": "茅台目标价上调", "评级": "买入", "机构": "中信证券",
             "目标价(元)": "1800.0", "EPS(元)": "55.0", "PE": "30.0",
             "报告日期": "2026-08-10", "报告链接": "http://r/1"},
            {"报告名称": "短期承压", "评级": "增持", "机构": "国泰君安",
             "目标价(元)": "1650.0", "EPS(元)": "52.0", "PE": "28.0",
             "报告日期": "2026-08-12", "报告链接": "http://r/2"},
        ])


@pytest.fixture(autouse=True)
def _stub_ak(monkeypatch):
    ak = _StubAk()
    _inject_ak(ak)
    _CACHE.clear()
    # 限制源链到 akshare 可 stub 的源，避免沙箱测试走真实网络（sina/cls/em_profile）
    monkeypatch.setenv("NEWS_SOURCES", "eastmoney")
    monkeypatch.setenv("PROFILE_SOURCES", "individual_info,cninfo")
    monkeypatch.setenv("REPORT_SOURCES", "eastmoney")
    yield ak
    _inject_ak(None)
    _CACHE.clear()


def test_fetch_stock_news_parse_dedupe_filter():
    items, meta = fetch_stock_news("600519", max_results=10)
    assert meta["used_fallback"] is False
    # 去重后 2 条（标题+时间重复的去掉一条）
    assert len(items) == 2
    assert all(isinstance(i, NewsItem) for i in items)
    assert items[0].title == "茅台提价"
    assert items[0].symbol == "600519"


def test_fetch_stock_news_keyword_filter():
    items, _ = fetch_stock_news("600519", focus_keywords=["提价"])
    assert len(items) == 1
    assert "提价" in items[0].title


def test_fetch_stock_news_fail_fallback(_stub_ak):
    _stub_ak.news_fail = True
    items, meta = fetch_stock_news("600519")
    assert items == []
    assert meta["used_fallback"] is True


def test_fetch_news_cache_hit():
    _cache_put("news:600519:10:", [NewsItem("600519", "cached")])
    items, meta = fetch_stock_news("600519")
    assert meta["source"] == "cache"
    assert items[0].title == "cached"


def test_fetch_stock_profile_individual():
    prof, meta = fetch_stock_profile("600519")
    assert prof is not None
    assert prof.total_mv == pytest.approx(15000.0)  # 亿
    assert prof.industry == "酿酒行业"
    assert prof.source == "individual_info"
    assert meta["used_fallback"] is False


def test_fetch_stock_profile_cninfo_fallback(_stub_ak):
    _stub_ak.individual_fail = True
    prof, meta = fetch_stock_profile("600519")
    assert prof is not None
    assert prof.company_name == "贵州茅台酒股份有限公司"
    assert prof.main_business
    assert prof.source == "cninfo"
    assert meta["used_fallback"] is True


def test_fetch_stock_profile_all_fail(_stub_ak):
    _stub_ak.individual_fail = True
    _stub_ak.cninfo_fail = True
    prof, meta = fetch_stock_profile("600519")
    assert prof is None
    assert meta["used_fallback"] is True


def test_fetch_research_reports():
    reps, meta = fetch_research_reports("600519", max_results=2)
    assert len(reps) == 2
    assert reps[0].rating == "买入"
    assert reps[0].target_price == pytest.approx(1800.0)
    assert reps[0].eps == pytest.approx(55.0)
    assert reps[0].org == "中信证券"
    assert meta["used_fallback"] is False


def test_fetch_research_reports_fail(_stub_ak):
    _stub_ak.report_fail = True
    reps, meta = fetch_research_reports("600519")
    assert reps == []
    assert meta["used_fallback"] is True


def test_summarize_news_with_local_tool():
    item = NewsItem("600519", "茅台提价", "核心产品提价 10%")
    summary, h = summarize_news(item, local_tool=lambda text: "本地摘要: " + text[:10])
    assert summary.startswith("本地摘要:")
    assert len(h) == 40  # sha1 hex


def test_summarize_news_fallback_and_determinism():
    item = NewsItem("600519", "茅台提价", "核心产品提价 10%")
    s1, h1 = summarize_news(item)
    s2, h2 = summarize_news(item)
    assert s1 == s2
    assert h1 == h2
    assert "茅台提价" in s1


def test_models_roundtrip():
    i = NewsItem("600519", "t", content="c")
    assert NewsItem.from_dict(i.to_dict()).title == "t"
    p = StockProfile("600519", total_mv=1.0)
    assert StockProfile.from_dict(p.to_dict()).total_mv == 1.0
    r = ResearchReport("600519", title="r", rating="买入")
    assert ResearchReport.from_dict(r.to_dict()).rating == "买入"


def test_with_retry_succeeds_after_transient_failures():
    """_with_retry 对瞬态失败重试后成功。"""
    from laap.paper_trading.news_intel import _with_retry
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("RemoteDisconnected")
        return "ok"

    assert _with_retry(flaky, retries=3, delay=0.01) == "ok"
    assert calls["n"] == 3


def test_with_retry_exhausts_then_raises():
    from laap.paper_trading.news_intel import _with_retry
    calls = {"n": 0}

    def always_fail():
        calls["n"] += 1
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        _with_retry(always_fail, retries=2, delay=0.01)
    assert calls["n"] == 2


def test_fetch_profile_retries_individual_before_fallback(_stub_ak):
    """individual_info 前 2 次失败、第 3 次成功 → 命中 individual_info 源（不落 cninfo 兜底）。"""
    calls = {"n": 0}
    orig = _stub_ak.stock_individual_info_em

    def flaky(symbol):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("RemoteDisconnected")
        return orig(symbol)
    _stub_ak.stock_individual_info_em = flaky
    prof, meta = fetch_stock_profile("600519")
    assert prof.source == "individual_info"
    assert meta["used_fallback"] is False
    assert calls["n"] == 3


# ════════════════════════════════════════════════════════════
# 新源：sina/cls 新闻、em_profile 资料、sina/cls 研报（mock HTTP）
# ════════════════════════════════════════════════════════════

def test_news_sina_parse(monkeypatch):
    """新浪新闻：mock roll API → 解析 + symbol 过滤。"""
    import json as _json
    from laap.paper_trading import news_intel
    payload = {"result": {"data": {"list": [
        {"title": "贵州茅台(600519)提价10%", "intro": "核心产品提价", "ctime": "2026-08-15",
         "url": "http://sina/1"},
        {"title": "某公司并购", "intro": "无关新闻", "ctime": "2026-08-15",
         "url": "http://sina/2"},
    ]}}}
    monkeypatch.setattr(news_intel, "_http_get_json",
                        lambda *a, **k: payload)
    items = news_intel._news_sina("600519", 5, None)
    assert len(items) == 1
    assert items[0].source == "sina"
    assert "茅台" in items[0].title


def test_news_cls_parse(monkeypatch):
    """财联社电报：mock updateTelegraphList → 解析 + symbol 过滤。"""
    from laap.paper_trading import news_intel
    payload = {"data": {"roll_data": [
        {"title": "贵州茅台(600519)公告", "content": "提价10%", "ctime": "2026-08-15",
         "id": "100"},
        {"title": "无关", "content": "某某", "ctime": "2026-08-15", "id": "101"},
    ]}}
    monkeypatch.setattr(news_intel, "_http_get_json",
                        lambda *a, **k: payload)
    items = news_intel._news_cls("600519", 5, None)
    assert len(items) == 1
    assert items[0].source == "cls"
    assert items[0].url == "https://www.cls.cn/detail/100"


def test_profile_em_parse(monkeypatch):
    """东财 F10 em_profile：mock PageAjax → 解析行业/主营。"""
    import os
    from laap.paper_trading import news_intel
    os.environ["PROFILE_SOURCES"] = "em_profile"
    payload = {"jbzl": [{"INDUSTRYCSRC1": "白酒",
                         "MAINBUSINESS": "茅台酒生产销售",
                         "TOTALCAPITAL": "1256000000",
                         "ORGNAME": "贵州茅台酒股份有限公司"}]}
    monkeypatch.setattr(news_intel, "_http_get_json",
                        lambda *a, **k: payload)
    prof, meta = news_intel.fetch_stock_profile("600519")
    os.environ.pop("PROFILE_SOURCES", None)
    assert prof is not None
    assert prof.industry == "白酒"
    assert prof.main_business
    assert prof.source == "em_profile"
    assert meta["used_fallback"] is False  # em_profile 是链首位 → 非回退


def test_reports_sina_parse(monkeypatch):
    """新浪研报：mock HTML → 正则提取报告标题。"""
    from laap.paper_trading import news_intel
    html = ('<a>某公司公告</a><a>贵州茅台目标价上调研报</a><a>增持评级</a>')
    monkeypatch.setattr(news_intel, "_http_get_text",
                        lambda *a, **k: html)
    items = news_intel._reports_sina("600519", 5)
    assert len(items) >= 1
    assert items[0].source == "sina"


def test_reports_cls_parse(monkeypatch):
    """财联社研报：mock telegraphList research → 解析。"""
    from laap.paper_trading import news_intel
    payload = {"data": {"roll_data": [
        {"title": "贵州茅台研报", "ctime": "2026-08-15", "id": "200"}]}}
    monkeypatch.setattr(news_intel, "_http_get_json",
                        lambda *a, **k: payload)
    items = news_intel._reports_cls("600519", 5)
    assert len(items) == 1
    assert items[0].source == "cls"


# ════════════════════════════════════════════════════════════
# 搜索型新闻源：tavily / bocha / minimax（mock POST）
# ════════════════════════════════════════════════════════════

def test_news_tavily_parse(monkeypatch):
    import os
    from laap.paper_trading import news_intel
    monkeypatch.setenv("TAVILY_API_KEYS", "test-key")
    payload = {"results": [
        {"title": "贵州茅台(600519)提价", "content": "核心产品提价10%",
         "url": "http://tavily/1"},
        {"title": "无关", "content": "其他", "url": "http://tavily/2"},
    ]}
    monkeypatch.setattr(news_intel, "_http_post_json",
                        lambda *a, **k: payload)
    items = news_intel._news_tavily("600519", 5, None)
    monkeypatch.delenv("TAVILY_API_KEYS", raising=False)
    assert len(items) == 1
    assert items[0].source == "tavily"
    assert "茅台" in items[0].title


def test_news_bocha_parse(monkeypatch):
    import os
    from laap.paper_trading import news_intel
    monkeypatch.setenv("BOCHA_API_KEYS", "test-key")
    payload = {"data": {"webPages": {"value": [
        {"name": "贵州茅台(600519)公告", "summary": "提价10%",
         "url": "http://bocha/1"},
        {"name": "无关", "summary": "其他", "url": "http://bocha/2"},
    ]}}}
    monkeypatch.setattr(news_intel, "_http_post_json",
                        lambda *a, **k: payload)
    items = news_intel._news_bocha("600519", 5, None)
    monkeypatch.delenv("BOCHA_API_KEYS", raising=False)
    assert len(items) == 1
    assert items[0].source == "bocha"
    assert items[0].url == "http://bocha/1"


def test_news_minimax_parse(monkeypatch):
    import os
    from laap.paper_trading import news_intel
    monkeypatch.setenv("MINIMAX_API_KEYS", "test-key")
    payload = {"choices": [{"message": {"content":
        "[{'title': '贵州茅台(600519)提价', 'content': '提价10%', 'url': 'http://mm/1'}]"}}]}
    monkeypatch.setattr(news_intel, "_http_post_json",
                        lambda *a, **k: payload)
    items = news_intel._news_minimax("600519", 5, None)
    monkeypatch.delenv("MINIMAX_API_KEYS", raising=False)
    assert len(items) == 1
    assert items[0].source == "minimax"


def test_json_parse_list_loose():
    from laap.paper_trading.news_intel import _json_parse_list
    assert _json_parse_list("[{'a': 1}]") == [{"a": 1}]
    assert _json_parse_list("垃圾") == []


def test_tushare_ohlcv_parse(monkeypatch):
    """Tushare daily K线解析（mock urllib urlopen）。

    2026-08-16 起 _load_tushare_ohlcv 按 Tushare 真实返回语义处理：
    items 为**降序（最新在前）**，取最近 N 条后反转成升序。
    mock 数据按降序给出（20260815 在前），期望升序末条 close=102。
    """
    import json as _json
    import os
    import urllib.request
    from laap.paper_trading import kline_source as ks
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    payload = _json.dumps({"data": {
        "fields": ["trade_date", "open", "high", "low", "close", "vol"],
        "items": [["20260815", 101.0, 103.0, 100.0, 102.0, 1200.0],   # 最新在前
                  ["20260814", 100.0, 102.0, 99.0, 101.0, 1000.0]]}}).encode()

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return payload

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    ohlcv = ks._load_tushare_ohlcv("600519", 10)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    assert len(ohlcv) == 2
    assert ohlcv[-1][1] == 102.0  # close（升序末条 = 最新 20260815）
    assert ohlcv[0][1] == 101.0   # 升序首条 = 20260814


# ── Tushare 新闻快讯（_news_tushare）──
def _ts_news_payload(items):
    """构造 Tushare news API 成功响应（code=0）。"""
    return {"code": 0, "msg": None, "data": {
        "fields": ["datetime", "title", "content"], "items": items}}


def test_news_tushare_parse(monkeypatch):
    """Tushare 新闻快讯解析：命中 symbol 代码 → NewsItem(source=tushare)。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    payload = _ts_news_payload([
        ["2026-08-15 09:00:00", "提价公告", "600519 贵州茅台宣布核心产品提价 10%"],
        ["2026-08-15 08:00:00", "大盘综述", "两市低开高走"],
    ])
    monkeypatch.setattr(news_intel, "_http_post_json", lambda *a, **k: payload)
    items = news_intel._news_tushare("600519", 10, None)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    assert len(items) == 1
    assert items[0].source == "tushare"
    assert items[0].title == "提价公告"
    assert items[0].published_at == "2026-08-15 09:00:00"


def test_news_tushare_focus_keyword(monkeypatch):
    """不含代码但含 focus_keyword → 命中。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    payload = _ts_news_payload([
        ["2026-08-15 09:00:00", "白酒板块异动", "白酒股集体走强，机构加仓"],
        ["2026-08-15 08:00:00", "新能源", "光伏板块反弹"],
    ])
    monkeypatch.setattr(news_intel, "_http_post_json", lambda *a, **k: payload)
    items = news_intel._news_tushare("600519", 10, ["白酒"])
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    assert len(items) == 1
    assert items[0].title == "白酒板块异动"


def test_news_tushare_no_token(monkeypatch):
    """TUSHARE_TOKEN 缺失 → 抛错供多源链回退。"""
    from laap.paper_trading import news_intel as ni
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN empty"):
        ni._news_tushare("600519", 10, None)


def test_news_tushare_permission_error(monkeypatch):
    """Tushare 返回权限错误 code≠0 → 抛错（fail-closed 回退下一源）。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    monkeypatch.setattr(news_intel, "_http_post_json",
                        lambda *a, **k: {"code": 2002, "msg": "权限不足", "data": None})
    with pytest.raises(RuntimeError, match="tushare news error"):
        news_intel._news_tushare("600519", 10, None)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)


def test_news_tushare_no_match(monkeypatch):
    """返回数据但不含 symbol/关键词 → 抛错。"""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    monkeypatch.setattr(news_intel, "_http_post_json",
                        lambda *a, **k: _ts_news_payload(
                            [["2026-08-15 09:00:00", "大盘综述", "两市低开高走"]]))
    with pytest.raises(RuntimeError, match="no news matched"):
        news_intel._news_tushare("600519", 10, None)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)


def test_fetch_stock_news_via_tushare(monkeypatch):
    """NEWS_SOURCES 仅含 tushare → fetch_stock_news 走 Tushare 快讯源。"""
    monkeypatch.setenv("NEWS_SOURCES", "tushare")
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    payload = _ts_news_payload([["2026-08-15 09:00:00", "提价公告", "600519 提价 10%"]])
    monkeypatch.setattr(news_intel, "_http_post_json", lambda *a, **k: payload)
    items, meta = news_intel.fetch_stock_news("600519", max_results=10)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    assert len(items) == 1
    assert items[0].source == "tushare"
    assert meta["source"] == "tushare"
    assert meta["used_fallback"] is False  # tushare 为链首位，未回退


# ════════════════════════════════════════════════════════════
# 代码规范化（'600511.SH'/'SH600511' → 裸 6 位，东财接口只收裸代码）
# ════════════════════════════════════════════════════════════

def test_normalize_symbol():
    from laap.paper_trading.news_intel import _normalize_symbol
    assert _normalize_symbol("600511.SH") == "600511"
    assert _normalize_symbol("000523.SZ") == "000523"
    assert _normalize_symbol("600511.sh") == "600511"   # 小写
    assert _normalize_symbol("SH600511") == "600511"    # 前缀式
    assert _normalize_symbol("sz000523") == "000523"
    assert _normalize_symbol("600519") == "600519"      # 裸代码原样
    assert _normalize_symbol("") == ""


def test_fetch_reports_normalizes_suffixed_symbol(_stub_ak, monkeypatch):
    """'600114.SH' → 东财接口收到裸 '600114'，返回模型 symbol 为裸代码。"""
    seen = {}
    orig = _stub_ak.stock_research_report_em

    def _wrap(symbol):
        seen["symbol"] = symbol
        return orig(symbol)
    monkeypatch.setattr(_stub_ak, "stock_research_report_em", _wrap)
    reports, meta = fetch_research_reports("600114.SH", max_results=5)
    assert seen["symbol"] == "600114"
    assert meta["used_fallback"] is False
    assert reports and reports[0].symbol == "600114"


def test_fetch_profile_normalizes_suffixed_symbol(_stub_ak, monkeypatch):
    seen = {}
    orig = _stub_ak.stock_individual_info_em

    def _wrap(symbol):
        seen["symbol"] = symbol
        return orig(symbol)
    monkeypatch.setattr(_stub_ak, "stock_individual_info_em", _wrap)
    prof, meta = fetch_stock_profile("600519.SH")
    assert seen["symbol"] == "600519"
    assert prof is not None and prof.symbol == "600519"


def test_fetch_news_normalizes_suffixed_symbol(_stub_ak, monkeypatch):
    seen = {}
    orig = _stub_ak.stock_news_em

    def _wrap(symbol):
        seen["symbol"] = symbol
        return orig(symbol)
    monkeypatch.setattr(_stub_ak, "stock_news_em", _wrap)
    items, meta = fetch_stock_news("600519.SH")
    assert seen["symbol"] == "600519"
    assert items and items[0].symbol == "600519"


def test_fetch_reports_no_data_flag(_stub_ak, monkeypatch):
    """主源无数据（NoDataError）→ meta.no_data=True（区别于网络降级）。"""
    from laap.paper_trading.data_sources import NoDataError

    def _empty(symbol, max_results):
        raise NoDataError(f"eastmoney no reports for {symbol}")
    monkeypatch.setattr(news_intel, "_reports_eastmoney", _empty)
    reports, meta = fetch_research_reports("600519")
    assert reports == []
    assert meta["no_data"] is True
    assert meta["used_fallback"] is True
