# -*- coding: utf-8 -*-
"""Tests for _market_summary_demo.py (LAAP 市场行情总结脚本)。"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "_market_summary_demo.py"

# 腾讯行情样例（真实抓取字段，70+ 段）：上证指数 跌 0.82%
SAMPLE_SH = (
    "1~上证指数~000001~3934.09~3966.59~3950.71~529490944~0~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0."
    "00~0~0.00~0~0.00~0~0.00~0~~20260811161401~-32.50~-0.82~3966.39~3930.64~3934.09/529490944/1066737091823~529490944~106673709"
)
SAMPLE_RAW = f'v_sh000001="{SAMPLE_SH}";'.encode("gbk")


@pytest.fixture(scope="module")
def script_mod():
    spec = importlib.util.spec_from_file_location("market_summary_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["market_summary_demo"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_compiles():
    compile(SCRIPT.read_bytes(), str(SCRIPT), "exec")


def test_fetch_market_parses_tencent_data(script_mod):
    """mock 腾讯响应：正确解析指数字段。"""
    import urllib.request

    class FakeResp:
        def read(self):
            return SAMPLE_RAW

    with patch.object(urllib.request, "urlopen", return_value=FakeResp()):
        result = script_mod.fetch_market()

    assert result["count"] == 1
    ix = result["indices"][0]
    assert ix["name"] == "上证指数"
    assert ix["price"] == 3934.09
    assert ix["change"] == -32.50
    assert ix["change_pct"] == -0.82
    assert ix["high"] == 3966.39
    assert ix["low"] == 3930.64
    assert ix["turnover_yi"] == round(106673709 / 10000.0, 1)


def test_market_tool_routes(script_mod):
    """「A股市场行情」消息触发 get_market_overview 且 region=cn。"""
    from laap.agi.tool_router import build_tool_calls

    routed = build_tool_calls("总结一下今天A股市场行情", script_mod.MARKET_TOOLS)
    assert routed is not None
    tc = routed["tool_calls"][0]
    assert tc["function"]["name"] == "get_market_overview"
    import json

    assert json.loads(tc["function"]["arguments"]).get("region") == "cn"


def test_stock_question_prefers_quote_tool(script_mod):
    """多工具场景：个股股价问题应路由到行情工具而非市场概览。"""
    from laap.agi.tool_router import build_tool_calls

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_realtime_quote",
                "description": "Get real-time stock quote including price, change%, "
                               "volume ratio, turnover rate, PE, PB, market cap.",
                "parameters": {
                    "type": "object",
                    "properties": {"stock_code": {"type": "string"}},
                    "required": ["stock_code"],
                },
            },
        }
    ] + script_mod.MARKET_TOOLS
    routed = build_tool_calls("查一下600519股价", tools)
    assert routed is not None
    assert routed["tool_calls"][0]["function"]["name"] == "get_realtime_quote"


def test_market_question_prefers_market_tool(script_mod):
    """多工具场景：大盘行情问题应路由到市场概览工具。"""
    from laap.agi.tool_router import build_tool_calls

    quote_tool = {
        "type": "function",
        "function": {
            "name": "get_realtime_quote",
            "description": "Get real-time stock quote including price, change%, "
                           "volume ratio, turnover rate, PE, PB, market cap.",
            "parameters": {
                "type": "object",
                "properties": {"stock_code": {"type": "string"}},
                "required": ["stock_code"],
            },
        },
    }
    routed = build_tool_calls("总结一下今天A股市场行情", [quote_tool] + script_mod.MARKET_TOOLS)
    assert routed is not None
    assert routed["tool_calls"][0]["function"]["name"] == "get_market_overview"


@pytest.mark.network
def test_fetch_market_real_network(script_mod):
    """真实网络：三大指数全部拉取成功（需要腾讯行情接口可达）。"""
    result = script_mod.fetch_market()
    assert result["count"] == 3
    names = {ix["name"] for ix in result["indices"]}
    assert names == {"上证指数", "深证成指", "创业板指"}
