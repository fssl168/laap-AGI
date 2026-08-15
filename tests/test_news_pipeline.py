# -*- coding: utf-8 -*-
"""news_pipeline.py 管线 E2E 测试（stub 数据源 + fake executor）。"""
import sqlite3
import pytest

from laap.paper_trading import news_pipeline as np_mod
from laap.paper_trading.news_pipeline import NewsSignalPipeline, NewsSignalWorker, is_market_session_time
from laap.paper_trading.news_intel import NewsItem, ResearchReport
from laap.paper_trading.news_verifier import TechState
from datetime import datetime


class _FakeDB:
    def __init__(self, path):
        self._path = str(path)
        c = sqlite3.connect(self._path)
        c.executescript("""
        CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, symbol TEXT);
        CREATE TABLE IF NOT EXISTS outcomes (trade_id TEXT PRIMARY KEY, pnl_pct REAL);
        """)
        c.commit()
        c.close()

    def conn(self):
        c = sqlite3.connect(self._path)
        c.row_factory = sqlite3.Row
        return c


class _FakeMarket:
    """非降级行情源（get_price 返回固定价 + used_fallback=False）。"""

    def __init__(self, price=100.0, degraded=False):
        self.price = price
        self.degraded = degraded

    def get_price(self, symbol, ts=None):
        if self.degraded:
            return 99.0, {"source": "stub", "used_fallback": True}
        return self.price, {"source": "fake", "used_fallback": False}


class _FakeLoop:
    """最小 loop：提供 db / trading_self / ledger.cash / market（供 pipeline 状态读取）。"""

    class _Ledger:
        cash = 1_000_000.0

        def stats(self):
            return {"total": 1_000_000.0}

        def positions(self):
            return []

    def __init__(self, db, market=None):
        self.db = db
        self.ledger = self._Ledger()
        self.trading_self = None
        self.market = market or _FakeMarket()


@pytest.fixture
def pipe(tmp_path):
    db = _FakeDB(tmp_path / "pipe.db")
    loop = _FakeLoop(db)
    calls = {}

    def fake_executor(**kw):
        calls.update(kw)
        return {"decision_id": "dec_1", "order_id": "ord_1", "trade_id": "trd_1"}

    p = NewsSignalPipeline(loop=loop, db=db, executor=fake_executor)
    p._calls = calls
    return p


def _bullish_llm(prompt, system="", max_tokens=800):
    return {"verdict": "genuine_bullish", "confidence": 0.9,
            "reasons": ["利好"], "impact": "短期利好"}


def _neutral_llm(prompt, system="", max_tokens=800):
    return {"verdict": "neutral", "confidence": 0.4, "reasons": [],
            "impact": ""}


def _fake_bullish_news():
    return [NewsItem("600519", "茅台提价", "核心产品提价 10%", source="公告",
                     published_at="2026-08-15")], {"source": "stub", "used_fallback": False}


def _fake_bullish_reports():
    return [ResearchReport("600519", title="买入", rating="买入",
                           target_price=120.0)], {"source": "stub", "used_fallback": False}


def _patch_sources(monkeypatch, news=None, reports=None):
    news = news if news is not None else ([], {"source": "stub", "used_fallback": True})
    reports = reports if reports is not None else ([], {"source": "stub", "used_fallback": True})
    monkeypatch.setattr(np_mod, "fetch_stock_news", lambda *a, **k: news)
    monkeypatch.setattr(np_mod, "fetch_stock_profile",
                        lambda *a, **k: (None, {"source": "stub", "used_fallback": True}))
    monkeypatch.setattr(np_mod, "fetch_research_reports", lambda *a, **k: reports)
    monkeypatch.setattr(np_mod, "compute_tech_state",
                        lambda *a, **k: TechState("600519", rsi=40.0, close=100.0,
                                                  atr=2.0, ma20=95.0, prev_close=99.0,
                                                  change_pct=0.01))


def test_pipeline_true_bullish_dispatches(pipe, monkeypatch):
    _patch_sources(monkeypatch, news=_fake_bullish_news(),
                   reports=_fake_bullish_reports())
    pipe.llm_call = _bullish_llm
    r = pipe.run("600519", auto_order=True)
    assert r["dispatched"] is True
    assert r["silent"] is False
    assert pipe._calls.get("symbol") == "600519"
    assert pipe._calls.get("quantity", 0) >= 100
    assert "茅台" in pipe._calls.get("rationale", "")
    assert "decision_id" in r and r["decision_id"]


def test_pipeline_fake_news_silent(pipe, monkeypatch):
    fake_news = [NewsItem("600519", "网传并购", "公司澄清不属实")], {"source": "stub"}
    _patch_sources(monkeypatch, news=fake_news, reports=_fake_bullish_reports())
    pipe.llm_call = lambda p, system="", max_tokens=800: {
        "verdict": "fake_news", "confidence": 0.8, "reasons": [], "impact": ""}
    r = pipe.run("600519")
    assert r["dispatched"] is False
    assert r["silent"] is True
    assert pipe._calls == {}  # 未下单


def test_pipeline_no_news_silent_no_llm(pipe, monkeypatch):
    _patch_sources(monkeypatch, news=([], {"source": "stub", "used_fallback": True}))
    calls = []
    pipe.llm_call = lambda *a, **k: calls.append(1) or {"verdict": "genuine_bullish", "confidence": 0.9,
                                                        "reasons": [], "impact": ""}
    r = pipe.run("600519")
    assert r["news_count"] == 0
    assert r["dispatched"] is False
    assert calls == []  # D1：无新闻 → 0 次 LLM 调用


def test_pipeline_auto_order_false_only_plan(pipe, monkeypatch):
    _patch_sources(monkeypatch, news=_fake_bullish_news(),
                   reports=_fake_bullish_reports())
    pipe.llm_call = _bullish_llm
    r = pipe.run("600519", auto_order=False)
    assert r["dispatched"] is False
    assert pipe._calls == {}
    assert "plan" in r


def test_pipeline_degraded_price_no_dispatch(monkeypatch, tmp_path):
    """行情降级（无实时价/合成价）→ fail-closed 不下单（§3.1），仅出计划+留痕。"""
    db = _FakeDB(tmp_path / "degraded.db")
    loop = _FakeLoop(db, market=_FakeMarket(degraded=True))
    calls = {}
    pipe = NewsSignalPipeline(loop=loop, db=db,
                              executor=lambda **kw: calls.update(kw) or {
                                  "decision_id": "d", "order_id": "o", "trade_id": "t"})
    pipe.llm_call = _bullish_llm
    _patch_sources(monkeypatch, news=_fake_bullish_news(),
                   reports=_fake_bullish_reports())
    r = pipe.run("600519", auto_order=True)
    assert r["dispatched"] is False
    assert r["silent"] is True
    assert "行情降级" in (r.get("reason") or "")
    assert calls == {}                    # 未下单
    assert r["price_quality"]["used_fallback"] is True
    conn = pipe.db.conn()
    n = conn.execute("SELECT COUNT(*) FROM news_verdicts").fetchone()[0]
    conn.close()
    assert n >= 1                         # 判定仍留痕


def test_pipeline_risk_rejection(pipe, monkeypatch):
    _patch_sources(monkeypatch, news=_fake_bullish_news(),
                   reports=_fake_bullish_reports())
    pipe.llm_call = _bullish_llm
    # 注入"恒拒绝"的风控门，验证接线：拒绝 → 0 订单 + risk_rejections 留痕
    # （风控门本身的 R1-R5 逻辑已由 test_risk_gate.py 单测覆盖）
    class _RejectingGate:
        def check_signal(self, *a, **k):
            return False, "R2", "单票超仓(测试注入)"
    pipe.risk_gate = _RejectingGate()
    r = pipe.run("600519")
    assert r["dispatched"] is False
    assert pipe._calls == {}
    assert "plan" in r
    conn = pipe.db.conn()
    rows = conn.execute("SELECT * FROM risk_rejections").fetchall()
    assert len(rows) >= 1
    assert rows[0]["rule_id"] == "R2"
    conn.close()


def test_pipeline_persists_verdicts(pipe, monkeypatch):
    _patch_sources(monkeypatch, news=_fake_bullish_news(),
                   reports=_fake_bullish_reports())
    pipe.llm_call = _bullish_llm
    pipe.run("600519")
    conn = pipe.db.conn()
    rows = conn.execute("SELECT * FROM news_verdicts").fetchall()
    assert len(rows) >= 1
    assert rows[0]["symbol"] == "600519"
    assert rows[0]["verdict"] == "genuine_bullish"
    conn.close()


def test_is_market_session_time():
    assert is_market_session_time(datetime(2026, 8, 17, 10, 0)) is True
    assert is_market_session_time(datetime(2026, 8, 17, 13, 30)) is True
    assert is_market_session_time(datetime(2026, 8, 17, 11, 45)) is False  # 午休
    assert is_market_session_time(datetime(2026, 8, 17, 16, 0)) is False   # 收盘后
    assert is_market_session_time(datetime(2026, 8, 17, 9, 0)) is False    # 盘前


def test_worker_disabled_by_default(monkeypatch):
    # 显式清除 env，确保断言基于"默认关"（quant_config 动态读 env，需隔离）
    monkeypatch.delenv("LAAP_NEWS_INTRADAY", raising=False)
    w = NewsSignalWorker(NewsSignalPipeline(db=None), symbols=["600519"])
    assert w.enabled is False
    assert w.start() is False  # 默认关，不启动


def test_worker_stats(monkeypatch):
    # enabled=True 但 freshness_ok 失败 → start 返回 True（仍启动，但告警跳过本轮）
    w = NewsSignalWorker(NewsSignalPipeline(db=None), symbols=["600519"],
                         interval=60, enabled=True)
    monkeypatch.setattr(w, "_freshness_ok", lambda: True)
    monkeypatch.setattr(w, "_is_trading_day", lambda: False)
    assert w.start() is True
    assert w.is_running is True
    w.stop()
    assert w.is_running is False
