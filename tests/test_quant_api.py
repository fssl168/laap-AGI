"""量化闭环 /v1/quant/* 路由测试。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


def test_quant_routes_registered():
    """create_app 应注册 6 条 /v1/quant/* 路由。"""
    from laap_brain.api import create_app
    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    expected = {
        "/v1/quant/decisions",
        "/v1/quant/lessons",
        "/v1/quant/evolve",
        "/v1/quant/evolve/approve",
        "/v1/quant/evolve/reject",
        "/v1/quant/evolve/audit",
    }
    assert expected.issubset(routes)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Req:
    def __init__(self, body=None, query=None):
        self._body = body or {}
        self._query = query or {}

    async def json(self):
        return self._body

    @property
    def query(self):
        return self._query


def test_decision_record_missing_symbol(monkeypatch):
    """缺 symbol → 400。"""
    from laap_brain.api import handle_quant_decision_record
    resp = _run(handle_quant_decision_record(_Req(body={"action": "buy"})))
    assert resp.status == 400
    assert "symbol" in resp.text


def test_decision_record_ok(monkeypatch, tmp_path):
    """正常决策留痕 → 200 + 落 decisions 表。"""
    import laap_brain.api as api
    from laap.paper_trading.db import PaperDB
    db = PaperDB(db_path=str(tmp_path / "pt.db"))
    monkeypatch.setattr(api, "_quant_db", db)

    resp = _run(api.handle_quant_decision_record(_Req(body={
        "symbol": "600519", "action": "buy", "rationale": "测试",
    })))
    assert resp.status == 200
    conn = db.conn()
    n = conn.execute("SELECT COUNT(*) FROM decisions WHERE symbol='600519'").fetchone()[0]
    conn.close()
    assert n == 1


def test_lessons_empty(monkeypatch, tmp_path):
    """无教训 → 200 + 空 lessons 列表。"""
    import laap_brain.api as api
    from laap.paper_trading.db import PaperDB
    db = PaperDB(db_path=str(tmp_path / "pt.db"))
    monkeypatch.setattr(api, "_quant_db", db)

    resp = _run(api.handle_quant_lessons(_Req()))
    assert resp.status == 200


def test_evolve_no_engine(monkeypatch):
    """无 quant engine → 500。"""
    import laap_brain.api as api
    monkeypatch.setattr(api, "_get_quant_engine", lambda: None)
    resp = _run(api.handle_quant_evolve(_Req()))
    assert resp.status == 500


def test_evolve_approve_missing_id(monkeypatch):
    """批准缺 mutation_id → 400。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_evolve_approve(_Req(body={})))
    assert resp.status == 400
    assert "mutation_id" in resp.text


# ════════════════════════════════════════════════════════════
# TradingSelf 状态 + evolve_params apply_code
# ════════════════════════════════════════════════════════════

class _StubTradingSelf:
    def trading_identity(self):
        return {"risk_appetite": 0.5, "discipline": 0.7,
                "position_scale_max": 0.6, "stop_loss_default": 0.09}
    def identity_statement(self):
        return "我是测试自我"
    personality = {"preset_name": "测试", "traits": {"warmth": 0.5}}
    self_model = None
    memory = None


def test_quant_self_status_route_registered():
    """GET /v1/quant/self/status 已注册。"""
    from laap_brain.api import create_app
    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/self/status" in routes


def test_quant_self_status_ok(monkeypatch):
    """交易自我状态端点返回身份/人格/自我模型。"""
    import laap_brain.api as api
    monkeypatch.setattr(api, "_get_trading_self", lambda: _StubTradingSelf())
    resp = _run(api.handle_quant_self_status(_Req()))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert d["identity"] == "我是测试自我"
    assert "trading_identity" in d
    assert d["personality_preset"] == "测试"


def test_quant_self_status_unavailable(monkeypatch):
    import laap_brain.api as api
    monkeypatch.setattr(api, "_get_trading_self", lambda: None)
    resp = _run(api.handle_quant_self_status(_Req()))
    assert resp.status == 500


def test_evolve_params_apply_code_calls_apply(monkeypatch):
    """apply_code=true → 搜索结果落回代码（含 self_review 透传）。"""
    import laap_brain.api as api

    class _Q:
        def evolve_params(self, method="random", **kw):
            return {"best_params": {"fast_ma": 6}}
        def apply_params_to_code(self, params, rationale="", method="",
                                 self_review=True):
            return {"status": "self_blocked", "self_verdict": "reject"}

    monkeypatch.setattr(api, "_get_quant_engine", lambda: _Q())
    resp = _run(api.handle_quant_evolve_params(_Req(body={
        "method": "random", "apply_code": True, "self_review": True})))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert "code_application" in d
    assert d["code_application"]["status"] == "self_blocked"


# ════════════════════════════════════════════════════════════
# T2: /v1/quant/daily_cycle + /v1/quant/apply_params
# ════════════════════════════════════════════════════════════

def test_quant_daily_cycle_route_registered():
    from laap_brain.api import create_app
    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/daily_cycle" in routes
    assert "/v1/quant/apply_params" in routes


def test_quant_daily_cycle_ok(monkeypatch):
    """daily_cycle 调用 run_daily_cycle 并返回结果。"""
    import laap_brain.api as api

    class _Loop:
        def run_daily_cycle(self, symbols, params, ohlcv_map=None):
            return {"signals": [{"action": "hold"}], "net_value": {"total": 1e6},
                    "data_quality": {}}

    monkeypatch.setattr(api, "_get_paper_loop", lambda: _Loop())
    resp = _run(api.handle_quant_daily_cycle(_Req(body={"symbols": ["600519"]})))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert d["signals"][0]["action"] == "hold"


def test_quant_daily_cycle_unavailable(monkeypatch):
    import laap_brain.api as api
    monkeypatch.setattr(api, "_get_paper_loop", lambda: None)
    resp = _run(api.handle_quant_daily_cycle(_Req(body={})))
    assert resp.status == 500


def test_quant_apply_params_missing_params(monkeypatch):
    import laap_brain.api as api
    resp = _run(api.handle_quant_apply_params(_Req(body={})))
    assert resp.status == 400
    assert "params" in resp.text


def test_quant_apply_params_ok(monkeypatch):
    """apply_params 调用 apply_params_to_code（含 self_review 透传）。"""
    import laap_brain.api as api

    captured = {}
    class _Q:
        def apply_params_to_code(self, params, rationale="", method="",
                                 self_review=True):
            captured.update(params=params, self_review=self_review)
            return {"status": "awaiting_approval", "mutation_id": "m1"}

    monkeypatch.setattr(api, "_get_quant_engine", lambda: _Q())
    resp = _run(api.handle_quant_apply_params(_Req(body={
        "params": {"fast_ma": 6}, "self_review": False})))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert d["status"] == "awaiting_approval"
    assert captured["self_review"] is False


# ════════════════════════════════════════════════════════════
# 新闻情报闭环 API（P4）
# ════════════════════════════════════════════════════════════

def test_news_routes_registered():
    """create_app 应注册 5 条新闻情报路由。"""
    from laap_brain.api import create_app
    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    expected = {
        "/v1/quant/news",
        "/v1/quant/profile",
        "/v1/quant/news/verify",
        "/v1/quant/news/scan",
        "/v1/quant/risk/rejections",
    }
    assert expected.issubset(routes)


def _FakeDBWithSchema(path):
    import sqlite3
    conn = sqlite3.connect(str(path))
    conn.executescript("""
    CREATE TABLE news_verdicts (news_id TEXT, symbol TEXT, verdict TEXT,
        confidence REAL, reasons_json TEXT, impact TEXT, rsi REAL,
        trade_action TEXT, dispatched INTEGER, decision_id TEXT,
        used_fallback INTEGER, ts REAL);
    CREATE TABLE news_items (id TEXT PRIMARY KEY, symbol TEXT, title TEXT,
        content TEXT, source TEXT, published_at TEXT, url TEXT, fetched_ts REAL);
    CREATE TABLE risk_rejections (id TEXT, symbol TEXT, rule_id TEXT,
        reason TEXT, meta_json TEXT, ts REAL);
    """)
    conn.commit()
    conn.close()

    class _DB:
        def conn(self):
            c = sqlite3.connect(str(path))
            c.row_factory = sqlite3.Row
            return c
    return _DB()


def test_quant_news_ok(monkeypatch, tmp_path):
    import laap_brain.api as api
    db = _FakeDBWithSchema(tmp_path / "n.db")
    c = db.conn()
    c.execute("INSERT INTO news_verdicts (news_id,symbol,verdict,confidence,"
              "reasons_json,impact,rsi,trade_action,dispatched,decision_id,"
              "used_fallback,ts) VALUES ('n1','600519','genuine_bullish',0.8,"
              "'[]','',55,'buy',1,'dec1',0,1.0)")
    c.execute("INSERT INTO news_items (id,symbol,title,content,source,"
              "published_at,url,fetched_ts) VALUES ('n1','600519','茅台提价',"
              "'核心产品提价','公告','2026-08-15','http://x',1.0)")
    c.commit()
    c.close()
    monkeypatch.setattr(api, "_get_quant_db", lambda: db)
    resp = _run(api.handle_quant_news(_Req(query={"symbol": "600519"})))
    assert resp.status == 200
    rows = json.loads(resp.text)
    assert len(rows) == 1
    assert rows[0]["verdict"] == "genuine_bullish"
    assert rows[0]["title"] == "茅台提价"  # 联表 news_items 返回标题


def test_quant_profile_missing_symbol(monkeypatch):
    import laap_brain.api as api
    resp = _run(api.handle_quant_profile(_Req()))
    assert resp.status == 400


def test_quant_news_verify_missing_fields():
    import laap_brain.api as api
    resp = _run(api.handle_quant_news_verify(_Req(body={"symbol": "600519"})))
    assert resp.status == 400


def test_quant_news_verify_ok(monkeypatch):
    import laap_brain.api as api
    import laap.paper_trading.news_verifier as nv
    import laap.paper_trading.news_intel as ni
    captured = {}

    def _fake_verify(item, profile, ts, llm_call=None):
        captured.update(symbol=item.symbol, title=item.title)
        from laap.paper_trading.news_verifier import NewsVerdict
        return NewsVerdict(news_id="n1", verdict="genuine_bullish",
                           confidence=0.9, reasons=["利好"], trade_action="buy")
    monkeypatch.setattr(nv, "compute_tech_state", lambda *a, **k: None)
    monkeypatch.setattr(ni, "fetch_stock_profile", lambda *a, **k: (None, {}))
    monkeypatch.setattr(nv, "verify_news", _fake_verify)
    resp = _run(api.handle_quant_news_verify(
        _Req(body={"symbol": "600519", "title": "茅台提价", "content": "c"})))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert d["verdict"] == "genuine_bullish"
    assert captured["symbol"] == "600519"


def test_quant_news_scan_missing_symbol():
    import laap_brain.api as api
    resp = _run(api.handle_quant_news_scan(_Req(body={})))
    assert resp.status == 400


def test_quant_news_scan_ok(monkeypatch):
    import laap_brain.api as api

    class _StubPipe:
        def run(self, symbol, auto_order=True, force=False):
            return {"symbol": symbol, "dispatched": True, "silent": False,
                    "reason": "真利好 → 自动下单", "news_count": 1}
    monkeypatch.setattr(api, "_get_news_pipeline", lambda auto_order=True: _StubPipe())
    resp = _run(api.handle_quant_news_scan(
        _Req(body={"symbol": "600519", "auto_order": True})))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert d["dispatched"] is True


def test_quant_risk_rejections_ok(monkeypatch, tmp_path):
    import laap_brain.api as api
    db = _FakeDBWithSchema(tmp_path / "r.db")
    c = db.conn()
    c.execute("INSERT INTO risk_rejections (id,symbol,rule_id,reason,meta_json,ts)"
              " VALUES ('r1','600519','R2','超仓','{}',1.0)")
    c.commit()
    c.close()
    monkeypatch.setattr(api, "_get_quant_db", lambda: db)
    resp = _run(api.handle_quant_risk_rejections(_Req(query={"symbol": "600519"})))
    assert resp.status == 200
    rows = json.loads(resp.text)
    assert len(rows) == 1
    assert rows[0]["rule_id"] == "R2"
