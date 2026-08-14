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
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Make LAAP brain modules importable
LAAP_ROOT = Path(__file__).resolve().parent.parent
ARIS_BRAIN = LAAP_ROOT / "aris_brain"
sys.path.insert(0, str(ARIS_BRAIN))
sys.path.insert(0, str(ARIS_BRAIN / "psi_jspace_bridge"))

import requests
from mcp.server.fastmcp import FastMCP

LAAP_API_BASE = os.environ.get("LAAP_API_BASE", "http://localhost:11546")

mcp = FastMCP("laap-brain")


def _laap_post(endpoint: str, payload: dict) -> dict:
    """Call LAAP HTTP API and return JSON."""
    try:
        resp = requests.post(
            f"{LAAP_API_BASE}{endpoint}",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "source": "laap_mcp_server"}


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


def _get_dominant_need(state: dict) -> str:
    needs = state.get("needs", {})
    if not needs:
        return "explore"
    return max(needs, key=lambda k: needs.get(k, 0))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LAAP Brain MCP Server")
    parser.add_argument("--sse", action="store_true", help="Run in SSE mode")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="SSE host")
    parser.add_argument("--port", type=int, default=11547, help="SSE port")
    args = parser.parse_args()

    if args.sse:
        mcp = FastMCP("laap-brain", host=args.host, port=args.port)
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
