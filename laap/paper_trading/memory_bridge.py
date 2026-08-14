"""LAAP Paper Trading — 记忆桥接（闭环 A 后半）。

把纸面交易的结果/教训沉淀进 UnifiedMemory，并在新决策时检索注入。
复用 AGI 记忆接口:
  encode_experience(content, ..., context_triggers)  — 教训沉淀
  query / retrieve_context                            — 经验检索
  generate_memory_prompt                              — 注入决策 prompt
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from laap.paper_trading.db import PaperDB
from laap.paper_trading.models import OutcomeRecord

logger = logging.getLogger("laap.paper_trading.memory_bridge")


def lesson_to_experience(outcome: OutcomeRecord, symbol: str = "") -> str:
    """outcome + lesson → 经验文本（供 UnifiedMemory 编码）。"""
    sym = f" {symbol}" if symbol else ""
    return (f"[{outcome.lesson_type}]{sym} 交易结果 pnl={outcome.pnl_pct:.2%} "
            f"hold={outcome.hold_days}d vs_expected={outcome.vs_expected}: "
            f"{outcome.lesson}")


def encode_lesson(memory: Any, outcome: OutcomeRecord, symbol: str = "") -> str:
    """教训沉淀 → memory.encode_experience，返回 episode_id。

    context_triggers=[symbol, lesson_type]，使经验可按标的/教训类型检索。
    """
    content = lesson_to_experience(outcome, symbol)
    try:
        result = memory.encode_experience(
            content,
            emotional_valence=-0.3 if outcome.pnl_pct < 0 else 0.3,
            emotional_arousal=0.5,
            context_triggers=[symbol, outcome.lesson_type],
        )
        return result.get("episode_id", "")
    except Exception as e:
        logger.warning(f"encode_lesson failed: {e}")
        return ""


def retrieve_for_symbol(memory: Any, symbol: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """同标的经验检索。"""
    try:
        return memory.query(symbol, max_results=max_results)
    except Exception as e:
        logger.warning(f"retrieve_for_symbol failed: {e}")
        return []


def inject_memory_prompt(memory: Any, symbol: str, action: str) -> str:
    """生成注入决策的 memory prompt（历史经验/概念/技能）。"""
    context_text = f"{symbol} {action}"
    try:
        return memory.generate_memory_prompt(context_text)
    except Exception as e:
        logger.warning(f"inject_memory_prompt failed: {e}")
        return "No relevant memory context available."


def verify_lessons(db: PaperDB, lesson_type: str, min_confirm: int = 2) -> Dict[str, Any]:
    """教训校验：同 lesson_type 累计 >= min_confirm 次真实平仓才 verified=True。

    防单笔噪声污染：未达阈值的教训保持 verified=False，不参与推理（或仅弱信号）。
    """
    conn = db.conn()
    try:
        rows = conn.execute(
            "SELECT trade_id FROM outcomes WHERE lesson_type=?", (lesson_type,)).fetchall()
        count = len(rows)
        verified = count >= min_confirm
        if verified:
            conn.execute(
                "UPDATE outcomes SET verified=1 WHERE lesson_type=?", (lesson_type,))
            conn.commit()
    finally:
        conn.close()
    return {"lesson_type": lesson_type, "count": count, "verified": verified}


def verified_lessons(db: PaperDB) -> List[Dict[str, Any]]:
    """查询已校验（verified=1）的教训，供推理弱信号/审计。"""
    conn = db.conn()
    try:
        rows = conn.execute(
            "SELECT * FROM outcomes WHERE verified=1").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
