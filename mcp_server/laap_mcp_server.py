"""
LAAP Brain MCP Server
=====================

Exposes LAAP cognitive capabilities as MCP tools for Hermes Agent.

Run in stdio mode (default, for Hermes mcp_servers):
    python mcp_server/laap_mcp_server.py

Run in SSE mode:
    python mcp_server/laap_mcp_server.py --sse --port 11547

Tools:
    laap_cognitive_state  - get PSI cognitive state for a user input
    laap_recall_memory    - recall relevant memories from LAAP
    laap_bootstrap        - awaken a new LAAP instance
    laap_reflect          - reflect on a completed turn
    laap_quant_*          - paper_trading 量化闭环工具 (查询/判定/下单/进化, 薄封装 /v1/quant/*)
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make LAAP brain modules importable
LAAP_ROOT = Path(__file__).resolve().parent.parent
ARIS_BRAIN = LAAP_ROOT / "aris_brain"
sys.path.insert(0, str(ARIS_BRAIN))
sys.path.insert(0, str(ARIS_BRAIN / "psi_jspace_bridge"))

import requests
from mcp.server.fastmcp import FastMCP

LAAP_API_BASE = os.environ.get("LAAP_API_BASE", "http://localhost:11546")

mcp = FastMCP("laap-brain")


def _laap_headers() -> dict:
    """可选鉴权头: LAAP_API_KEY 配置后与 api.py auth_middleware 对齐, 否则空头。"""
    key = os.environ.get("LAAP_API_KEY", "").strip()
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def _laap_post(endpoint: str, payload: dict) -> dict:
    """Call LAAP HTTP API and return JSON."""
    try:
        resp = requests.post(
            f"{LAAP_API_BASE}{endpoint}",
            json=payload,
            timeout=30,
            headers=_laap_headers(),
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "source": "laap_mcp_server"}


def _laap_get(endpoint: str, params: Optional[dict] = None) -> dict:
    """Call LAAP HTTP API (GET) and return JSON."""
    try:
        resp = requests.get(
            f"{LAAP_API_BASE}{endpoint}",
            params=params,
            timeout=30,
            headers=_laap_headers(),
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "source": "laap_mcp_server"}


@mcp.resource("memory://default")
def laap_memory_default() -> str:
    """LAAP default memory context: recall recent memories from the hierarchy."""
    result = _laap_post("/v1/recall_memory", {"query": "", "limit": 10})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_cognitive_state(input: str) -> str:
    """
    Get LAAP PSI cognitive state for the given user input.

    Returns a preamble that should be injected into the system prompt
    to modulate tone, attention, and response style.

    Args:
        input: The user's message for this turn.
    """
    result = _laap_post("/v1/cognitive_state", {"input": input})
    if "error" in result:
        return json.dumps({"laap_error": result["error"]}, ensure_ascii=False)

    return json.dumps(
        {
            "preamble": result.get("preamble", ""),
            "cot_hint": result.get("cot_hint", ""),
            "dominant_need": _get_dominant_need(result.get("state", {})),
            "attention_focus": result.get("state", {}).get("attention_focus", ""),
            "mood": result.get("state", {}).get("mood", ""),
            "needs": result.get("state", {}).get("needs", {}),
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def laap_recall_memory(query: str, limit: int = 5) -> str:
    """
    Recall memories from LAAP memory hierarchy relevant to the query.

    Args:
        query: Search query for memory recall.
        limit: Maximum number of memories to return (default 5).
    """
    result = _laap_post("/v1/recall_memory", {"query": query, "limit": limit})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_bootstrap(user_name: str = "friend", preset: str = "") -> str:
    """
    Awaken a new LAAP instance / trigger the Aris awakening ceremony.

    Args:
        user_name: Name of the user awakening LAAP.
        preset: Optional personality preset.
    """
    payload = {"user_name": user_name}
    if preset:
        payload["preset"] = preset
    result = _laap_post("/v1/bootstrap", payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_reflect(output: str, success: bool = False, connection: bool = False) -> str:
    """
    Reflect on a completed assistant turn and update LAAP PSI state.

    Args:
        output: The assistant's final output for this turn.
        success: Whether the turn was successful/useful.
        connection: Whether the turn strengthened user connection.
    """
    feedback = {"success": success, "connection": connection}
    result = _laap_post("/v1/reflect", {"output": output, "feedback": feedback})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_express(input: str) -> str:
    """
    Get TTS + Live2D expression parameters for the current LAAP cognitive state.

    Use this when you want to make Aris's voice and avatar match her mood.

    Args:
        input: The user's message for this turn (used to update cognitive state).
    """
    result = _laap_post("/v1/express", {"input": input})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_rsi_status() -> str:
    """
    Check RSI (Recursive Self-Improvement) engine status and stats.

    Returns current optimization parameters, improvement history, and active goals.
    """
    try:
        import sys
        from pathlib import Path
        LAAP_ROOT = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(LAAP_ROOT))
        from laap.agi.rsi_engine import RSIMetaEngine
        rsi = RSIMetaEngine()
        return json.dumps({
            "status": "ready",
            "stats": rsi.stats(),
            "parameters": [p.to_dict() for p in rsi.parameters.values()],
            "active_goals": [g.to_dict() for g in rsi.get_active_goals()]
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "status": "error"}, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_rsi_improve(parameter: str = None, rationale: str = "") -> str:
    """
    Apply an RSI self-improvement to a specific parameter.

    Args:
        parameter: Name of parameter to optimize (e.g., 'learning_rate', 'exploration_rate').
                   If None, auto-selects best candidate based on current stats.
        rationale: Optional explanation for the improvement.

    Returns:
        Result of the improvement attempt including old/new values and success status.
    """
    try:
        import sys
        from pathlib import Path
        LAAP_ROOT = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(LAAP_ROOT))
        from laap.agi.rsi_engine import RSIMetaEngine
        rsi = RSIMetaEngine()

        if not parameter:
            suggestions = rsi.suggest_improvements()
            if suggestions:
                parameter = suggestions[0]['parameter']
                rationale = suggestions[0]['rationale']
            else:
                return json.dumps({"status": "no_suggestions", "message": "No improvements needed"}, ensure_ascii=False)

        attempt = rsi.apply_improvement(parameter, 0.5, rationale)
        return json.dumps({
            "status": "applied",
            "parameter": attempt.target,
            "old_value": round(attempt.old_value, 3),
            "new_value": round(attempt.new_value, 3),
            "rationale": attempt.rationale
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "status": "error"}, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_rsi_full_cycle() -> str:
    """
    Run a full RSI improvement cycle: suggest → apply → generate goals.

    This performs one complete self-improvement iteration.
    """
    try:
        import sys
        from pathlib import Path
        LAAP_ROOT = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(LAAP_ROOT))
        from laap.agi.rsi_engine import RSIMetaEngine
        rsi = RSIMetaEngine()
        result = rsi.full_improvement_cycle()
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "status": "error"}, ensure_ascii=False, indent=2)


# ── paper_trading 量化闭环工具: /v1/quant/* 的 MCP 薄封装 ──
# 契约单源: 全部透传 LAAP HTTP API, 成本/风控/T+1/涨跌停等语义由 laap/paper_trading
# 内部保证 (fail-closed), MCP 层只做参数传递 + JSON 返回。写类工具 (下单/落码/部署)
# 在 docstring 中显式标注副作用; 危险开关默认关闭 (fail-closed)。

@mcp.tool()
def laap_quant_status() -> str:
    """
    Get paper_trading TradingSelf status (人格/自我模型/记忆教训).

    Read-only. Returns identity, personality preset, self-model stats.
    """
    result = _laap_get("/v1/quant/self/status")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_trades(symbol: str = "") -> str:
    """
    Query paper trading fill records (成交记录).

    Read-only. Args:
        symbol: 股票代码过滤 (如 600519), 空 = 最近 100 条全部。
    """
    params = {"symbol": symbol} if symbol else None
    result = _laap_get("/v1/quant/trades", params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_net_values() -> str:
    """
    Query paper net value series (净值序列 ts/cash/equity/total).

    Read-only.
    """
    result = _laap_get("/v1/quant/net_values")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_signals(symbol: str = "") -> str:
    """
    Query paper trading signals (交易信号).

    Read-only. Args:
        symbol: 股票代码过滤, 空 = 最近 100 条全部。
    """
    params = {"symbol": symbol} if symbol else None
    result = _laap_get("/v1/quant/signals", params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_orders() -> str:
    """
    Query paper orders (订单列表, 最近 100 条).

    Read-only.
    """
    result = _laap_get("/v1/quant/orders")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_outcomes() -> str:
    """
    Query paper outcomes / lessons (结果回填与教训, 最近 100 条).

    Read-only.
    """
    result = _laap_get("/v1/quant/outcomes")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_decisions(symbol: str = "") -> str:
    """
    Query paper decision records (决策留痕).

    Read-only. Args:
        symbol: 股票代码过滤, 空 = 最近 100 条全部。
    """
    params = {"symbol": symbol} if symbol else None
    result = _laap_get("/v1/quant/decisions", params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_lessons(lesson_type: str = "") -> str:
    """
    Query trading lessons (教训).

    Read-only. Args:
        lesson_type: 按类型过滤 (如 buy/sell), 空 = 全部 lessons 行。
    """
    params = {"lesson_type": lesson_type} if lesson_type else None
    result = _laap_get("/v1/quant/lessons", params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_risk_rejections(symbol: str = "") -> str:
    """
    Query risk-gate rejection audit (风控拒绝审计, 刑部).

    Read-only. Args:
        symbol: 股票代码过滤, 空 = 最近 100 条全部。
    """
    params = {"symbol": symbol} if symbol else None
    result = _laap_get("/v1/quant/risk/rejections", params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_kline(symbol: str = "600519", days: int = 120) -> str:
    """
    Query kline data (K线, symbol + days).

    Read-only. Args:
        symbol: 股票代码 (默认 600519)。
        days: 回溯天数 (默认 120)。
    """
    result = _laap_get("/v1/quant/kline", {"symbol": symbol, "days": days})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_news(symbol: str = "") -> str:
    """
    Query news verdicts joined with news content (新闻判定 + 内容).

    Read-only. Args:
        symbol: 股票代码过滤, 空 = 最近 50 条全部。
    """
    params = {"symbol": symbol} if symbol else None
    result = _laap_get("/v1/quant/news", params)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_profile(symbol: str) -> str:
    """
    Query stock profile (个股资料/股票概况, 双源兜底).

    Read-only. Args:
        symbol: 股票代码 (必填)。
    """
    result = _laap_get("/v1/quant/profile", {"symbol": symbol})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_evolve_audit() -> str:
    """
    Query quant evolution audit (进化审计记录, 最近 20 条).

    Read-only.
    """
    result = _laap_get("/v1/quant/evolve/audit")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_news_verify(symbol: str, title: str, content: str = "") -> str:
    """
    Manually verify a single news item (新闻判定, 不自动下单).

    Args:
        symbol: 股票代码。
        title: 新闻标题 (必填)。
        content: 新闻正文 (可选)。

    只判定不成交; 判定为真利好且过阈值时由后续 scan/下单链路决定是否执行。
    """
    result = _laap_post("/v1/quant/news/verify", {
        "symbol": symbol, "title": title, "content": content,
    })
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_news_scan(symbol: str, auto_order: bool = False, force: bool = False) -> str:
    """
    Run the full news pipeline once (新闻→判定→风控→自动下单留痕).

    Args:
        symbol: 股票代码 (必填)。
        auto_order: 是否允许自动下单。默认 False (fail-closed: 只出计划+留痕, 不成交)。
        force: True 时强制重判 (跳过 D1 去重, 会再调 LLM 增加成本)。

    ⚠️ auto_order=True 会真实扣费成交 (paper ledger, 含风控门 R1-R5)。
    """
    result = _laap_post("/v1/quant/news/scan", {
        "symbol": symbol, "auto_order": auto_order, "force": force,
    })
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_daily_cycle(symbols: Optional[List[str]] = None,
                           params: Optional[Dict[str, Any]] = None) -> str:
    """
    Run the daily paper closed-loop cycle (真实K线→信号→交易自我审核→交易→净值).

    Args:
        symbols: 股票代码列表, 缺省 = 默认 3 只 (600519/000001/000858)。
        params: 策略参数字典, 缺省用 STRATEGY_PARAMS。
    """
    payload: Dict[str, Any] = {}
    if symbols is not None:
        payload["symbols"] = symbols
    if params is not None:
        payload["params"] = params
    result = _laap_post("/v1/quant/daily_cycle", payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_evolve() -> str:
    """
    Trigger one round of code-level constrained evolution (产提案, 不自动部署).

    只产提案; 部署需另调 laap_quant_evolve_approve。
    """
    result = _laap_post("/v1/quant/evolve", {})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_evolve_params(
    method: str = "random",
    llm: bool = False,
    n_samples: Optional[int] = None,
    seed: Optional[int] = None,
    population: Optional[int] = None,
    generations: Optional[int] = None,
    significance: Optional[bool] = None,
    baseline_samples: Optional[int] = None,
    baseline_seed: Optional[int] = None,
    apply_code: bool = False,
    self_review: bool = True,
) -> str:
    """
    Run parameter evolution (确定性 / LLM 增强 + 可选落回代码).

    Args:
        method: grid | random | genetic。
        llm: True 时用 LLM 微调搜索。
        n_samples / seed / population / generations: 搜索参数 (可选)。
        significance / baseline_samples / baseline_seed: OOS 显著性门禁 (可选)。
        apply_code: True 时把搜索最佳参数落回 strategy.py (M4 治理)。
        self_review: apply_code 时是否启用交易自我审核 (默认 True)。

    ⚠️ apply_code=True 会修改策略代码 (走 M4 治理 + 审核, 可回滚)。
    """
    payload: Dict[str, Any] = {"method": method}
    if llm:
        payload["llm"] = True
    for k in ("n_samples", "seed", "population", "generations",
              "significance", "baseline_samples", "baseline_seed"):
        v = locals().get(k)
        if v is not None:
            payload[k] = v
    if apply_code:
        payload["apply_code"] = True
        payload["self_review"] = self_review
    result = _laap_post("/v1/quant/evolve_params", payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_apply_params(params: Dict[str, Any],
                            self_review: bool = True,
                            rationale: str = "") -> str:
    """
    Apply parameter search results back to code (M4 治理 + 交易自我审核).

    Args:
        params: 参数字典 (必填, 如 {"atr_period": 14, "risk_per_trade": 0.02})。
        self_review: 是否启用交易自我审核 (默认 True)。
        rationale: 落码理由 (可选)。

    ⚠️ 会修改策略代码 (可回滚, 审计留痕)。
    """
    result = _laap_post("/v1/quant/apply_params", {
        "params": params, "self_review": self_review, "rationale": rationale,
    })
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_evolve_approve(mutation_id: str, approver: str = "mcp") -> str:
    """
    Approve and deploy an evolution proposal (人工批准部署).

    Args:
        mutation_id: 提案 ID (来自 laap_quant_evolve / evolve_audit)。
        approver: 批准人标识 (默认 mcp)。

    ⚠️ 部署 = 把已测试通过的 mutation 落进代码 (审计留痕, 可 rollback)。
    """
    result = _laap_post("/v1/quant/evolve/approve", {
        "mutation_id": mutation_id, "approver": approver,
    })
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def laap_quant_evolve_reject() -> str:
    """
    Reject and rollback the most recent deployed evolution (拒绝并回滚).

    ⚠️ 回滚最近一次部署的代码进化。
    """
    result = _laap_post("/v1/quant/evolve/reject", {})
    return json.dumps(result, ensure_ascii=False, indent=2)


def _get_dominant_need(state: dict) -> str:
    needs = state.get("needs", {})
    if not needs:
        return "explore"
    return max(needs, key=lambda k: needs.get(k, 0))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LAAP Brain MCP Server")
    parser.add_argument("--sse", action="store_true", help="Run in SSE mode")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="SSE host (default 127.0.0.1; 无认证服务不应暴露到局域网)")
    parser.add_argument("--port", type=int, default=11547, help="SSE port")
    args = parser.parse_args()

    if args.sse:
        mcp = FastMCP("laap-brain", host=args.host, port=args.port)
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
