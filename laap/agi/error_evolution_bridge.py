"""LAAP AGI — 故障诊断管线 → ARIS 自进化 接入桥 (ErrorEvolutionBridge, 2026-08-18)。

把量化侧错误闭环（paper_trading/error_monitor 的 system.internal.error_alert）
接入 ARIS 自进化引擎，实现 L1 事件感知 + L2 记忆沉淀 + L3 反思进化
+ L4 业务漂移防护：

  L1 事件感知: 订阅 EventBus system.internal.error_alert
               → CognitiveBus.publish(SYSTEM_FAULT) — ARIS 认知循环"看到"系统故障
  L2 记忆沉淀: 错误模式/根因/处置结果 → UnifiedMemory.encode_experience
               （与交易教训同类，供后续检索/复盘）
  L3 反思进化: 高频/高优先级错误 → MetaLearningEngine.record_session
               （记录"错误模式×处置策略"学习会话，更新策略效果统计，
                 为 recommend_strategy 提供数据，驱动 ARIS 调整处置策略）
  L4 业务漂移: 对 error_alert 载荷/主题做契约单源校验（event_bus 主题常量 +
               error_monitor 分类/根因单源），发现漂移发布 BUSINESS_DRIFT，
               并跳过非法分析的记忆/学习（fail-closed，不触碰量化业务契约）。

职责分工（2026-08-18 明确）:
  - laap/agi/self_healing.py::ErrorMonitor   代码级 bug 自愈（SyntaxError/Import 等）
  - paper_trading/error_monitor.py::ErrorScanner  运维级巡检（DB/文件/数据源/端口）
  - 本桥: 事件感知 + 记忆 + 学习 + 契约漂移防护
          （不自愈、不改代码、不下单，fail-closed）

启用（默认关，显式 env）:
  LAAP_ERROR_EVOLVE=1  → EventOrchestrator 挂载本桥
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from laap.agi.cognitive_bus import CognitiveBus, CognitiveEventType, get_bus
from laap.agi.unified_memory import UnifiedMemory, MemoryPriority
from laap.agi.meta_learning import MetaLearningEngine

logger = logging.getLogger("laap.agi.error_evolution")

# 高优先级阈值（priority>=2 触发 L3 反思）
_HIGH_PRIORITY = 2
# 单次事件最多沉淀/学习的错误类别数（防 ARIS 被刷屏）
_MAX_PER_EVENT = 5
# 单次事件最多上报的漂移明细数（防刷屏）
_MAX_DRIFT_VIOLATIONS = 20
# analysis 必需字段（error_monitor.analyze 输出契约）
_REQUIRED_ANALYSIS_FIELDS = ("category", "root_cause", "count", "priority",
                             "action")


class ErrorEvolutionBridge:
    """故障诊断管线 → ARIS 自进化 接入桥（L1+L2+L3+L4）。"""

    def __init__(self, bus: Optional[Any] = None,
                 cognitive_bus: Optional[CognitiveBus] = None,
                 memory: Optional[UnifiedMemory] = None,
                 meta: Optional[MetaLearningEngine] = None):
        from laap.paper_trading.event_bus import EventBus, T_SYSTEM_INTERNAL
        self.bus = bus or EventBus()
        self._error_alert_topic = f"{T_SYSTEM_INTERNAL}.error_alert"
        self.cognitive_bus = cognitive_bus or get_bus("aris")
        self.memory = memory or UnifiedMemory()
        self.meta = meta or MetaLearningEngine()
        self._sid: Optional[str] = None
        self.faults_notified = 0
        self.memories_written = 0
        self.sessions_recorded = 0
        # L4 业务漂移防护统计
        self.drift_checks = 0
        self.drift_violations = 0
        self.last_drift: Optional[Dict[str, Any]] = None

    # ── 生命周期 ──────────────────────────────────────────

    def attach(self) -> None:
        if self._sid is not None:
            return
        self._sid = self.bus.subscribe(self._error_alert_topic,
                                       self._handle_error_alert)
        logger.info("ErrorEvolutionBridge attached (L1+L2+L3+L4)")

    def detach(self) -> None:
        if self._sid is not None:
            self.bus.unsubscribe(self._sid)
            self._sid = None

    # ── L4 业务漂移防护（契约单源校验）─────────────────────

    def _check_business_drift(self, ev: Any) -> List[str]:
        """校验故障事件是否与契约单源一致。

        校验点:
          - 事件主题必须以 event_bus.T_SYSTEM_INTERNAL 为前缀（禁止平行硬编码）
          - payload.analyses 必须是 list，且每项包含 error_monitor.analyze
            输出所需字段
          - category / root_cause 必须来自 error_monitor 单源定义
            （CATEGORY_PATTERNS / ROOT_CAUSE_ACTIONS）
          - counts 与 analyses.count 不一致视为载荷漂移

        返回违规描述列表（空=无漂移）。
        """
        self.drift_checks += 1
        violations: List[str] = []
        topic = getattr(ev, "type", "")
        if topic != self._error_alert_topic:
            violations.append(
                f"event_topic_drift: {topic!r} != {self._error_alert_topic!r}")

        from laap.paper_trading import error_monitor as _em
        known_categories = set(_em.CATEGORY_PATTERNS)
        known_root_causes = set(_em.ROOT_CAUSE_ACTIONS)

        raw_payload = getattr(ev, "payload", None)
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        analyses = payload.get("analyses") if isinstance(payload, dict) else None
        counts = payload.get("counts") if isinstance(payload, dict) else None
        if not isinstance(analyses, list):
            violations.append("analyses_type_drift: 期望 list")
            return violations

        if not isinstance(counts, dict):
            if counts is not None:
                violations.append("counts_type_drift: 期望 dict")

        for i, a in enumerate(analyses):
            if not isinstance(a, dict):
                violations.append(f"analyses[{i}]_type_drift: 期望 dict")
                continue
            for field in _REQUIRED_ANALYSIS_FIELDS:
                if field not in a:
                    violations.append(f"analyses[{i}]_missing_field:{field}")
            cat = a.get("category", "")
            if cat and cat not in known_categories:
                violations.append(f"analyses[{i}]_unknown_category:{cat}")
            rc = a.get("root_cause", "")
            if rc and rc not in known_root_causes:
                violations.append(f"analyses[{i}]_unknown_root_cause:{rc}")
            if (isinstance(counts, dict) and cat in counts
                    and counts.get(cat) != a.get("count")):
                violations.append(
                    f"analyses[{i}]_count_drift:{cat} counts="
                    f"{counts.get(cat)} != analysis.count={a.get('count')}")

        return violations

    def _publish_business_drift(self, violations: List[str],
                                summary: str) -> None:
        """L4: 将业务/契约漂移发布到认知总线（ARIS 可感知）。"""
        try:
            self.cognitive_bus.publish(
                CognitiveEventType.BUSINESS_DRIFT, "error_evolution_bridge",
                {
                    "violations": violations[:_MAX_DRIFT_VIOLATIONS],
                    "summary": summary,
                    "ts": time.time(),
                })
        except Exception as e:
            logger.warning(f"ErrorEvolutionBridge L4 publish failed: {e}")

    # ── L1 事件感知 ────────────────────────────────────────

    def _handle_error_alert(self, ev: Any) -> None:
        raw_payload = getattr(ev, "payload", None)
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        raw_analyses = payload.get("analyses")
        analyses = raw_analyses if isinstance(raw_analyses, list) else []
        raw_counts = payload.get("counts")
        counts = raw_counts if isinstance(raw_counts, dict) else {}
        summary = payload.get("summary", "")

        # L4: 先做业务漂移/契约校验；违规分析不进入记忆/学习（fail-closed）
        violations = self._check_business_drift(ev)
        if violations:
            self.drift_violations += 1
            self.last_drift = {
                "ts": time.time(),
                "violations": violations[:_MAX_DRIFT_VIOLATIONS],
            }
            self._publish_business_drift(violations, summary)
            logger.warning("ErrorEvolutionBridge L4 drift: %s", violations)

        from laap.paper_trading import error_monitor as _em
        known_root_causes = set(_em.ROOT_CAUSE_ACTIONS)
        valid = []
        for a in analyses:
            if not isinstance(a, dict):
                continue
            if (all(f in a for f in _REQUIRED_ANALYSIS_FIELDS)
                    and a.get("root_cause") in known_root_causes):
                valid.append(a)
        analyses = valid

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
            "drift_checks": self.drift_checks,
            "drift_violations": self.drift_violations,
            "last_drift": self.last_drift,
        }
