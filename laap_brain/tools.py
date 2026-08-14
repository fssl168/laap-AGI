"""
LAAP Tool Calling — OpenAI 兼容工具调用支持（兼容层）
======================================================

本模块是向后兼容的 re-export 层：真正的实现已下沉到 AGI 认知层
`laap.agi.tool_router`（AGIToolRouter，含 PSI 认知增强决策）。

新增代码请直接使用：
    from laap.agi.tool_router import AGIToolRouter, ToolCallPlan, get_router

本模块保留同名函数签名，避免破坏既有调用方（api.py / 测试）。
"""

from laap.agi.tool_router import (  # noqa: F401
    AGIToolRouter,
    ToolCallPlan,
    FAMOUS_STOCKS,
    build_tool_calls,
    collect_tool_context,
    extract_arguments,
    find_stock_entities,
    find_stock_entity,
    get_router,
    match_tool,
    stock_name_for,
    summarize_tool_result,
    tokenize,
)

__all__ = [
    "AGIToolRouter",
    "ToolCallPlan",
    "FAMOUS_STOCKS",
    "build_tool_calls",
    "collect_tool_context",
    "extract_arguments",
    "find_stock_entities",
    "find_stock_entity",
    "get_router",
    "match_tool",
    "stock_name_for",
    "summarize_tool_result",
    "tokenize",
]
