"""LAAP AGI — 故障诊断管线 → ARIS 自进化 接入桥 (ErrorEvolutionBridge, 2026-08-18)。

把量化侧错误闭环（paper_trading/error_monitor 的 system.internal.error_alert）
接入 ARIS 自进化引擎，实现 L1 事件感知 + L2 记忆沉淀 + L3 反思进化：

  L1 事件感知: 订阅 EventBus system.internal.error_alert
               → CognitiveBus.publish(SYSTEM_FAULT) — ARIS 认知循环"看到"系统故障
  L2 记忆沉淀: 错误模式/根因/处置结果 → UnifiedMemory.encode_experience
               （与交易教训同类，供后续检索/复盘）
  L3 反思进化: 高频/高优先级错误 → MetaLearningEngine.record_session
               （记录"错误模式×处置策略"学习会话，更新策略效果统计，
                 为 recommend_strategy 提供数据，驱动 ARIS 调整处置策略）

职责分工（2026-08-18 明确）:
  - laap/agi/self_healing.py::ErrorMonitor   代码级 bug 自愈（SyntaxError/Import 等）
  - paper_trading/error_monitor.py::ErrorScanner  运维级巡检（DB/文件/数据源/端口）
  - 本桥: 事件感知 + 记忆 + 学习（不自愈、不改代码、不下单，fail-closed）

启用（默认关，显式 env）:
  LAAP_ERROR_EVOLVE=1  → EventOrchestrator 挂载本桥
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from laap.agi.cognitive_bus import CognitiveBus, CognitiveEventType, get_bus
from laap.agi.unified_memory import UnifiedMemory, MemoryPriority
from laap.agi.meta_learning import MetaLearningEngine

logger = logging.getLogger("laap.agi.error_evolution")

# 高优先级阈值（priority>=2 触发 L3 反思）
_HIGH_PRIORITY = 2
# 单次事件最多沉淀/学习的错误类别数（防 ARIS 被刷屏）
_MAX_PER_EVENT = 5


class ErrorEvolutionBridge:
    """故障诊断管线 → ARIS 自进化 接入桥（L1+L2+L3）。"""

    def __init__(self, bus: Optional[Any] = None,
                 cognitive_bus: Optional[CognitiveBus] = None,
                 memory: Optional[UnifiedMemory] = None,
                 meta: Optional[MetaLearningEngine] = None):
        from laap.paper_trading.event_bus import EventBus
        self.bus = bus or EventBus()
        self.cognitive_bus = cognitive_bus or get_bus("aris")
        self.memory = memory or UnifiedMemory()
        self.meta = meta or MetaLearningEngine()
        self._sid: Optional[str] = None
        self.faults_notified = 0
        self.memories_written = 0
        self.sessions_recorded = 0

    # ── 生命周期 ──────────────────────────────────────────

    def attach(self) -> None:
        if self._sid is not None:
            return
        self._sid = self.bus.subscribe("system.internal.error_alert",
                                       self._handle_error_alert)
        logger.info("ErrorEvolutionBridge attached (L1+L2+L3)")

    def detach(self) -> None:
        if self._sid is not None:
            self.bus.unsubscribe(self._sid)
            self._sid = None

    # ── L1 事件感知 ────────────────────────────────────────

    def _handle_error_alert(self, ev: Any) -> None:
        payload = ev.payload or {}
        analyses = payload.get("analyses") or []
        counts = payload.get("counts") or {}
        summary = payload.get("summary", "")
        high = [a for a in analyses if a.get("priority", 0) >= _HIGH_PRIORITY]
        try:
            # L1: 认知总线发布 SYSTEM_FAULT（ARIS 感知）
            self.cognitive_bus.publish(
                CognitiveEventType.SYSTEM_FAULT, "error_evolution_bridge",
                {
                    "summary": summary,
                    "high_priority": [a["category"] for a in high][:_MAX_PER_EVENT],
                    "categories": {k: v for k, v in list(counts.items())[:_MAX_PER_EVENT]},
                    "ts": time.time(),
                })
            self.faults_notified += 1
        except Exception as e:
            logger.warning(f"ErrorEvolutionBridge L1 publish failed: {e}")

        # L2 + L3: 对每类高优先级/高发错误沉淀记忆 + 记录学习会话
        for a in (high or analyses)[:_MAX_PER_EVENT]:
            try:
                self._remember_and_learn(a, summary)
            except Exception as e:
                logger.warning(f"ErrorEvolutionBridge L2/L3 failed for "
                               f"{a.get('category')}: {e}")

    # ── L2 记忆沉淀 ────────────────────────────────────────

    def _remember_and_learn(self, analysis: Dict[str, Any],
                            summary: str) -> None:
        cat = analysis.get("category", "unknown")
        rc = analysis.get("root_cause", "unknown")
        cnt = analysis.get("count", 1)
        action = analysis.get("action", "")
        sample = analysis.get("sample", "")[:120]
        auto = rc in ("partial_source_failure", "report_source_failure",
                      "source_chain_failure")

        content = (
            f"系统故障教训: {cat}（根因 {rc}）近窗口 {cnt} 次"
            f"{'，已自动处置(数据源降级)' if auto else '，需人工'}。"
            f"处置建议: {action[:60]}。样例: {sample}"
        )
        # L2: 统一记忆沉淀（情绪负值=故障, 优先级按是否需人工）
        self.memory.encode_experience(
            content=content,
            emotional_valence=-0.5 if auto else -0.9,
            emotional_arousal=0.4 if auto else 0.7,
            priority=(MemoryPriority.IMPORTANT if not auto
                      else MemoryPriority.RELEVANT),
            context_triggers=[f"system_fault:{cat}", f"root_cause:{rc}"],
        )
        self.memories_written += 1

        # L3: 元学习记录（错误模式 × 处置策略 → 策略效果统计）
        strategy = "auto_fallback" if auto else "manual_review"
        # OPERATIONAL 为运维处置通用策略（2026-08-18 加入枚举）; 以 successful 区分
        # 自动(成功) / 人工(待复盘), 不新增平行枚举值。
        self.meta.record_session(
            concept=f"fault:{rc}",
            strategy="operational",
            duration_minutes=5.0,
            mastery_before=0.2 if auto else 0.5,
            mastery_after=0.9 if auto else 0.3,
            difficulty=0.3 if auto else 0.8,
            successful=auto,
            domain="system_ops",
            notes=f"{cat} x{cnt} | {strategy} | {summary[:60]}",
        )
        self.sessions_recorded += 1

    # ── 查询 ──────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "attached": self._sid is not None,
            "faults_notified": self.faults_notified,
            "memories_written": self.memories_written,
            "sessions_recorded": self.sessions_recorded,
        }
