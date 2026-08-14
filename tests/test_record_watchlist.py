# -*- coding: utf-8 -*-
"""Tests for _record_watchlist.py + watchlist 域路由。"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "_record_watchlist.py"

WATCHLIST_TOOL = [{
    "type": "function",
    "function": {
        "name": "add_watchlist",
        "description": "Record the user's stock watchlist (记录自选股列表) into memory. "
                       "Extracts all A-share stock codes from the message.",
        "parameters": {"type": "object", "properties": {
            "stock_codes": {"type": "string", "description": "Space-separated A-share codes"}},
            "required": ["stock_codes"]},
    },
}]


@pytest.fixture(scope="module")
def script_mod():
    spec = importlib.util.spec_from_file_location("record_watchlist", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["record_watchlist"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_compiles():
    compile(SCRIPT.read_bytes(), str(SCRIPT), "exec")


def test_watchlist_domain_routes(script_mod):
    """「记录自选股」消息触发 add_watchlist 工具。"""
    from laap.agi.tool_router import build_tool_calls

    codes = script_mod.WATCHLIST
    msg = f"请把我的自选股 {codes} 记录到记忆里"
    routed = build_tool_calls(msg, WATCHLIST_TOOL)
    assert routed is not None
    tc = routed["tool_calls"][0]
    assert tc["function"]["name"] == "add_watchlist"
    extracted = json.loads(tc["function"]["arguments"])["stock_codes"].split()
    assert len(extracted) == 42
    assert extracted[0] == "600326" and extracted[-1] == "002347"


def test_stock_codes_extraction_complete(script_mod):
    """42 只代码全部提取（无丢失/无多余）。"""
    from laap.agi.tool_router import build_tool_calls

    codes = script_mod.WATCHLIST
    routed = build_tool_calls(f"把我的自选股 {codes} 记下来", WATCHLIST_TOOL)
    extracted = json.loads(routed["tool_calls"][0]["function"]["arguments"])["stock_codes"].split()
    assert set(extracted) == set(codes.split())


def test_watchlist_not_triggered_by_stock_question():
    """个股问题不触发自选股记录工具。"""
    from laap.agi.tool_router import build_tool_calls

    assert build_tool_calls("查一下茅台股价", WATCHLIST_TOOL) is None


@pytest.mark.network
def test_memory_recall_roundtrip(script_mod):
    """真实链路：自选股记忆可被召回（需要 LAAP daemon）。"""
    import urllib.request

    payload = json.dumps({"query": "我的自选股列表", "limit": 1}).encode()
    req = urllib.request.Request(
        "http://localhost:11546/v1/recall_memory", data=payload,
        headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    top = (d.get("memories") or [{}])[0]
    assert "自选股" in (top.get("text") or "") and "K线记忆" in (top.get("text") or "")
    assert top.get("score", 0) >= 0.2
