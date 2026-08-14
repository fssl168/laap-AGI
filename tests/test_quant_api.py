"""量化闭环 /v1/quant/* 路由测试。"""

from __future__ import annotations

import asyncio
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
