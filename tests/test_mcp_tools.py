"""Test LAAP MCP server tools (pytest).

覆盖:
  - 工具注册 (FastMCP list_tools): 认知工具 + paper_trading laap_quant_* 工具齐全
  - GET 透传: 端点 + 查询参数 + JSON 返回
  - POST 载荷构造: fail-closed 默认 (news_scan auto_order=False), 白名单透传
  - 鉴权头: LAAP_API_KEY 配置后透传 Bearer
不依赖真实 LAAP 服务 (monkeypatch HTTP 助手)。
"""
import json

import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(ROOT / "mcp_server"))
sys.path.insert(0, str(ROOT / "aris_brain"))

import laap_mcp_server as ms

QUANT_TOOLS = [
    "laap_quant_status",
    "laap_quant_trades",
    "laap_quant_net_values",
    "laap_quant_signals",
    "laap_quant_orders",
    "laap_quant_outcomes",
    "laap_quant_decisions",
    "laap_quant_lessons",
    "laap_quant_risk_rejections",
    "laap_quant_kline",
    "laap_quant_news",
    "laap_quant_profile",
    "laap_quant_evolve_audit",
    "laap_quant_news_verify",
    "laap_quant_news_scan",
    "laap_quant_daily_cycle",
    "laap_quant_evolve",
    "laap_quant_evolve_params",
    "laap_quant_apply_params",
    "laap_quant_evolve_approve",
    "laap_quant_evolve_reject",
]

CORE_TOOLS = [
    "laap_cognitive_state",
    "laap_recall_memory",
    "laap_bootstrap",
    "laap_reflect",
    "laap_express",
    "laap_rsi_status",
    "laap_rsi_improve",
    "laap_rsi_full_cycle",
]


@pytest.mark.asyncio
async def test_all_tools_registered():
    """认知工具与 paper_trading 工具全部注册进 MCP 工具列表。"""
    tools = await ms.mcp.list_tools()
    names = {t.name for t in tools}
    for name in CORE_TOOLS + QUANT_TOOLS:
        assert name in names, f"missing tool: {name}"
    assert len(names) == len(CORE_TOOLS) + len(QUANT_TOOLS)


def test_quant_trades_get_passthrough(monkeypatch):
    """GET 查询工具: 端点 + symbol 参数透传, 返回可解析 JSON。"""
    captured = {}

    def fake_get(endpoint, params=None):
        captured["endpoint"] = endpoint
        captured["params"] = params
        return [{"symbol": "600519", "side": "buy"}]

    monkeypatch.setattr(ms, "_laap_get", fake_get)
    out = ms.laap_quant_trades("600519")
    assert captured["endpoint"] == "/v1/quant/trades"
    assert captured["params"] == {"symbol": "600519"}
    data = json.loads(out)
    assert data[0]["symbol"] == "600519"


def test_quant_kline_params(monkeypatch):
    """laap_quant_kline: symbol+days 透传。"""
    captured = {}

    def fake_get(endpoint, params=None):
        captured["endpoint"] = endpoint
        captured["params"] = params
        return {"symbol": "600519", "data": []}

    monkeypatch.setattr(ms, "_laap_get", fake_get)
    out = ms.laap_quant_kline("600519", 30)
    assert captured["endpoint"] == "/v1/quant/kline"
    assert captured["params"] == {"symbol": "600519", "days": 30}
    assert json.loads(out)["symbol"] == "600519"


def test_quant_profile_requires_symbol(monkeypatch):
    captured = {}

    def fake_get(endpoint, params=None):
        captured["endpoint"] = endpoint
        return {"profile": None}

    monkeypatch.setattr(ms, "_laap_get", fake_get)
    ms.laap_quant_profile("000001")
    assert captured["endpoint"] == "/v1/quant/profile"


def test_news_scan_fail_closed_default(monkeypatch):
    """laap_quant_news_scan: 默认 auto_order=False (fail-closed), force=False。"""
    captured = {}

    def fake_post(endpoint, payload):
        captured["endpoint"] = endpoint
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(ms, "_laap_post", fake_post)
    out = ms.laap_quant_news_scan("600519")
    assert captured["endpoint"] == "/v1/quant/news/scan"
    assert captured["payload"]["auto_order"] is False
    assert captured["payload"]["force"] is False
    assert json.loads(out)["status"] == "ok"


def test_news_scan_auto_order_explicit(monkeypatch):
    """显式 auto_order=True 时透传 (调用方承担下单副作用)。"""
    captured = {}

    def fake_post(endpoint, payload):
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(ms, "_laap_post", fake_post)
    ms.laap_quant_news_scan("600519", auto_order=True)
    assert captured["payload"]["auto_order"] is True


def test_evolve_params_whitelist(monkeypatch):
    """laap_quant_evolve_params: 白名单透传, 未显式给的键不出现, apply_code 带 self_review。"""
    captured = {}

    def fake_post(endpoint, payload):
        captured["payload"] = payload
        return {"results": []}

    monkeypatch.setattr(ms, "_laap_post", fake_post)
    ms.laap_quant_evolve_params(method="genetic", seed=42, apply_code=True, self_review=True)
    p = captured["payload"]
    assert p["method"] == "genetic"
    assert p["seed"] == 42
    assert "llm" not in p  # 未显式开启不出现
    assert p["apply_code"] is True
    assert p["self_review"] is True


def test_helper_error_returns_json_string(monkeypatch):
    """HTTP 助手失败返回 {"error": ...} → 工具仍输出可解析 JSON (不抛异常)。"""
    monkeypatch.setattr(ms, "_laap_get", lambda ep, params=None: {"error": "boom"})
    out = ms.laap_quant_orders()
    data = json.loads(out)
    assert "error" in data


def test_auth_header(monkeypatch):
    """LAAP_API_KEY 配置后 _laap_headers 带 Bearer; 未配置为空。"""
    monkeypatch.delenv("LAAP_API_KEY", raising=False)
    assert ms._laap_headers() == {}
    monkeypatch.setenv("LAAP_API_KEY", "sk-test")
    assert ms._laap_headers() == {"Authorization": "Bearer sk-test"}
