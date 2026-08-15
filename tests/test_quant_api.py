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
