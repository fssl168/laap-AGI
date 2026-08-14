"""
LAAP AGI Tool Router 测试（认知层工具决策）
============================================

覆盖 laap/agi/tool_router.py：
  - 基础匹配（与 laap_brain.tools 兼容）
  - PSI 认知增强：情感/自信度/需求调整决策阈值
  - 语义记忆 → 意图域加分
  - AGIAgent.decide_tool_calls 集成入口

运行:
    python -m pytest tests/test_agi_tool_router.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the repository root is on sys.path when running directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from laap.agi.tool_router import (  # noqa: E402
    AGIToolRouter,
    ToolCallPlan,
    build_tool_calls,
    get_router,
    match_tool,
)

# 弱工具：名字无域词、描述只有 1 个域词 → news 权重 1。
# 无实体消息「看下新闻」→ strong 域 3×权重1 = 恰好 3 分，用于阈值边界测试。
WEAK_NEWS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_info",
            "description": "news service",
            "parameters": {
                "type": "object",
                "properties": {"stock_code": {"type": "string"}},
                "required": ["stock_code"],
            },
        },
    }
]

# 强工具：DSA 真实 search_stock_news 形状（名字含 news → 权重 3）
STRONG_NEWS_TOOLS = [
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
    }
]

NEUTRAL_PSI = {"emotion": "neutral", "confidence": 0.5, "needs": {}}
ANXIOUS_PSI = {"emotion": "anxiety", "confidence": 0.5, "needs": {}}
LOW_CONF_PSI = {"emotion": "neutral", "confidence": 0.2, "needs": {}}
HIGH_COMPETENCE_PSI = {"emotion": "neutral", "confidence": 0.5, "needs": {"competence": 0.85}}


# ── 基础匹配（与 laap_brain.tools 同源）────────────────────────


def test_router_basic_match():
    router = AGIToolRouter()
    plan = router.decide("查一下600519的新闻", STRONG_NEWS_TOOLS, psi_state=NEUTRAL_PSI)
    assert plan is not None and plan.tool_calls
    assert plan.tool_calls[0]["function"]["name"] == "search_stock_news"
    args = json.loads(plan.tool_calls[0]["function"]["arguments"])
    assert args["stock_code"] == "600519"
    assert plan.decision == "matched"
    assert plan.engine == "agi:tool_router"


def test_router_no_match_without_tools():
    router = AGIToolRouter()
    assert router.decide("查一下600519的新闻", [], psi_state=NEUTRAL_PSI) is None


# ── 认知阈值调整 ──────────────────────────────────────────────


def test_adjusted_threshold_neutral():
    router = AGIToolRouter()
    assert router.adjusted_threshold(NEUTRAL_PSI) == 3
    assert router.adjusted_threshold(None) == 3


def test_adjusted_threshold_negative_emotion():
    router = AGIToolRouter()
    assert router.adjusted_threshold(ANXIOUS_PSI) == 4
    assert router.adjusted_threshold({"emotion": "confusion", "confidence": 0.5, "needs": {}}) == 4


def test_adjusted_threshold_low_confidence():
    router = AGIToolRouter()
    assert router.adjusted_threshold(LOW_CONF_PSI) == 4


def test_adjusted_threshold_high_competence():
    router = AGIToolRouter()
    assert router.adjusted_threshold(HIGH_COMPETENCE_PSI) == 2


def test_adjusted_threshold_combined_deltas():
    router = AGIToolRouter()
    # anxiety(+1) + low confidence(+1) + high competence(-1) = 3 + 1 = 4
    psi = {"emotion": "anxiety", "confidence": 0.1, "needs": {"competence": 0.9}}
    assert router.adjusted_threshold(psi) == 4
    # 阈值不低于 1
    psi2 = {"emotion": "calm", "confidence": 0.9, "needs": {"competence": 0.99}}
    assert router.adjusted_threshold(psi2) == 2


# ── 认知状态翻转决策（弱匹配恰好 3 分）─────────────────────────


def test_anxiety_suppresses_weak_match():
    """焦虑时阈值 4：恰好 3 分的弱匹配不触发工具；neutral 触发。"""
    router = AGIToolRouter()
    assert router.decide("看下新闻", WEAK_NEWS_TOOLS, psi_state=NEUTRAL_PSI) is not None
    assert router.decide("看下新闻", WEAK_NEWS_TOOLS, psi_state=ANXIOUS_PSI) is None


def test_low_confidence_suppresses_weak_match():
    router = AGIToolRouter()
    assert router.decide("看下新闻", WEAK_NEWS_TOOLS, psi_state=LOW_CONF_PSI) is None


def test_high_competence_encourages_match():
    router = AGIToolRouter()
    assert router.decide("看下新闻", WEAK_NEWS_TOOLS, psi_state=HIGH_COMPETENCE_PSI) is not None


# ── 语义记忆 → 意图域加分 ─────────────────────────────────────


def test_memory_boost_flips_anxious_decision():
    """记忆里反复出现新闻域 → +1 分 → 焦虑阈值 4 也能触发。"""
    router = AGIToolRouter()
    memory = ["用户经常问股票新闻", "最近在关注腾讯的新闻动态"]
    plan = router.decide(
        "看下新闻",
        WEAK_NEWS_TOOLS,
        psi_state=ANXIOUS_PSI,
        memory_context=memory,
    )
    assert plan is not None
    assert plan.cognition["memory_boost"].get("news", 0) >= 1
    assert plan.threshold_used == 4


def test_memory_boost_empty_context_no_change():
    router = AGIToolRouter()
    assert router.decide("看下新闻", WEAK_NEWS_TOOLS, psi_state=ANXIOUS_PSI) is None


# ── tool_choice 语义（认知层）─────────────────────────────────


def test_router_tool_choice_none():
    router = AGIToolRouter()
    assert router.decide("看下新闻", WEAK_NEWS_TOOLS, tool_choice="none", psi_state=NEUTRAL_PSI) is None


def test_router_tool_choice_forced():
    router = AGIToolRouter()
    plan = router.decide(
        "随便聊聊",
        STRONG_NEWS_TOOLS,
        tool_choice={"type": "function", "function": {"name": "search_stock_news"}},
        psi_state=NEUTRAL_PSI,
    )
    assert plan is not None
    assert plan.decision == "forced"


def test_router_tool_choice_required_picks_best():
    router = AGIToolRouter()
    plan = router.decide("你好", WEAK_NEWS_TOOLS, tool_choice="required", psi_state=NEUTRAL_PSI)
    assert plan is not None
    assert plan.tool_calls[0]["function"]["name"] == "query_info"


# ── 决策历史 ─────────────────────────────────────────────────


def test_router_decision_history():
    router = AGIToolRouter()
    router.decide("看下新闻", WEAK_NEWS_TOOLS, psi_state=NEUTRAL_PSI)
    router.decide("今天天气如何", WEAK_NEWS_TOOLS, psi_state=NEUTRAL_PSI)
    assert len(router.decision_history) == 2
    assert router.decision_history[0].decision == "matched"
    assert router.decision_history[1].decision == "none"
    assert router.last_decision is not None


# ── AGIAgent 集成入口 ─────────────────────────────────────────


def _make_light_agent():
    """轻量 AGIAgent：跳过全部认知模块初始化（enable_all=False 有 _module_count 缺陷，补属性）。"""
    from laap.agi.core import AGIAgent

    agent = AGIAgent(name="test-agent", enable_all=False)
    for attr in (
        "memory_system", "evolution", "security", "hermes", "code_evolution",
        "self_healing", "quality_assurance", "code_minimizer",
        "agent_registry", "task_board", "safe_rollback",
    ):
        if not hasattr(agent, attr):
            setattr(agent, attr, None)
    return agent


def test_agi_agent_decide_tool_calls():
    agent = _make_light_agent()
    result = agent.decide_tool_calls("查一下600519的新闻", STRONG_NEWS_TOOLS, psi_state=NEUTRAL_PSI)
    assert result is not None
    assert result["engine"] == "agi:tool_router"
    assert result["tool_calls"][0]["function"]["name"] == "search_stock_news"
    assert isinstance(result["plan"], ToolCallPlan)

    none_result = agent.decide_tool_calls("今天天气如何", STRONG_NEWS_TOOLS, psi_state=NEUTRAL_PSI)
    assert none_result is None


def test_agi_agent_router_cached():
    agent = _make_light_agent()
    r1 = agent.get_tool_router()
    r2 = agent.get_tool_router()
    assert r1 is r2
    assert isinstance(r1, AGIToolRouter)


# ── 模块级单例与兼容层 ────────────────────────────────────────


def test_module_singleton():
    assert get_router() is get_router()
    assert isinstance(get_router(), AGIToolRouter)


def test_compat_layer_build_tool_calls():
    """laap_brain.tools 兼容层仍可用。"""
    from laap_brain.tools import build_tool_calls as compat_build

    routed = compat_build("查一下600519的新闻", STRONG_NEWS_TOOLS)
    assert routed is not None
    assert routed["tool_calls"][0]["function"]["name"] == "search_stock_news"
    assert match_tool("查一下600519的新闻", STRONG_NEWS_TOOLS) is not None


# ── 论文搜索场景（paper 域 + query 提取）─────────────────────

PAPER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "Search academic papers on arXiv. Returns paper titles, authors, abstracts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search topic"},
                    "max_results": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
        },
    }
]


def test_paper_domain_match():
    tool = match_tool("搜索关于AGI的论文", PAPER_TOOLS)
    assert tool is not None
    assert tool["function"]["name"] == "search_papers"


def test_paper_query_extraction():
    routed = build_tool_calls("搜索关于AGI的论文", PAPER_TOOLS)
    args = json.loads(routed["tool_calls"][0]["function"]["arguments"])
    assert args["query"] == "AGI"
    assert "max_results" not in args  # 非 required 且提取不到 → 不传（用默认）

    routed2 = build_tool_calls("搜一下 大语言模型 相关的论文", PAPER_TOOLS)
    args2 = json.loads(routed2["tool_calls"][0]["function"]["arguments"])
    assert args2["query"] == "大语言模型"


def test_paper_no_false_positive_on_stock_question():
    """股票问题不该触发论文搜索。"""
    assert match_tool("查一下茅台股价", PAPER_TOOLS) is None


# ── 中英文论文参数（source / language）───────────────────────

BILINGUAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "Search academic papers (中英文论文资料查询). Supports arXiv "
                           "and OpenAlex/Crossref including Chinese journals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "source": {"type": "string", "enum": ["auto", "arxiv", "openalex", "crossref"]},
                    "language": {"type": "string", "enum": ["all", "zh", "en"]},
                },
                "required": ["query"],
            },
        },
    }
]


def _args_of(msg):
    r = build_tool_calls(msg, BILINGUAL_TOOLS)
    assert r is not None, f"no tool call for {msg!r}"
    return json.loads(r["tool_calls"][0]["function"]["arguments"])


def test_source_crossref_for_chinese():
    args = _args_of("搜索关于 强化学习 的中文论文")
    assert args["source"] == "crossref"
    assert args["language"] == "zh"
    assert args["query"] == "强化学习"


def test_source_arxiv_for_english():
    args = _args_of("找一下 arxiv 上的 LLM 英文论文")
    assert args["source"] == "arxiv"
    assert args["language"] == "en"


def test_source_default_omitted():
    """未指定中英文时 source/language 不传（调用方用默认 auto/all）。"""
    args = _args_of("搜索关于AGI的论文")
    assert "source" not in args
    assert "language" not in args


def test_bilingual_queries_use_crossref():
    args = _args_of("搜中英文的大语言模型论文")
    assert args["source"] == "crossref"
    assert "language" not in args  # 中英文都要 → 不过滤语言


# ── 相对日期提取（前天/昨天/今天）───────────────────────────

DATE_TOOL = [{
    "type": "function",
    "function": {
        "name": "get_watchlist_status",
        "description": "Query watchlist status. 查询自选股某交易日概况，date 支持 前天/昨天/今天。",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string"}},
            "required": []},
    },
}]


def test_date_offset_extraction():
    assert _args_of2("自选股前天的股票怎么样").get("date") == "-2"
    assert _args_of2("昨天的自选股怎么样").get("date") == "-1"
    assert _args_of2("今天自选股怎么样").get("date") == "0"
    assert "date" not in _args_of2("自选股怎么样")


def _args_of2(msg):
    r = build_tool_calls(msg, DATE_TOOL)
    assert r is not None, f"no tool call for {msg!r}"
    return json.loads(r["tool_calls"][0]["function"]["arguments"])
