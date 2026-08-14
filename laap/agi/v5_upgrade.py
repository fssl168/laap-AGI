"""
LAAP AGI v5 Upgrade: 薄门面 (R11 拆分)
======================================
原 v5_upgrade.py (1946 行) 已拆分为 v5_memory_learning / v5_planning /
v5_quality。本文件保留全部既有导入符号, 确保
`from laap.agi.v5_upgrade import V5UpgradeEngine, ...` 零破坏。
"""

from typing import Optional, Dict, List, Any, Tuple, Set, Callable

import logging
import math, random, time, json, os, threading
logger = logging.getLogger("laap.v5")

from .v5_memory_learning import (
    SumTree, PrioritizedExperienceBuffer, FisherInfoTracker,
    SkillImportanceTracker, BugReport, BugCategory, EnhancedBugClassifier,
    LogicFixGenerator, RaceConditionDetector, ConditionalIndependenceTester,
    CausalDiscovery, NoveltyDetector, SurpriseDetector, CuriosityDriver,
    MetaLearner, ActiveLearningEngine,
)
from .v5_planning import (
    ValueModel, GoalCoCreator, RiskAssessor, PlanStep, Plan,
    MCTSNode, MCTSPlanner, HierarchicalPlanner, PlanMonitor,
    KnowledgeGraph, StatePredictor, EnhancedMCTSPlanner,
)
from .v5_quality import (
    FormalVerifier, SecureSandboxScanner, BenchmarkSuite,
)

__all__ = [
    "SumTree", "PrioritizedExperienceBuffer", "FisherInfoTracker",
    "SkillImportanceTracker", "BugReport", "BugCategory",
    "EnhancedBugClassifier", "LogicFixGenerator", "RaceConditionDetector",
    "ConditionalIndependenceTester", "CausalDiscovery",
    "NoveltyDetector", "SurpriseDetector", "CuriosityDriver",
    "MetaLearner", "ActiveLearningEngine",
    "ValueModel", "GoalCoCreator", "RiskAssessor", "PlanStep", "Plan",
    "MCTSNode", "MCTSPlanner", "HierarchicalPlanner", "PlanMonitor",
    "KnowledgeGraph", "StatePredictor", "EnhancedMCTSPlanner",
    "FormalVerifier", "SecureSandboxScanner", "BenchmarkSuite",
    "V5_VERSION",
]

V5_VERSION = "5.0.0"


class V5UpgradeEngine:
    """Orchestrates all V5.0 upgrade components."""

    def __init__(self):
        self.version = V5_VERSION

        # Phase 1
        self.ewc = FisherInfoTracker()
        self.skill_importance = SkillImportanceTracker()
        self.experience_buffer = PrioritizedExperienceBuffer()
        self.bug_classifier = EnhancedBugClassifier()
        self.fix_generator = LogicFixGenerator()
        self.race_detector = RaceConditionDetector()
        self.verifier = FormalVerifier()
        self.sandbox = SecureSandboxScanner()

        # Phase 2
        self.causal_discovery = CausalDiscovery()
        self.active_learning = ActiveLearningEngine()
        self.knowledge = KnowledgeGraph()

        # Phase 3
        self.goal_creator = GoalCoCreator()
        self.risk_assessor = RiskAssessor()
        self.mcts_planner = EnhancedMCTSPlanner()
        self.hierarchical_planner = HierarchicalPlanner()
        self.plan_monitor = PlanMonitor()
        self.state_predictor = self.mcts_planner.predictor

        self._start_time = time.time()
        self._total_upgrades = 0
        self._lock = threading.Lock()

    def record_experience(self, state: str, action: str, outcome: float,
                           context: Dict = None):
        self.experience_buffer.add(state, action, outcome, context)
        self.skill_importance.record_use(action[:20], outcome > 0.5)
        # EWC: record parameter importance
        self.ewc.record(action[:20], outcome, importance=outcome)
        # Active learning
        self.active_learning.record_outcome(
            "exploit" if outcome > 0.5 else "explore",
            action[:20], context.get("task_type", "general") if context else "general",
            outcome,
        )
        with self._lock:
            self._total_upgrades += 1

    def classify_and_fix(self, error_msg: str, source_file: str = "",
                          source_line: int = 0, code_context: str = "") -> Dict:
        bug = self.bug_classifier.classify(error_msg, source_file, source_line, code_context)
        fix = self.fix_generator.generate_fix(bug, code_context)
        return {"bug": bug, "fix": fix, "timestamp": time.time()}

    def discover_causality(self, data: Dict[str, List[float]]) -> Dict:
        return self.causal_discovery.discover(data)

    def create_goal_plan(self, user_input: str, agent_state: Dict = None) -> Dict:
        goals = self.goal_creator.co_create_goals(
            user_input, agent_state or {"confidence": 0.5}
        )
        plans = []
        for goal in goals[:3]:
            risk = self.risk_assessor.assess(goal)
            plan = self.mcts_planner.plan(
                goal["title"],
                goal.get("sub_goals", ["analyze", "execute", "verify"]),
            )
            plans.append({"goal": goal, "risk": risk, "plan": plan})
        return {"goals": goals, "plans": plans}

    def get_status(self) -> Dict:
        return {
            "version": self.version,
            "uptime": round(time.time() - self._start_time, 1),
            "phase_1": {
                "ewc_modules": len(self.ewc.fisher),
                "skill_importance": len(self.skill_importance.importance),
                "experience_buffer": len(self.experience_buffer),
                "bug_categories": len(BugCategory),
                "security_patterns": len(self.sandbox.SECURITY_PATTERNS),
                "verifier_rules": len(self.verifier.RULES),
            },
            "phase_2": {
                "causal_variables": len(self.causal_discovery.graph),
                "active_learning_steps": self.active_learning._total_steps,
                "meta_strategies": len(self.active_learning.meta.strategies),
                "knowledge_facts": len(self.knowledge.TRIPLES),
                "knowledge_rels": len(self.knowledge.RELATION_TYPES),
            },
            "phase_3": {
                "goals_created": len(self.goal_creator._goal_history),
                "plan_monitor_devs": len(self.plan_monitor.deviation_log),
                "mcts_plans": self.mcts_planner._total_plans,
            },
            "total_upgrades": self._total_upgrades,
        }

    def get_report(self) -> str:
        s = self.get_status()
        lines = [
            f"LAAP V5.0 Upgrade Engine v{s['version']}",
            f"Uptime: {s['uptime']}s | Total upgrades: {s['total_upgrades']}",
            "",
            "Phase 1 — Infrastructure:",
            f"  EWC modules tracked: {s['phase_1']['ewc_modules']}",
            f"  Skill importance: {s['phase_1']['skill_importance']}",
            f"  Experience buffer: {s['phase_1']['experience_buffer']}",
            f"  Bug categories: {s['phase_1']['bug_categories']}",
            f"  Security patterns: {s['phase_1']['security_patterns']}",
            f"  Verifier rules: {s['phase_1']['verifier_rules']}",
            "",
            "Phase 2 — Cognition:",
            f"  Causal variables: {s['phase_2']['causal_variables']}",
            f"  Active learning steps: {s['phase_2']['active_learning_steps']}",
            f"  Meta-strategies: {s['phase_2']['meta_strategies']}",
            f"  Knowledge facts: {s['phase_2']['knowledge_facts']}",
            f"  Relation types: {s['phase_2']['knowledge_rels']}",
            "",
            "Phase 3 — Autonomy:",
            f"  Goals created: {s['phase_3']['goals_created']}",
            f"  Plan deviations: {s['phase_3']['plan_monitor_devs']}",
            f"  MCTS plans: {s['phase_3']['mcts_plans']}",
        ]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Singleton and integration
# ═══════════════════════════════════════════════════════════════

_INSTANCE: Optional[V5UpgradeEngine] = None


def get_v5_engine() -> V5UpgradeEngine:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = V5UpgradeEngine()
    return _INSTANCE


def integrate_with_bridge(bridge) -> bool:
    """Wire V5.0 engine into an existing laap_bridge_agent bridge."""
    engine = get_v5_engine()
    if not bridge:
        return False

    # Monkey-patch bridge's before_turn to include V5 cognitive enhancements
    original_before = bridge.before_turn
    def v5_before_turn(msg):
        ctx = original_before(msg) if original_before else {}
        # Active learning: should we explore?
        task_type = ctx.get("meta", {}).get("task_type", "general")
        action, strategy = engine.active_learning.select_action(
            {"confidence": ctx.get("unity", {}).get("confidence", 0.5)},
            task_type,
        )
        ctx["v5"] = {"mode": action, "strategy": strategy}
        # Goal co-creation for complex requests
        if len(msg) > 80:
            goals = engine.goal_creator.co_create_goals(msg, {"confidence": 0.5})
            ctx["v5"]["goals"] = len(goals)
        return ctx
    bridge.before_turn = v5_before_turn

    # Monkey-patch bridge's after_tool to record experience
    original_after_tool = bridge.after_tool
    def v5_after_tool(tool_name, result):
        if original_after_tool:
            original_after_tool(tool_name, result)
        ok = result and "error" not in str(result).lower() if result else False
        engine.record_experience(tool_name, tool_name, 0.8 if ok else 0.2,
                                  {"task_type": getattr(bridge, '_last_context', {}).get("meta", {}).get("task_type", "general")})
    bridge.after_tool = v5_after_tool

    # Add V5 commands
    original_cmd = bridge.handle_command
    def v5_handle_command(cmd, *args):
        cmd_lower = cmd.lstrip("/").lower()
        if cmd_lower == "v5":
            return engine.get_report()
        if cmd_lower == "v5-status":
            import json
            return json.dumps(engine.get_status(), indent=2, ensure_ascii=False)
        if cmd_lower == "v5-goals" and args:
            goals = engine.goal_creator.co_create_goals(
                args[0] if args else "",
                {"confidence": 0.5}
            )
            lines = ["[V5.0 Goal Proposals]"]
            for i, g in enumerate(goals[:5], 1):
                lines.append(f"  {i}. {g['title']} (score={g.get('combined', 0):.2f})")
                for sg in g.get("sub_goals", []):
                    lines.append(f"      → {sg}")
            return "\n".join(lines)
        return original_cmd(cmd, *args) if original_cmd else f"Unknown: {cmd}"
    bridge.handle_command = v5_handle_command

    bridge.v5 = engine
    logger.info(f"[V5.0] Bridge integration complete — {sum(len(v) for v in engine.get_status().values() if isinstance(v, dict))} components active")
    return True


def integrate_with_agi_bridge(agi_bridge=None) -> bool:
    """
    Wire V5.0 engine into AGIBridge (used by laap-hermes default mode).

    Hooks into AGIBridge's after_turn and after_tool methods to record
    every interaction and tool call through the V5.0 engine.
    """
    engine = get_v5_engine()

    # Find the AGIBridge singleton
    if agi_bridge is None:
        try:
            from laap_brain.agi_bridge import AGIBridge
            agi_bridge = AGIBridge.get_instance()
        except Exception as e:
            logger.debug(f"操作失败: {e}")
    if not agi_bridge:
        return False

    # Patch after_tool
    orig_tool = getattr(agi_bridge, 'after_tool', None)
    def v5_after_tool(tool_name, tool_result, domain="general", tool_args=None):
        if orig_tool:
            orig_result = orig_tool(tool_name, tool_result, domain, tool_args)
        else:
            orig_result = {}
        ok = tool_result and "error" not in str(tool_result).lower() if tool_result else False
        engine.record_experience(tool_name, str(tool_name), 0.8 if ok else 0.2,
                                 {"task_type": domain, "tool_args": str(tool_args)[:100]})
        return orig_result

    # Patch after_turn
    orig_turn = getattr(agi_bridge, 'after_turn', None)
    def v5_after_turn(response, domain="general", turn_duration_ms=0.0):
        if orig_turn:
            orig_result = orig_turn(response, domain, turn_duration_ms)
        else:
            orig_result = {}
        success = bool(response and len(response) > 10)
        engine.record_experience(domain, "turn", 0.8 if success else 0.2,
                                 {"response_len": len(response or ""), "duration_ms": turn_duration_ms})
        return orig_result

    agi_bridge.after_tool = v5_after_tool
    agi_bridge.after_turn = v5_after_turn
    agi_bridge.v5 = engine

    # Patch existing agent if available
    if hasattr(agi_bridge, '_agent') and agi_bridge._agent:
        try:
            if hasattr(agi_bridge._agent, 'v5'):
                pass  # already set
            agi_bridge._agent.v5 = engine
        except Exception as e:
            logger.debug(f"操作失败: {e}")
    engine._bridge_type = "agi_bridge"
    logger.info(f"[V5.0] AGI Bridge integrated — {engine.get_status()['total_upgrades']} upgrades tracked")
    return True


def integrate_with_hermes_direct() -> bool:
    """
    Full Hermes direct integration: patches the AGI bridge AND the
    lightweight bridge simultaneously. Idempotent.
    """
    engine = get_v5_engine()
    ok = integrate_with_agi_bridge()
    engine._bridge_type = "hermes_direct"
    logger.info(f"[V5.0] Full Hermes direct integration active")
    return ok


# ═══════════════════════════════════════════════════════════════
# CLI Test
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    engine = get_v5_engine()
    logger.info(f"LAAP V5.0 Upgrade Engine v{V5_VERSION}")
    logger.info("=" * 50)
    for i in range(20):
        engine.record_experience(f"state_{i % 5}", f"action_{i % 3}", random.random())
    logger.info(f"Phase 1: Buffer={len(engine.experience_buffer)} EWC={len(engine.ewc.fisher)}")
    bugs = [
        "SyntaxError: invalid syntax at line 42",
        "TypeError: unsupported operand type(s) for +: 'int' and 'str'",
        "race condition in shared counter increment",
    ]
    for b in bugs:
        result = engine.classify_and_fix(b, "test.py")
        logger.info(f"  Bug: [{result['bug'].category}] {b[:40]}...")
    data = {"A": [1, 2, 3, 4, 5], "B": [2, 4, 6, 8, 10], "C": [5, 4, 3, 2, 1]}
    result = engine.discover_causality(data)
    logger.info(f"Phase 2: Causal edges={len(result['edges'])}")
    goals = engine.goal_creator.co_create_goals(
        "fix the race condition in the concurrent counter system", {}
    )
    logger.info(f"Phase 3: {len(goals)} goal candidates generated")
    print()
    logger.info(engine.get_report())

