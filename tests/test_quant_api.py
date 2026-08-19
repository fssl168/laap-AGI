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


def test_quant_dashboard_init_registered_and_returns():
    """G4：/v1/quant/dashboard/init 已注册，返回聚合快照字段齐全。"""
    from laap_brain.api import create_app, handle_quant_dashboard_init
    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/dashboard/init" in routes

    resp = _run(handle_quant_dashboard_init(_Req()))
    assert resp.status == 200
    data = json.loads(resp.text)
    for key in ("signals", "trades", "net_values", "strategies",
                "system_status", "ws_url", "stock_names"):
        assert key in data, f"缺少 {key}"
    assert data["ws_url"].startswith("ws://")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Req:
    def __init__(self, body=None, query=None, match_info=None):
        self._body = body or {}
        self._query = query or {}
        self.match_info = match_info or {}
        self.host = "127.0.0.1:11546"
        self.headers = {"X-Forwarded-Proto": "http"}

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
        def run_daily_cycle(self, symbols, params, ohlcv_map=None, news_gate=None):
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
# /v1/quant/strategies 策略列表/映射
# ════════════════════════════════════════════════════════════

def test_quant_strategies_route_registered():
    from laap_brain.api import create_app
    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/strategies" in routes


def test_quant_strategies_returns_mapping():
    """返回所有策略（8 个）与映射字段 + default。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_strategies(_Req()))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert d["count"] == 8
    assert d["default"] == "multi_factor"
    names = {s["name"] for s in d["strategies"]}
    assert "multi_factor" in names
    assert "golden_cross" in names and "macd_momentum" in names
    for s in d["strategies"]:
        assert "display_name" in s and "description" in s and "type" in s


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


# ════════════════════════════════════════════════════════════
# 量化控制台新增端点：/v1/quant/config + /v1/quant/account
# ════════════════════════════════════════════════════════════

def test_quant_config_registered_and_returns():
    """G3：/v1/quant/config 已注册，返回 config/sources/strategy。"""
    from laap_brain.api import create_app, handle_quant_config
    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/config" in routes

    resp = _run(handle_quant_config(_Req()))
    assert resp.status == 200
    data = json.loads(resp.text)
    for key in ("config", "sources", "strategy"):
        assert key in data, f"缺少 {key}"
    assert "NEWS_MIN_CONFIDENCE" in data["config"]
    assert "PAPER_TRADING_STRATEGY" in data["config"]
    assert isinstance(data["strategy"]["list"], list)
    assert len(data["strategy"]["list"]) >= 1


def test_quant_account_registered_and_returns(monkeypatch, tmp_path):
    """G4：/v1/quant/account 已注册，返回现金/持仓/净值/今日盈亏。"""
    from laap_brain.api import create_app, handle_quant_account
    import laap_brain.api as api

    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/account" in routes

    # 用真实 PaperDB（含 net_values/trades schema），loop 用 stub 提供 ledger
    from laap.paper_trading.db import PaperDB
    db = PaperDB(db_path=str(tmp_path / "a.db"))
    monkeypatch.setattr(api, "_get_quant_db", lambda: db)

    class _StubLedger:
        cash = 900000.0

        def open_positions(self):
            return []

    class _StubLoop:
        ledger = _StubLedger()

    monkeypatch.setattr(api, "_get_paper_loop", lambda: _StubLoop())
    resp = _run(api.handle_quant_account(_Req()))
    assert resp.status == 200
    data = json.loads(resp.text)
    for key in ("cash", "positions", "latest_net_value", "net_values", "today_pnl"):
        assert key in data, f"缺少 {key}"
    assert data["cash"] == 900000.0
    assert data["positions"] == []


# ════════════════════════════════════════════════════════════
# 量化控制台 P2-P5 新增端点：/v1/quant/backtest / decide / order
# ════════════════════════════════════════════════════════════

def test_quant_backtest_registered_and_returns(monkeypatch):
    """G2：/v1/quant/backtest 已注册，mock kline + runner 返回 metrics/net_values。"""
    from laap_brain.api import create_app, handle_quant_backtest
    import laap_brain.api as api

    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/backtest" in routes

    # mock kline_source.load_ohlcv：返回 (closes 序列, quality)
    monkeypatch.setattr(
        "laap.paper_trading.kline_source.load_ohlcv",
        lambda symbol, days=200, with_quality=False: (
            [(1.0, 10.0, 11.0, 9.5, 1000)] * 60,  # (open, close, high, low, volume)
            {"source": "injected", "used_fallback": False}))

    resp = _run(api.handle_quant_backtest(_Req(body={
        "strategy": "multi_factor", "symbol": "600519", "days": 60})))
    assert resp.status == 200
    data = json.loads(resp.text)
    for key in ("metrics", "net_values", "symbol", "strategy", "days"):
        assert key in data, f"缺少 {key}"
    assert data["symbol"] == "600519"
    assert isinstance(data["net_values"], list)


def test_quant_backtest_no_kline_400(monkeypatch):
    """无 K 线 → 400。"""
    import laap_brain.api as api
    monkeypatch.setattr(
        "laap.paper_trading.kline_source.load_ohlcv",
        lambda symbol, days=200, with_quality=False: ([], {}))
    resp = _run(api.handle_quant_backtest(_Req(body={"symbol": "600519"})))
    assert resp.status == 400


def test_quant_decide_registered_and_returns(monkeypatch):
    """G1b：/v1/quant/decide 已注册，mock bridge.use_decide 返回建议。"""
    from laap_brain.api import create_app, handle_quant_decide
    import laap_brain.api as api

    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/decide" in routes

    class _StubBridge:
        def use_decide(self, symbol, action, qty=0, rationale=""):
            return {"decision": "approve", "meaning": "可以", "benefit": "",
                    "reasons": [], "auto_execute": False, "executed": False,
                    "symbol": symbol, "action": action}

    monkeypatch.setattr("laap.paper_trading.quant_bridge.get_bridge",
                        lambda: _StubBridge())
    resp = _run(api.handle_quant_decide(_Req(body={
        "symbol": "600519", "action": "buy", "qty": 100})))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert d["decision"] == "approve"
    assert d["executed"] is False


def test_quant_decide_missing_fields():
    """缺 symbol/action → 400。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_decide(_Req(body={"symbol": "600519"})))
    assert resp.status == 400


def test_quant_order_registered_and_returns(monkeypatch):
    """G1：/v1/quant/order 已注册，mock bridge.use_execute 原样透传。"""
    from laap_brain.api import create_app, handle_quant_order
    import laap_brain.api as api

    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/order" in routes

    class _StubBridge:
        def use_execute(self, decision_id="", symbol="", action="", qty=0,
                        confirm_word=""):
            return {"executed": False, "status": "need_confirmation",
                    "message": "需要明确确认词"}

    monkeypatch.setattr("laap.paper_trading.quant_bridge.get_bridge",
                        lambda: _StubBridge())
    resp = _run(api.handle_quant_order(_Req(body={
        "symbol": "600519", "action": "buy", "qty": 100,
        "confirm_word": ""})))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert d["status"] == "need_confirmation"


def test_quant_order_bad_qty_400():
    """qty 非法 → 400。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_order(_Req(body={
        "symbol": "600519", "action": "buy", "qty": "abc",
        "confirm_word": "确认执行"})))
    assert resp.status == 400


def test_quant_order_missing_fields():
    """缺 symbol/action → 400。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_order(_Req(body={"action": "buy", "qty": 100})))
    assert resp.status == 400


# ── 支持批次 P1：参数运行时读写（/v1/quant/params/apply）──

def test_quant_params_apply_registered_and_updates_env(monkeypatch):
    """P1：端点已注册；改 env 后 quant_config.get 立即返回新值（运行时生效）。"""
    from laap_brain.api import create_app, handle_quant_params_apply
    import laap_brain.api as api
    from laap.paper_trading import quant_config as qc

    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/params/apply" in routes

    monkeypatch.delenv("NEWS_MIN_CONFIDENCE", raising=False)
    assert qc.get("NEWS_MIN_CONFIDENCE") == 0.7
    resp = _run(api.handle_quant_params_apply(_Req(body={
        "params": {"NEWS_MIN_CONFIDENCE": 0.8}})))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert d["applied"]["NEWS_MIN_CONFIDENCE"] == 0.8
    assert d["rejected"] == {}
    assert qc.get("NEWS_MIN_CONFIDENCE") == 0.8


def test_quant_params_apply_bool_coercion(monkeypatch):
    """bool 严格 1/true → '1'，其余 → '0'（对齐既有 _coerce 语义）。"""
    import laap_brain.api as api
    from laap.paper_trading import quant_config as qc
    monkeypatch.delenv("LAAP_NEWS_INTRADAY", raising=False)
    assert qc.get("LAAP_NEWS_INTRADAY") is False
    resp = _run(api.handle_quant_params_apply(_Req(body={
        "params": {"LAAP_NEWS_INTRADAY": True}})))
    d = json.loads(resp.text)
    assert d["applied"]["LAAP_NEWS_INTRADAY"] is True
    assert qc.get("LAAP_NEWS_INTRADAY") is True


def test_quant_params_apply_unknown_key_rejected():
    """未知键进 rejected（白名单 fail-closed），不写 env。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_params_apply(_Req(body={
        "params": {"FAKE_KEY": 1}})))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert "FAKE_KEY" in d["rejected"]
    assert d["applied"] == {}


def test_quant_params_apply_invalid_number_rejected(monkeypatch):
    """数值非法 → rejected（不静默回落默认，fail-closed）。"""
    import laap_brain.api as api
    monkeypatch.delenv("LAAP_TICK_ALERT_PCT", raising=False)
    resp = _run(api.handle_quant_params_apply(_Req(body={
        "params": {"LAAP_TICK_ALERT_PCT": "abc"}})))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert "LAAP_TICK_ALERT_PCT" in d["rejected"]


def test_quant_params_apply_bad_body_400():
    """空/缺 params → 400。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_params_apply(_Req(body={"params": {}})))
    assert resp.status == 400
    resp = _run(api.handle_quant_params_apply(_Req(body={"nope": 1})))
    assert resp.status == 400


def test_quant_config_returns_defaults():
    """config 响应含 defaults 字段（前端「恢复默认」数据源）。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_config(_Req()))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert "defaults" in d
    assert d["defaults"]["NEWS_MIN_CONFIDENCE"] == 0.7
    assert "MAX_POS_PER_STOCK" in d["defaults"]


# ── 支持批次 P2：人格设定（/v1/quant/personality + persona 乘数）──

def test_quant_personality_get_presets(monkeypatch):
    """三预设齐全且参数锁定；active 含 effective_risk/derived。"""
    from laap_brain.api import create_app, handle_quant_personality_get
    import laap_brain.api as api
    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/personality" in routes
    resp = _run(api.handle_quant_personality_get(_Req()))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert {p["id"] for p in d["presets"]} == {"conservative", "balanced", "aggressive"}
    for p in d["presets"]:
        assert p["params_locked"] is True
    assert "effective_risk" in d["active"]
    assert "derived" in d["active"]


def test_quant_personality_set_preset_changes_effective_risk(monkeypatch):
    """激活进攻型 → effective_risk 的 MAX_POS_PER_STOCK = 基线 × 1.3。"""
    import os
    import tempfile
    from pathlib import Path
    import laap_brain.api as api
    import json as _json
    from laap.paper_trading import quant_config as qc
    from laap.paper_trading.persona import persona_engine
    import laap.paper_trading.persona as persona_mod

    tmp = os.path.join(tempfile.mkdtemp(), "persona.json")
    monkeypatch.setattr(persona_mod, "_state_path", lambda: Path(tmp))

    base = qc.get("MAX_POS_PER_STOCK")
    resp = _run(api.handle_quant_personality_set(_Req(body={"preset": "aggressive"})))
    assert resp.status == 200
    d = _json.loads(resp.text)
    assert d["active"]["preset_id"] == "aggressive"
    assert d["active"]["risk_scale"] == 1.3
    eff = persona_engine().effective("MAX_POS_PER_STOCK")
    assert abs(eff - base * 1.3) < 1e-6


def test_quant_personality_set_custom_traits_clamped(monkeypatch):
    """自定义 traits 越界 clamp 到 0~1；mode=custom。"""
    import os
    import tempfile
    from pathlib import Path
    import laap_brain.api as api
    import json as _json
    import laap.paper_trading.persona as persona_mod

    tmp = os.path.join(tempfile.mkdtemp(), "persona.json")
    monkeypatch.setattr(persona_mod, "_state_path", lambda: Path(tmp))

    resp = _run(api.handle_quant_personality_set(_Req(body={
        "custom": {"traits": {"curiosity": 5.0, "loyalty": -2.0, "playfulness": 0.5}}})))
    assert resp.status == 200
    d = _json.loads(resp.text)
    assert d["active"]["mode"] == "custom"
    t = d["active"]["traits"]
    assert t["curiosity"] == 1.0
    assert t["loyalty"] == 0.0
    persona_mod.reset_engine()


def test_quant_personality_set_invalid_400():
    """非法 preset / 缺 body → 400。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_personality_set(_Req(body={"preset": "super_risk"})))
    assert resp.status == 400
    resp = _run(api.handle_quant_personality_set(_Req(body={"nope": 1})))
    assert resp.status == 400


# ── 支持批次 P3：记忆教训（/v1/quant/memory/*）──

def test_quant_memory_status_returns_summary():
    """记忆状态：summary + semantic_enabled 字段齐全。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_memory_status(_Req()))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert "summary" in d
    assert "semantic_enabled" in d


def test_quant_memory_runtime_three_scopes():
    """运行时三视图（profile/work/learning）独立返回。"""
    import laap_brain.api as api
    for scope in ("profile", "work", "learning"):
        resp = _run(api.handle_quant_memory_runtime(_Req(query={"scope": scope})))
        assert resp.status == 200
        d = json.loads(resp.text)
        assert d["scope"] == scope
        assert "scope" in d


def test_quant_memory_archive_four_kinds():
    """档案四类（news/policy/research/summary）独立返回，空库不 500。"""
    import laap_brain.api as api
    for kind in ("news", "policy", "research", "summary"):
        resp = _run(api.handle_quant_memory_archive(_Req(query={"kind": kind})))
        assert resp.status == 200
        d = json.loads(resp.text)
        assert d["kind"] == kind


def test_quant_memory_meta_self_report():
    """记忆体（元认识）自报告可用或诚实不可用。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_memory_meta(_Req()))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert "available" in d


# ── 支持批次 P4：批量回测与报告（/v1/quant/backtest/batch + reports/report）──

def test_quant_backtest_batch_two_symbols(monkeypatch):
    """批量 2 标的：runs 长度 + aggregate 字段；mock K 线（stub）。"""
    import laap_brain.api as api
    from laap_brain.api import create_app, handle_quant_backtest_batch

    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/backtest/batch" in routes

    monkeypatch.setattr(
        "laap.paper_trading.kline_source.load_ohlcv",
        lambda symbol, days=200, with_quality=False: (
            [(1.0, 10.0, 11.0, 9.5, 1000)] * 60,
            {"source": "injected", "used_fallback": False}))

    resp = _run(api.handle_quant_backtest_batch(_Req(body={
        "symbols": ["600519", "000001"], "days": 60})))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert len(d["runs"]) == 2
    assert all(r["ok"] for r in d["runs"])
    assert d["aggregate"]["total"] == 2
    assert d["aggregate"]["ok"] == 2
    assert d["aggregate"]["best_symbol"] in ("600519", "000001")
    assert d["aggregate"]["median_cumulative_return"] is not None


def test_quant_backtest_batch_symbols_cap_400():
    """>10 标的 → 400（防 DoS）。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_backtest_batch(_Req(body={
        "symbols": [f"6000{i}" for i in range(11)]})))
    assert resp.status == 400


def test_quant_backtest_batch_bad_body_400():
    """缺 symbols / 空列表 → 400。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_backtest_batch(_Req(body={"nope": 1})))
    assert resp.status == 400
    resp = _run(api.handle_quant_backtest_batch(_Req(body={"symbols": []})))
    assert resp.status == 400


def test_quant_backtest_reports_list_and_detail(monkeypatch):
    """报告列表 + 详情；mock K 线与落库。"""
    import laap_brain.api as api
    from laap_brain.api import (create_app, handle_quant_backtest_reports,
                                handle_quant_backtest_report)
    import laap_brain.api as api_mod
    from laap.paper_trading.db import PaperDB
    import tempfile, os

    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/backtest/reports" in routes

    monkeypatch.setattr(
        "laap.paper_trading.kline_source.load_ohlcv",
        lambda symbol, days=200, with_quality=False: (
            [(1.0, 10.0, 11.0, 9.5, 1000)] * 60,
            {"source": "injected", "used_fallback": False}))
    tmp_db = os.path.join(tempfile.mkdtemp(), "bt.db")
    monkeypatch.setattr(api_mod, "_get_quant_db", lambda: PaperDB(db_path=tmp_db))

    resp = _run(api.handle_quant_backtest_batch(_Req(body={
        "symbols": ["600519"], "days": 60})))
    assert resp.status == 200

    resp2 = _run(api.handle_quant_backtest_reports(_Req(query={"limit": 10})))
    assert resp2.status == 200
    d2 = json.loads(resp2.text)
    assert d2["count"] >= 1
    rid = d2["reports"][0]["id"]

    resp3 = _run(api.handle_quant_backtest_report(_Req(match_info={"id": rid})))
    assert resp3.status == 200
    d3 = json.loads(resp3.text)
    assert d3["id"] == rid
    assert "net_values" in d3
    assert d3["run_type"] == "batch"


def test_quant_backtest_report_not_found_404():
    """乱码 id → 404。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_backtest_report(_Req(match_info={"id": "no_such_id"})))
    assert resp.status == 404


# ── 支持批次 P5：实盘占位（/v1/quant/live/*）──

def test_quant_live_status_placeholder():
    """占位状态：live_enabled 恒 false，paper 模式 active，broker 全未连接。"""
    from laap_brain.api import create_app, handle_quant_live_status
    import laap_brain.api as api
    app = create_app()
    routes = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/v1/quant/live/status" in routes
    resp = _run(api.handle_quant_live_status(_Req()))
    assert resp.status == 200
    d = json.loads(resp.text)
    assert d["mode"] == "placeholder"
    assert d["live_enabled"] is False
    assert d["paper_mode_active"] is True
    assert len(d["brokers"]) >= 3
    assert all(b["connected"] is False for b in d["brokers"])


def test_quant_live_connect_not_implemented():
    """连接恒 501 + connected:false（fail-closed，不假装接入）。"""
    import laap_brain.api as api
    resp = _run(api.handle_quant_live_connect(_Req(body={"broker_id": "qmt"})))
    assert resp.status == 501
    d = json.loads(resp.text)
    assert d["status"] == "not_implemented"
    assert d["connected"] is False


# ── 支持批次 P3 扩展：档案 CRUD（/v1/quant/memory/archive）──

def _memory_db(monkeypatch):
    """memory archive 的 db 重定向到 tmp PaperDB。"""
    import os
    import tempfile
    import laap_brain.api as api_mod
    from laap.paper_trading.db import PaperDB
    tmp = os.path.join(tempfile.mkdtemp(), "mem.db")
    db = PaperDB(db_path=tmp)
    monkeypatch.setattr(api_mod, "_get_quant_db", lambda: db)
    return db


def test_memory_archive_policy_crud(monkeypatch):
    """政策（原研报）：新增/列表/更新（content 变→新 hash）/删除/404。"""
    import laap_brain.api as api
    from laap.paper_trading.memory_api import (
        archive, archive_create, archive_update, archive_delete)
    _memory_db(monkeypatch)

    ok, res, code = archive_create("policy", {"sector": "碳中和", "content": "政策A：2030 碳达峰"})
    assert ok and code == 200
    pid = res["id"]

    d = archive("policy", limit=20)
    assert d["kind"] == "policy"
    assert any(i["id"] == pid for i in d["items"])

    ok, res2, code = archive_update("policy", pid, {"content": "政策A2：2035 碳中和"})
    assert ok and code == 200
    assert res2["id"] != pid  # content 变 → sha1 主键变（旧 hash 已删）

    ok, _, code = archive_delete("policy", res2["id"])
    assert ok
    ok, _, code = archive_delete("policy", pid)  # 旧 hash 已随更新删除
    assert not ok and code == 404


def test_memory_archive_research_crud(monkeypatch):
    """研报（原记录文档）：新增/列表/更新/删除（文件系统，tmp 目录）。"""
    import os
    import tempfile
    from pathlib import Path
    import laap_brain.api as api
    import laap.paper_trading.memory_api as mem_api

    tmpdir = tempfile.mkdtemp()
    monkeypatch.setattr(mem_api, "_report_dir", lambda: Path(tmpdir))

    ok, res, code = mem_api.archive_create("research", {"name": "研报A", "content": "# 研报A\n内容"})
    assert ok and code == 200
    fname = res["id"]
    assert (Path(tmpdir) / fname).exists()

    d = mem_api.archive("research", limit=20)
    assert any(i["id"] == fname for i in d["items"])

    ok, _, code = mem_api.archive_update("research", fname, {"content": "# 研报A v2"})
    assert ok
    assert (Path(tmpdir) / fname).read_text(encoding="utf-8") == "# 研报A v2"

    ok, _, code = mem_api.archive_delete("research", fname)
    assert ok
    assert not (Path(tmpdir) / fname).exists()


def test_memory_archive_news_crud(monkeypatch):
    """新闻稿：新增/列表（含 id）/更新/删除（级联判定）。"""
    import laap_brain.api as api
    from laap.paper_trading.memory_api import (
        archive, archive_create, archive_update, archive_delete)
    db = _memory_db(monkeypatch)

    ok, res, code = archive_create("news", {"symbol": "600519", "title": "人工新闻",
                                            "content": "正文", "source": "manual"})
    assert ok and code == 200
    nid = res["id"]

    d = archive("news", limit=20)
    assert any(i["id"] == nid for i in d["items"])

    ok, _, code = archive_update("news", nid, {"title": "人工新闻v2"})
    assert ok

    ok, _, code = archive_delete("news", nid)
    assert ok
    ok, _, code = archive_delete("news", nid)
    assert not ok and code == 404


def test_memory_archive_summary_note_crud(monkeypatch):
    """摘要：聚合字段 + 人工笔记新增/更新/删除。"""
    import laap_brain.api as api
    from laap.paper_trading.memory_api import (
        archive, archive_create, archive_update, archive_delete)
    _memory_db(monkeypatch)

    ok, res, code = archive_create("summary", {"title": "周记", "content": "本周观察"})
    assert ok and code == 200
    nid = res["id"]

    d = archive("summary", limit=20)
    assert "summary" in d and "notes" in d
    assert any(n["id"] == nid for n in d["notes"])

    ok, _, code = archive_update("summary", nid, {"content": "本周观察（修订）"})
    assert ok

    ok, _, code = archive_delete("summary", nid)
    assert ok
    d2 = archive("summary", limit=20)
    assert not any(n["id"] == nid for n in d2["notes"])


# ── 行情研究 · 自选股 + 政策自选（P3 扩展）──

def test_watchlist_crud(monkeypatch):
    """自选股（池+额外）：池内标的 add 幂等；额外标的写表增删。"""
    import laap_brain.api as api
    from laap.paper_trading.memory_api import watchlist_list, watchlist_add, watchlist_remove
    _memory_db(monkeypatch)
    # 隔离真实 K 线库回退：固定兜底池
    monkeypatch.setattr(
        "laap.paper_trading.daily_pipeline._get_watchlist_symbols",
        lambda: ["600519", "000001", "000858"])

    # 额外标的（不在 DEFAULT_SYMBOLS 兜底池内）
    ok, res, code = watchlist_add("300750", "电池龙头")
    assert ok and code == 200
    assert res["source"] == "extra" and res["in_pool"] is False

    # 池内标的（DEFAULT_SYMBOLS 兜底 600519）→ 幂等，不写表
    ok, res2, code = watchlist_add("600519", "池内")
    assert ok and res2["source"] == "pool" and res2["in_pool"] is True

    d = watchlist_list()
    assert any(i["symbol"] == "300750" and i["note"] == "电池龙头" and i["source"] == "extra"
               for i in d["items"])
    assert any(i["symbol"] == "600519" and i["source"] == "pool" for i in d["items"])
    assert d["pool_count"] == 3  # DEFAULT_SYMBOLS 兜底 3 只

    ok, _, code = watchlist_remove("300750")
    assert ok
    ok, _, code = watchlist_remove("300750")
    assert not ok and code == 404
    assert not any(i["symbol"] == "300750" for i in watchlist_list()["items"])


def test_policy_picks_crud(monkeypatch):
    """政策自选股：新增/列表/删除/404。"""
    import laap_brain.api as api
    from laap.paper_trading.memory_api import (
        policy_picks_list, policy_picks_add, policy_picks_remove)
    _memory_db(monkeypatch)

    ok, res, code = policy_picks_add("hash1", "新能源", "600905", "光伏龙头", "利好")
    assert ok and code == 200
    pid = res["id"]

    d = policy_picks_list()
    assert any(i["symbol"] == "600905" and i["sector"] == "新能源" for i in d["items"])

    ok, _, code = policy_picks_remove(pid)
    assert ok
    ok, _, code = policy_picks_remove(pid)
    assert not ok and code == 404


def test_policy_analyze_llm(monkeypatch):
    """政策解读：LLM 返回 JSON → 结构化提取，used_fallback=False。"""
    import laap_brain.api as api
    from laap.paper_trading.memory_api import archive_create, policy_analyze
    _memory_db(monkeypatch)

    ok, res, code = archive_create("policy", {"sector": "新能源", "content": "支持光伏、储能产业发展"})
    assert ok
    phash = res["id"]

    class _StubCall:
        def __call__(self, prompt, system="", max_tokens=800):
            return '{"sectors":["光伏","储能"],"hotspots":["钙钛矿"],"upstream":["硅料"]}'

    monkeypatch.setattr("laap.paper_trading.llm_sources.build_llm_call", lambda: _StubCall())
    d = policy_analyze(phash)
    assert d["used_fallback"] is False
    assert "光伏" in d["sectors"]
    assert d["upstream"] == ["硅料"]


def test_policy_analyze_fallback(monkeypatch):
    """政策解读：LLM 失败 → 关键词降级 + used_fallback=True（fail-closed 诚实）。"""
    import laap_brain.api as api
    from laap.paper_trading.memory_api import archive_create, policy_analyze
    _memory_db(monkeypatch)

    ok, res, code = archive_create("policy", {"sector": "半导体", "content": "发展集成电路产业，支持芯片制造"})
    assert ok
    phash = res["id"]

    def _boom(prompt, system="", max_tokens=800):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr("laap.paper_trading.llm_sources.build_llm_call", lambda: _boom)
    d = policy_analyze(phash)
    assert d["used_fallback"] is True
    assert any("产业" in s or "电路" in s for s in d["sectors"])


def test_policy_analyze_not_found(monkeypatch):
    """政策不存在 → error。"""
    import laap_brain.api as api
    from laap.paper_trading.memory_api import policy_analyze
    _memory_db(monkeypatch)
    d = policy_analyze("no_such_hash")
    assert "error" in d


def test_policy_picks_match_industry(monkeypatch):
    """候选生成：profile 行业关键词命中 → via=keyword。"""
    from laap.paper_trading.memory_api import policy_picks_match
    _memory_db(monkeypatch)
    monkeypatch.setattr(
        "laap.paper_trading.memory_api._pool_feature_index",
        lambda: {"600519": {"name": "贵州茅台", "industry": "白酒"},
                 "000858": {"name": "五粮液", "industry": "白酒"},
                 "300750": {"name": "宁德时代", "industry": "电池"},
                 "600905": {"name": "三峡能源", "industry": "电力"}})
    d = policy_picks_match(["白酒"], limit=8)
    syms = [c["symbol"] for c in d["candidates"]]
    assert "600519" in syms and "000858" in syms
    assert any(c["via"] == "keyword" for c in d["candidates"])
    assert d["pool_size"] == 4


def test_policy_picks_match_memory_backfill(monkeypatch):
    """候选生成：算法命中<5 → 记忆链历史政策候选补齐到 ≥5（LLM 默认不补足）。"""
    from laap.paper_trading.memory_api import policy_picks_match
    _memory_db(monkeypatch)
    monkeypatch.setattr(
        "laap.paper_trading.memory_api._pool_feature_index",
        lambda: {"600519": {"name": "贵州茅台", "industry": "白酒"},
                 "000858": {"name": "五粮液", "industry": "白酒"},
                 "300750": {"name": "宁德时代", "industry": "电池"},
                 "600905": {"name": "三峡能源", "industry": "电力"},
                 "601899": {"name": "紫金矿业", "industry": "有色金属"},
                 "600036": {"name": "招商银行", "industry": "银行"},
                 "000001": {"name": "平安银行", "industry": "银行"}})
    class FakeMem:
        """假记忆链：query(白酒) 召回历史政策候选（方向相关性），其余为空。"""
        def query(self, q, max_results=10):
            if "白酒" in str(q):
                return [
                    {"type": "episodic", "content": "政策候选 300750 宁德时代 方向:电池 关注:白酒",
                     "strength": 1.0, "emotional_valence": 0.3},
                    {"type": "episodic", "content": "政策候选 600905 三峡能源 方向:电力 关注:白酒",
                     "strength": 0.9, "emotional_valence": 0.3},
                    {"type": "episodic", "content": "政策候选 601899 紫金矿业 方向:有色金属 关注:白酒",
                     "strength": 0.8, "emotional_valence": 0.3},
                    {"type": "episodic", "content": "政策候选 600036 招商银行 方向:银行 关注:白酒",
                     "strength": 0.7, "emotional_valence": 0.3},
                ]
            return []
        def encode_experience(self, *a, **k): return {}
        def save(self): return True
    monkeypatch.setattr("laap.paper_trading.memory_api._get_unified_memory", lambda: FakeMem())
    called = {"llm": False}
    def boom(*a, **k):
        called["llm"] = True
        raise RuntimeError("LLM 不应被调用（候选 LLM 未配置）")
    monkeypatch.setattr("laap.paper_trading.memory_api._llm_pick", boom)
    d = policy_picks_match(["白酒"], limit=8)
    syms = [c["symbol"] for c in d["candidates"]]
    assert "600519" in syms                 # 算法命中
    assert "300750" in syms                 # 记忆链历史候选补齐
    assert len(syms) >= 5                   # 补齐到 ≥5
    assert any(c["via"] == "memory" for c in d["candidates"])
    assert not called["llm"]               # 未配置候选 LLM → 不调用 LLM