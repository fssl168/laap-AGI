"""
LAAP Tool Calling 测试
======================

覆盖 laap_brain/tools.py 的确定性工具路由与 OpenAI 兼容响应。

运行:
    python -m pytest tests/test_laap_tools.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the repository root is on sys.path when running directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from laap_brain.tools import (  # noqa: E402
    build_tool_calls,
    collect_tool_context,
    extract_arguments,
    find_stock_entity,
    match_tool,
    summarize_tool_result,
)

STOCK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": "查询股票最新价格和行情",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 600519"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_stock_news",
            "description": "获取股票相关新闻资讯",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "limit": {"type": "integer", "description": "新闻条数"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "technical_analysis",
            "description": "股票技术面分析：均线、趋势、支撑压力",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "period": {"type": "string", "description": "周期，如日线/周线"},
                },
                "required": ["symbol"],
            },
        },
    },
]


# ── 实体识别 ────────────────────────────────────────────────


def test_find_stock_entity_code_a():
    assert find_stock_entity("帮我查一下600519的股价") == "600519"


def test_find_stock_entity_hk():
    assert find_stock_entity("腾讯 hk00700 最近怎么样") == "hk00700"


def test_find_stock_entity_us_ticker():
    assert find_stock_entity("分析 AAPL 财报") == "AAPL"


def test_find_stock_entity_famous_name():
    assert find_stock_entity("贵州茅台今天涨了吗") == "600519"
    assert find_stock_entity("特斯拉走势如何") == "TSLA"


def test_find_stock_entity_none():
    assert find_stock_entity("今天天气怎么样") is None


# ── 工具匹配 ─────────────────────────────────────────────────


def test_match_quote_by_price_words():
    tool = match_tool("查一下贵州茅台股价", STOCK_TOOLS)
    assert tool is not None
    assert tool["function"]["name"] == "get_stock_quote"


def test_match_news_by_domain():
    tool = match_tool("帮我看看腾讯的最新新闻", STOCK_TOOLS)
    assert tool is not None
    assert tool["function"]["name"] == "fetch_stock_news"


def test_match_technical_by_entity_and_domain():
    tool = match_tool("分析600519技术面和均线", STOCK_TOOLS)
    assert tool is not None
    assert tool["function"]["name"] == "technical_analysis"


def test_no_match_for_unrelated_message():
    assert match_tool("帮我分析一下今天的天气怎么样", STOCK_TOOLS) is None
    assert match_tool("你好，很高兴认识你", STOCK_TOOLS) is None


def test_no_match_without_tools():
    assert match_tool("查一下茅台股价", []) is None


def test_single_tool_still_matches():
    """回归：只声明一个工具时匹配器不能崩（曾因取第二名越界）。"""
    single = [STOCK_TOOLS[0]]
    tool = match_tool("查一下600519股价", single)
    assert tool is not None
    assert tool["function"]["name"] == "get_stock_quote"
    routed = build_tool_calls("查一下600519股价", single)
    assert routed is not None
    assert routed["tool_calls"][0]["function"]["name"] == "get_stock_quote"


def test_explicit_tool_name_matches():
    tool = match_tool("调用 get_stock_quote 查茅台", STOCK_TOOLS)
    assert tool is not None
    assert tool["function"]["name"] == "get_stock_quote"


# ── 参数抽取 ─────────────────────────────────────────────────


def test_extract_arguments_symbol_and_limit():
    args = extract_arguments(
        "查一下腾讯 hk00700 的最近5条新闻",
        STOCK_TOOLS[1]["function"]["parameters"],
    )
    assert args["symbol"] == "hk00700"
    assert args["limit"] == 5


def test_extract_arguments_boolean():
    params = {
        "type": "object",
        "properties": {
            "refresh": {"type": "boolean"},
        },
    }
    assert extract_arguments("刷新一下，要最新数据", params)["refresh"] is True
    assert extract_arguments("不要刷新", params)["refresh"] is False


# ── build_tool_calls 响应格式 ────────────────────────────────


def test_build_tool_calls_openai_shape():
    routed = build_tool_calls("查一下贵州茅台股价", STOCK_TOOLS)
    assert routed is not None
    tc = routed["tool_calls"][0]
    assert tc["type"] == "function"
    assert tc["id"].startswith("call_")
    assert tc["function"]["name"] == "get_stock_quote"
    args = json.loads(tc["function"]["arguments"])
    assert args["symbol"] == "600519"
    assert routed["engine"] == "tools:rule"


def test_build_tool_calls_none_for_ambiguous():
    assert build_tool_calls("今天天气如何", STOCK_TOOLS) is None


# ── tool 结果回填 ────────────────────────────────────────────


def test_summarize_tool_result_last_tool_message():
    messages = [
        {"role": "user", "content": "查茅台股价"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "call_x", "type": "function",
                         "function": {"name": "get_stock_quote", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_x", "name": "get_stock_quote",
         "content": "茅台 600519 最新价 1688.00 元"},
    ]
    summary = summarize_tool_result(messages)
    assert summary is not None
    assert "get_stock_quote" in summary
    assert "1688.00" in summary


def test_summarize_tool_result_ignores_normal_last_message():
    messages = [{"role": "user", "content": "你好"}]
    assert summarize_tool_result(messages) is None


def test_collect_tool_context():
    messages = [
        {"role": "tool", "name": "get_stock_quote", "content": "价格 1688"},
        {"role": "user", "content": "那新闻呢"},
    ]
    ctx = collect_tool_context(messages)
    assert "get_stock_quote" in ctx
    assert "1688" in ctx


# ── DSA 真实形状：英文工具描述 + 中文提问 ─────────────────────

DSA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_realtime_quote",
            "description": "Get real-time stock quote including price, change%, volume ratio, "
                           "turnover rate, PE, PB, market cap. Returns live market data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string", "description": "Stock code, e.g., '600519' (A-share), 'AAPL' (US), 'hk00700' (HK)"},
                },
                "required": ["stock_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_history",
            "description": "Get daily OHLCV (open, high, low, close, volume) historical data "
                           "with MA5/MA10/MA20 indicators. Returns the last N trading days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string", "description": "Stock code"},
                    "days": {"type": "integer", "description": "Number of trading days (default: 60)"},
                },
                "required": ["stock_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_trend",
            "description": "Run comprehensive technical trend analysis on a stock. Returns MA alignment, "
                           "MACD status, RSI levels, support/resistance levels, and a buy/sell signal.",
            "parameters": {
                "type": "object",
                "properties": {"stock_code": {"type": "string", "description": "Stock code"}},
                "required": ["stock_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_ma",
            "description": "Calculate moving averages for arbitrary periods from historical K-line data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string"},
                    "periods": {"type": "string", "description": "Comma-separated periods, e.g. '5,10,20'"},
                },
                "required": ["stock_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_stock_news",
            "description": "Search for the latest news articles about a specific stock. "
                           "Requires both stock_code and stock_name for accurate search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string"},
                    "stock_name": {"type": "string", "description": "Stock name in Chinese, e.g., '贵州茅台'"},
                },
                "required": ["stock_code", "stock_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_indices",
            "description": "Get major market indices (e.g., Shanghai Composite, Shenzhen Component).",
            "parameters": {
                "type": "object",
                "properties": {"region": {"type": "string", "enum": ["cn", "hk", "us"]}},
            },
        },
    },
]


def test_dsa_english_quote_tool_chinese_question():
    """DSA 真实形状：英文描述工具 + 中文提问 → 应命中 get_realtime_quote。"""
    tool = match_tool("查一下贵州茅台股价", DSA_TOOLS)
    assert tool is not None
    assert tool["function"]["name"] == "get_realtime_quote"


def test_dsa_english_news_tool():
    tool = match_tool("腾讯最近有什么新闻", DSA_TOOLS)
    assert tool is not None
    assert tool["function"]["name"] == "search_stock_news"
    args = extract_arguments("腾讯最近有什么新闻", DSA_TOOLS[4]["function"]["parameters"])
    assert args["stock_code"] == "hk00700"
    assert args["stock_name"] == "腾讯"  # 中文名反查


def test_dsa_trend_vs_history_disambiguation():
    """趋势分析与历史数据要能区分开。"""
    tool = match_tool("分析600519的均线和趋势", DSA_TOOLS)
    assert tool is not None
    assert tool["function"]["name"] == "analyze_trend"
    tool2 = match_tool("给我600519最近30天的历史K线", DSA_TOOLS)
    assert tool2 is not None
    assert tool2["function"]["name"] == "get_daily_history"
    args = extract_arguments("给我600519最近30天的历史K线", DSA_TOOLS[1]["function"]["parameters"])
    assert args["days"] == 30


def test_dsa_periods_param():
    args = extract_arguments("算一下600519的5,10,20日均线", DSA_TOOLS[3]["function"]["parameters"])
    assert args["stock_code"] == "600519"
    assert args["periods"] == "5,10,20"


def test_dsa_region_param():
    args = extract_arguments("看一下美股大盘", DSA_TOOLS[5]["function"]["parameters"])
    assert args["region"] == "us"


def test_dsa_no_false_positive_weather():
    assert match_tool("今天天气怎么样", DSA_TOOLS) is None


# ── tool_choice 语义 ─────────────────────────────────────────


def test_tool_choice_none_disables():
    assert match_tool("查一下茅台股价", DSA_TOOLS, tool_choice="none") is None
    assert build_tool_calls("查一下茅台股价", DSA_TOOLS, tool_choice="none") is None


def test_tool_choice_required_forces_call():
    tool = match_tool("你好", DSA_TOOLS, tool_choice="required")
    assert tool is not None  # required 语义：即使低分也调用


def test_tool_choice_forced_by_name():
    tool = match_tool("随便聊聊", DSA_TOOLS, tool_choice={"type": "function", "function": {"name": "calculate_ma"}})
    assert tool is not None
    assert tool["function"]["name"] == "calculate_ma"
    routed = build_tool_calls("随便聊聊", DSA_TOOLS, tool_choice={"type": "function", "function": {"name": "calculate_ma"}})
    assert routed["tool_calls"][0]["function"]["name"] == "calculate_ma"


# ── 并行多实体调用 ───────────────────────────────────────────


def test_parallel_calls_for_multiple_entities():
    routed = build_tool_calls("查一下茅台和腾讯的股价", DSA_TOOLS)
    assert routed is not None
    calls = routed["tool_calls"]
    assert len(calls) == 2
    symbols = {json.loads(c["function"]["arguments"])["stock_code"] for c in calls}
    assert symbols == {"600519", "hk00700"}


# ── API 集成测试（/v1/chat/completions 全链路）────────────────


@pytest.mark.asyncio
async def test_api_returns_tool_calls():
    from aiohttp.test_utils import TestClient, TestServer

    from laap_brain.api import create_app

    app = create_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "laap-core",
                "messages": [{"role": "user", "content": "查一下贵州茅台股价"}],
                "tools": STOCK_TOOLS,
            },
        )
        assert resp.status == 200
        data = await resp.json()
        msg = data["choices"][0]["message"]
        assert data["choices"][0]["finish_reason"] == "tool_calls"
        assert msg["content"] is None
        assert msg["tool_calls"][0]["function"]["name"] == "get_stock_quote"
        assert json.loads(msg["tool_calls"][0]["function"]["arguments"])["symbol"] == "600519"
        assert data["engine"] == "agi:tool_router"
        assert data["tool_decision"]["threshold_used"] >= 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_api_no_tools_no_tool_calls():
    from aiohttp.test_utils import TestClient, TestServer

    from laap_brain.api import create_app

    app = create_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "laap-core",
                "messages": [{"role": "user", "content": "查一下贵州茅台股价"}],
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["choices"][0]["finish_reason"] == "stop"
        assert "tool_calls" not in data["choices"][0]["message"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_api_tool_result_roundtrip():
    from aiohttp.test_utils import TestClient, TestServer

    from laap_brain.api import create_app

    app = create_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "laap-core",
                "messages": [
                    {"role": "user", "content": "查一下茅台股价"},
                    {"role": "assistant", "content": None,
                     "tool_calls": [{"id": "call_x", "type": "function",
                                     "function": {"name": "get_stock_quote", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "call_x", "name": "get_stock_quote",
                     "content": "600519 最新价 1688.00 元"},
                ],
            },
        )
        assert resp.status == 200
        data = await resp.json()
        content = data["choices"][0]["message"]["content"]
        assert "get_stock_quote" in content
        assert "1688.00" in content
        assert data["engine"] == "tools:result"
    finally:
        await client.close()
