"""
LAAP AGI v5 Upgrade: 规划层 (R11 拆分)
====================================
原 v5_upgrade.py (1946 行) 拆分出的子模块之一。
完整拆分: v5_memory_learning.py(记忆/学习) / v5_planning.py(规划) /
          v5_quality.py(质量/安全/基准) /
          v5_upgrade.py(薄门面, 既有导入零破坏)。
"""

from __future__ import annotations

import logging
import sys, os, json, re, math, time, random, hashlib, itertools, heapq
from pathlib import Path
from typing import Optional, Any, Dict, List, Tuple, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque

import numpy as np

logger = logging.getLogger("laap.agi.v5_upgrade")


# ═══ 规划层 (自原 v5_upgrade.py 拆分) ═══
class ValueModel:
    """Represents agent's values and preferences."""

    def __init__(self):
        self.values = {
            "effectiveness": 1.0,
            "safety": 1.0,
            "creativity": 0.7,
            "thoroughness": 0.8,
            "efficiency": 0.6,
        }
        self.preferences: Dict[str, float] = {}

    def evaluate(self, goal: Dict) -> float:
        score = 0.0
        if goal.get("takes_risks", False):
            score -= 0.3 * (1 - self.values["safety"])
        if goal.get("is_thorough", False):
            score += 0.2 * self.values["thoroughness"]
        if goal.get("is_creative", False):
            score += 0.2 * self.values["creativity"]
        if goal.get("is_efficient", False):
            score += 0.2 * self.values["efficiency"]
        return max(0.0, min(1.0, 0.5 + score))


class GoalCoCreator:
    """Co-creates goals with value alignment checks."""

    def __init__(self):
        self.value_model = ValueModel()
        self._goal_history: List[Dict] = []

    def generate_candidates(self, user_intent: str,
                            agent_state: Dict) -> List[Dict]:
        """Generate goal candidates from intent + state."""
        intent_lower = user_intent.lower()
        candidates = []
        keywords = {
            "fix": {"description": "Debug and repair", "is_thorough": True, "takes_risks": False},
            "build": {"description": "Create new solution", "is_creative": True, "is_thorough": True},
            "analyze": {"description": "In-depth analysis", "is_thorough": True},
            "search": {"description": "Find information", "is_efficient": True},
            "improve": {"description": "Optimize existing", "is_efficient": True, "takes_risks": False},
            "learn": {"description": "Acquire new knowledge", "is_creative": True},
        }
        for keyword, attrs in keywords.items():
            if keyword in intent_lower:
                goal = {
                    "title": f"{attrs['description']} related to: {user_intent[:50]}",
                    "description": user_intent[:100],
                    "sub_goals": [
                        f"Understand scope of: {user_intent[:40]}",
                        f"Plan approach for: {user_intent[:40]}",
                        f"Execute and verify",
                    ],
                    "success_criteria": ["Task completed", "Result verified", "No regressions"],
                    **attrs,
                }
                goal["value_score"] = self.value_model.evaluate(goal)
                goal["alignment_score"] = 0.8 if goal["value_score"] > 0.5 else 0.3
                goal["combined"] = round(goal["value_score"] * 0.6 + goal["alignment_score"] * 0.4, 3)
                candidates.append(goal)

        if not candidates:
            candidates.append({
                "title": f"Process: {user_intent[:60]}",
                "description": user_intent[:100],
                "sub_goals": ["Analyze request", "Determine approach", "Execute", "Verify"],
                "success_criteria": ["Request handled", "User satisfied"],
                "is_thorough": True, "takes_risks": False, "is_creative": False, "is_efficient": True,
                "value_score": 0.6, "alignment_score": 0.7, "combined": 0.64,
            })

        candidates.sort(key=lambda x: -x["combined"])
        return candidates[:5]

    def co_create_goals(self, user_input: str,
                         agent_state: Dict) -> List[Dict]:
        goals = self.generate_candidates(user_input, agent_state)
        self._goal_history.append({
            "input": user_input[:80], "goals": len(goals), "time": time.time(),
        })
        return goals


class RiskAssessor:
    """Assesses risks for goals and actions."""

    RISK_PATTERNS = [
        ("destructive", ["delete", "remove", "drop", "rm -rf", "format"]),
        ("network", ["deploy", "publish", "push", "upload", "send"]),
        ("security", ["chmod", "sudo", "admin", "password", "token"]),
        ("data_loss", ["overwrite", "replace", "truncate", "clear"]),
    ]

    def assess(self, goal: Dict) -> Dict:
        risks = []
        text = (goal.get("title", "") + " " + goal.get("description", "")).lower()
        for risk_type, patterns in self.RISK_PATTERNS:
            for pattern in patterns:
                if pattern in text:
                    risks.append({
                        "type": risk_type,
                        "pattern": pattern,
                        "severity": "high" if risk_type in ("destructive", "data_loss") else "medium",
                        "likelihood": 0.4 if risk_type == "destructive" else 0.2,
                    })
        overall = 0.0
        if risks:
            overall = sum(r.get("likelihood", 0.1) for r in risks) / len(risks)
        return {"risks": risks, "overall_risk": round(overall, 3), "safe": overall < 0.5}

    def can_proceed(self, goal: Dict) -> Tuple[bool, str]:
        assessment = self.assess(goal)
        if not assessment["safe"]:
            return False, f"Risk assessment: {len(assessment['risks'])} risks found"
        return True, "Safe to proceed"


# ═══════════════════════════════════════════════════════════════
# Phase 3b: Long-Term Planning (MCTS-based)
# ═══════════════════════════════════════════════════════════════

@dataclass
class PlanStep:
    action: str = ""
    expected_outcome: str = ""
    duration_estimate: float = 1.0
    dependencies: List[int] = field(default_factory=list)
    completed: bool = False
    status: str = "pending"


@dataclass
class Plan:
    goal: str = ""
    steps: List[PlanStep] = field(default_factory=list)
    created_at: float = 0.0
    horizon: int = 5

    def progress(self) -> float:
        if not self.steps:
            return 0.0
        return sum(1 for s in self.steps if s.completed) / len(self.steps)


class MCTSNode:
    """Monte Carlo Tree Search node."""

    def __init__(self, state: str, action: str = "", parent: Optional["MCTSNode"] = None):
        self.state = state
        self.action = action
        self.parent = parent
        self.children: List["MCTSNode"] = []
        self.visits = 0
        self.value = 0.0
        self.depth = parent.depth + 1 if parent else 0

    def ucb_score(self, exploration_param: float = 1.4) -> float:
        if self.visits == 0:
            return float('inf')
        exploitation = self.value / self.visits
        if self.parent and self.parent.visits > 0:
            exploration = exploration_param * math.sqrt(math.log(self.parent.visits) / self.visits)
        else:
            exploration = 0
        return exploitation + exploration

    def best_child(self) -> Optional["MCTSNode"]:
        if not self.children:
            return None
        return max(self.children, key=lambda c: c.ucb_score())


class MCTSPlanner:
    """Monte Carlo Tree Search for action planning with state-based simulation."""

    def __init__(self, n_simulations: int = 50, exploration: float = 1.4):
        self.n_simulations = n_simulations
        self.exploration = exploration
        self._state_history: Dict[str, float] = {}
        self._action_outcomes: Dict[str, List[float]] = defaultdict(list)

    def _predict_next_state(self, current_state: str, action: str,
                            state_predictor: Optional[Callable] = None) -> str:
        """基于状态预测器预测下一状态"""
        if state_predictor:
            try:
                return state_predictor(current_state, action)
            except Exception as e:
                logger.debug(f"操作失败: {e}")
        key = f"{current_state[:30]}|{action[:20]}"
        if key in self._state_history:
            # 已有经验，预测往好的方向发展
            last_outcome = self._state_history[key]
            if last_outcome > 0.6:
                return f"{current_state} → {action}[success]"
            elif last_outcome > 0.3:
                return f"{current_state} → {action}[partial]"
            else:
                return f"{current_state} → {action}[failed]"
        return f"{current_state} → {action}"

    def _evaluate_state(self, state: str, goal: str) -> float:
        """
        基于目标评估当前状态
        返回0.0-1.0的分数
        """
        goal_keywords = set(goal.lower().split())
        state_lower = state.lower()

        # 关键词匹配度
        matches = sum(1 for kw in goal_keywords if kw in state_lower)
        keyword_score = min(1.0, matches / max(len(goal_keywords), 1))

        # 状态标记评分
        if "[success]" in state or "completed" in state_lower:
            success_score = 1.0
        elif "[partial]" in state:
            success_score = 0.5
        elif "[failed]" in state or "error" in state_lower:
            success_score = 0.0
        else:
            # 中间状态：根据深度和进度评估
            arrows = state.count("→")
            depth_penalty = min(0.3, arrows * 0.05)
            success_score = 0.5 - depth_penalty

        # 目标完成度
        goal_progress = 0.0
        if goal[:20].lower() in state_lower:
            goal_progress = 0.7 + 0.3 * min(1.0, arrows / 5.0)

        return (keyword_score * 0.3 + success_score * 0.4 + goal_progress * 0.3)

    def _compute_reward(self, prev_state: str, action: str, next_state: str,
                        goal: str, state_predictor: Optional[Callable] = None) -> float:
        """
        计算状态转移的奖励值
        基于状态质量差异 + 动作效果 + 目标接近度
        """
        prev_score = self._evaluate_state(prev_state, goal)
        next_score = self._evaluate_state(next_state, goal)

        # 主奖励：状态质量提升
        state_reward = next_score - prev_score

        # 动作成本惩罚（鼓励简洁方案）
        action_cost = 0.02

        # 成功完成惩罚
        if "[success]" in next_state or "completed" in next_state.lower():
            completion_bonus = 0.3
        elif "[failed]" in next_state:
            completion_bonus = -0.2
        else:
            completion_bonus = 0.0

        # 学习记录
        key = f"{prev_state[:30]}|{action[:20]}"
        self._action_outcomes[key].append(next_score)
        if len(self._action_outcomes[key]) > 20:
            self._action_outcomes[key] = self._action_outcomes[key][-20:]

        final_reward = state_reward - action_cost + completion_bonus
        return max(-0.5, min(1.0, final_reward))

    def plan(self, goal: str, actions: List[str],
             state_predictor: Optional[Callable] = None) -> Plan:
        """基于MCTS生成最优计划"""
        actions_sorted = sorted(set(actions))[:5]
        if not actions_sorted:
            return Plan(goal=goal, created_at=time.time(), horizon=0)

        # 初始化根节点
        initial_state = f"Goal: {goal[:50]}"
        root = MCTSNode(state=initial_state, action="", parent=None)

        # MCTS搜索
        for sim in range(self.n_simulations):
            # 1. Selection：从根到叶，选择最优子节点
            node = self._uct_select(root)

            # 2. Expansion：如果未完全展开，添加子节点
            if len(node.children) < len(actions_sorted):
                remaining_actions = [a for a in actions_sorted
                                     if not any(c.action == a for c in node.children)]
                if remaining_actions:
                    action = remaining_actions[0]
                    next_state = self._predict_next_state(
                        node.state, action, state_predictor
                    )
                    child = MCTSNode(state=next_state, action=action, parent=node)
                    node.children.append(child)
                    node = child

            # 3. Simulation：从当前状态模拟到终止或深度限制
            reward = self._simulate_with_prediction(
                node, goal, state_predictor, max_depth=5
            )

            # 4. Backpropagation：更新所有祖先节点
            self._backpropagate(node, reward)

        # 从最优路径构建计划
        plan = Plan(goal=goal, created_at=time.time(), horizon=len(actions_sorted))
        current = root
        step_idx = 0

        while current.children:
            best = max(current.children, key=lambda c: c.visits)
            if best.action:
                plan.steps.append(PlanStep(
                    action=best.action,
                    expected_outcome=self._predict_outcome_description(best.state),
                    duration_estimate=1.0 + (1.0 - self._evaluate_state(best.state, goal)) * 2,
                    dependencies=[i for i in range(step_idx)] if step_idx > 0 else [],
                ))
                step_idx += 1
            current = best
            if step_idx >= len(actions_sorted):
                break

        return plan

    def _uct_select(self, node: MCTSNode) -> MCTSNode:
        """UCB1选择策略"""
        while node.children:
            # 选择UCB分数最高的子节点
            selected = max(node.children, key=lambda c: c.ucb_score(self.exploration))
            if selected.visits == 0:
                return node
            node = selected
        return node

    def _simulate_with_prediction(self, start_node: MCTSNode, goal: str,
                                  state_predictor: Optional[Callable],
                                  max_depth: int = 5) -> float:
        """
        基于状态预测的模拟
        沿用学到的状态转移模型模拟多步后的最终奖励
        """
        total_reward = 0.0
        gamma = 0.9  # 折扣因子
        current_state = start_node.state

        for depth in range(max_depth):
            # 获取可能的动作（从历史或默认）
            possible_actions = list(self._action_outcomes.keys())
            if not possible_actions:
                possible_actions = ["explore", "analyze", "execute", "verify", "complete"]

            # 基于UCB选择动作
            action = max(possible_actions[:5],
                        key=lambda a: self._get_action_ucb(a, current_state))

            # 预测下一状态
            next_state = self._predict_next_state(
                current_state, action, state_predictor
            )

            # 计算即时奖励
            reward = self._compute_reward(
                current_state, action, next_state, goal, state_predictor
            )

            # 折扣累计
            total_reward += (gamma ** depth) * reward

            # 检查终止条件
            state_score = self._evaluate_state(next_state, goal)
            if state_score >= 0.9 or "[failed]" in next_state:
                break

            current_state = next_state

        return max(0.0, min(1.0, total_reward))

    def _get_action_ucb(self, action: str, state: str) -> float:
        """计算动作的UCB分数（用于模拟中的动作选择）"""
        outcomes = self._action_outcomes.get(
            f"{state[:30]}|{action[:20]}", [0.5]
        )
        avg = sum(outcomes) / len(outcomes)
        visits = len(outcomes)
        exploration_bonus = self.exploration * math.sqrt(math.log(max(1, visits) + 1) / max(1, visits))
        return avg + exploration_bonus

    def _predict_outcome_description(self, state: str) -> str:
        """生成人类可读的结果描述"""
        if "[success]" in state or "completed" in state.lower():
            return "目标成功完成"
        elif "[partial]" in state:
            return "部分完成"
        elif "[failed]" in state or "error" in state.lower():
            return "执行失败"
        else:
            arrows = state.count("→")
            return f"执行中 (进度: {min(100, arrows * 20)}%)"

    def _backpropagate(self, node: MCTSNode, reward: float):
        """反向传播更新访问次数和价值"""
        while node:
            node.visits += 1
            node.value += reward
            node = node.parent

    def select(self, root: MCTSNode) -> MCTSNode:
        """选择最优子节点"""
        node = root
        while node.children:
            node = self.best_child(node) or node
            if node.visits == 0:
                break
        return node

    def expand(self, node: MCTSNode, actions: List[str],
               state_predictor: Optional[Callable] = None):
        """展开节点添加子节点"""
        for action in actions[:4]:
            next_state = self._predict_next_state(node.state, action, state_predictor)
            child = MCTSNode(state=next_state, action=action, parent=node)
            node.children.append(child)

    def simulate(self, node: MCTSNode, depth: int = 3) -> float:
        """兼容旧接口：使用新的预测模拟"""
        return self._simulate_with_prediction(node, "", None, max_depth=depth)

    def backpropagate(self, node: MCTSNode, reward: float):
        """兼容旧接口"""
        self._backpropagate(node, reward)


class HierarchicalPlanner:
    """Three-level hierarchical planner."""

    def plan_high(self, goal: str) -> List[str]:
        return [
            f"Phase 1: Prepare — analyze {goal[:30]}",
            f"Phase 2: Execute — implement {goal[:30]}",
            f"Phase 3: Verify — validate {goal[:30]}",
        ]

    def plan_mid(self, high_phase: str) -> List[str]:
        return [
            f"Step 1: {high_phase[:30]} — gather inputs",
            f"Step 2: {high_phase[:30]} — process",
            f"Step 3: {high_phase[:30]} — review",
        ]

    def plan_low(self, mid_step: str) -> List[str]:
        return [
            f"Action: {mid_step[:30]}",
            f"Check: {mid_step[:30]} result",
        ]


class PlanMonitor:
    """Monitor plan execution and detect deviations."""

    def __init__(self, tolerance: float = 0.3):
        self.tolerance = tolerance
        self.deviation_log: List[Dict] = []

    def check_progress(self, plan: Plan, actual_state: Dict) -> Tuple[float, List[str]]:
        expected = plan.progress()
        deviations = []
        for step in plan.steps:
            if step.status == "pending":
                expected_done = plan.created_at + sum(
                    s.duration_estimate for s in plan.steps[:plan.steps.index(step)]
                )
                if time.time() > expected_done + self.tolerance * expected_done:
                    deviations.append(f"Step '{step.action[:30]}' behind schedule")
        return expected, deviations

    def check_deviation(self, plan: Plan, actual_state: Dict) -> bool:
        progress, deviations = self.check_progress(plan, actual_state)
        if deviations:
            self.deviation_log.append({
                "time": time.time(), "plan": plan.goal[:30],
                "deviations": deviations, "progress": progress,
            })
            return True
        return False


# ═══════════════════════════════════════════════════════════════
# Commonsense Knowledge Graph (no external API needed)
# ═══════════════════════════════════════════════════════════════

class KnowledgeGraph:
    """Built-in commonsense knowledge base with ~300 relational triples.

    Replaces external ConceptNet/ATOMIC APIs with curated local knowledge.
    Supports: IsA, PartOf, CapableOf, HasProperty, Causes, AtLocation, RelatedTo
    """

    TRIPLES: List[Tuple[str, str, str]] = [
        # ── IsA (concept hierarchy) ──
        ("python", "IsA", "programming language"), ("java", "IsA", "programming language"),
        ("c++", "IsA", "programming language"), ("javascript", "IsA", "scripting language"),
        ("rust", "IsA", "systems language"), ("sql", "IsA", "query language"),
        ("html", "IsA", "markup language"), ("css", "IsA", "stylesheet language"),
        ("docker", "IsA", "container platform"), ("git", "IsA", "version control"),
        ("linux", "IsA", "operating system"), ("windows", "IsA", "operating system"),
        ("database", "IsA", "data store"), ("api", "IsA", "interface"),
        ("thread", "IsA", "execution unit"), ("process", "IsA", "execution unit"),
        ("lock", "IsA", "synchronization primitive"), ("mutex", "IsA", "lock"),
        ("semaphore", "IsA", "synchronization primitive"),
        ("variable", "IsA", "data container"), ("function", "IsA", "code unit"),
        ("class", "IsA", "code unit"), ("module", "IsA", "code unit"),
        ("package", "IsA", "code collection"), ("library", "IsA", "code collection"),
        ("framework", "IsA", "code collection"), ("protocol", "IsA", "communication standard"),
        ("algorithm", "IsA", "procedure"), ("data structure", "IsA", "data organization"),
        ("array", "IsA", "data structure"), ("list", "IsA", "data structure"),
        ("dict", "IsA", "data structure"), ("hash map", "IsA", "dictionary"),
        ("tree", "IsA", "data structure"), ("graph", "IsA", "data structure"),
        ("queue", "IsA", "data structure"), ("stack", "IsA", "data structure"),
        ("server", "IsA", "computer"), ("client", "IsA", "computer"),
        ("cache", "IsA", "temporary storage"), ("buffer", "IsA", "temporary storage"),
        ("compiler", "IsA", "translator"), ("interpreter", "IsA", "executor"),
        ("debugger", "IsA", "development tool"), ("test", "IsA", "verification method"),
        # ── PartOf ──
        ("cpu", "PartOf", "computer"), ("gpu", "PartOf", "computer"),
        ("ram", "PartOf", "computer"), ("disk", "PartOf", "computer"),
        ("function", "PartOf", "module"), ("class", "PartOf", "module"),
        ("method", "PartOf", "class"), ("attribute", "PartOf", "class"),
        ("statement", "PartOf", "function"), ("expression", "PartOf", "statement"),
        ("loop", "PartOf", "algorithm"), ("condition", "PartOf", "algorithm"),
        # ── CapableOf ──
        ("function", "CapableOf", "return value"), ("loop", "CapableOf", "iterate data"),
        ("condition", "CapableOf", "branch execution"), ("lock", "CapableOf", "prevent race condition"),
        ("mutex", "CapableOf", "protect critical section"),
        ("cache", "CapableOf", "speed up access"), ("buffer", "CapableOf", "temporary data hold"),
        ("database", "CapableOf", "persist data"), ("api", "CapableOf", "enable communication"),
        ("thread", "CapableOf", "concurrent execution"),
        ("recursion", "CapableOf", "solve divide-conquer problems"),
        ("sorting", "CapableOf", "arrange data in order"),
        ("searching", "CapableOf", "find data by key"),
        ("encryption", "CapableOf", "protect data confidentiality"),
        ("testing", "CapableOf", "verify correctness"),
        ("logging", "CapableOf", "record events"),
        # ── HasProperty ──
        ("python", "HasProperty", "interpreted"), ("java", "HasProperty", "compiled"),
        ("c++", "HasProperty", "fast"), ("rust", "HasProperty", "memory-safe"),
        ("sql", "HasProperty", "declarative"), ("thread", "HasProperty", "shared memory"),
        ("lock", "HasProperty", "mutual exclusion"),
        ("recursion", "HasProperty", "stack depth limited"),
        ("hash map", "HasProperty", "O(1) average lookup"),
        ("array", "HasProperty", "contiguous memory"),
        ("linked list", "HasProperty", "dynamic size"),
        # ── Causes ──
        ("deadlock", "Causes", "process hang"), ("race condition", "Causes", "data corruption"),
        ("memory leak", "Causes", "out of memory"), ("infinite loop", "Causes", "program hang"),
        ("null pointer", "Causes", "crash"), ("buffer overflow", "Causes", "security breach"),
        ("sql injection", "Causes", "data breach"),
        ("stack overflow", "Causes", "program crash"),
        ("fragmentation", "Causes", "performance degradation"),
        ("contention", "Causes", "slowdown"),
        ("improper locking", "Causes", "deadlock"),
        # ── AtLocation ──
        ("function", "AtLocation", "module"), ("variable", "AtLocation", "memory"),
        ("file", "AtLocation", "disk"), ("process", "AtLocation", "memory"),
        ("thread", "AtLocation", "process"), ("cache", "AtLocation", "cpu"),
        ("database", "AtLocation", "server"),
        # ── RelatedTo ──
        ("cpu", "RelatedTo", "computation"), ("gpu", "RelatedTo", "graphics"),
        ("ram", "RelatedTo", "memory access"), ("disk", "RelatedTo", "storage"),
        ("network", "RelatedTo", "communication"), ("protocol", "RelatedTo", "network"),
        ("api", "RelatedTo", "web service"), ("database", "RelatedTo", "persistence"),
        ("cache", "RelatedTo", "performance"), ("thread", "RelatedTo", "concurrency"),
        ("lock", "RelatedTo", "synchronization"), ("mutex", "RelatedTo", "mutual exclusion"),
        ("deadlock", "RelatedTo", "concurrency bug"),
        ("race", "RelatedTo", "concurrency bug"),
        ("testing", "RelatedTo", "quality assurance"),
        ("logging", "RelatedTo", "observability"), ("monitoring", "RelatedTo", "observability"),
        ("function", "RelatedTo", "abstraction"), ("class", "RelatedTo", "encapsulation"),
        ("inheritance", "RelatedTo", "code reuse"), ("polymorphism", "RelatedTo", "flexibility"),
    ]

    RELATION_TYPES = {"IsA", "PartOf", "CapableOf", "HasProperty", "Causes", "AtLocation", "RelatedTo"}

    def __init__(self):
        self._index: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        self._cache: Dict[str, List[Dict]] = {}
        self.cache_size = 5000
        self._build_index()
        logger.info(f"[KG] Loaded {len(self.TRIPLES)} commonsense facts")

    def _build_index(self):
        for s, r, o in self.TRIPLES:
            self._index[s].append((s, r, o))
            self._index[o].append((s, r, o))

    def query(self, concept: str, relation_type: Optional[str] = None) -> List[Dict]:
        cache_key = f"{concept}:{relation_type}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        results = []
        concept_lower = concept.lower()
        for entity, triples in self._index.items():
            if concept_lower in entity or entity in concept_lower:
                for s, r, o in triples:
                    if relation_type and r != relation_type:
                        continue
                    results.append({"subject": s, "relation": r, "object": o})
        # Also search full text
        for s, r, o in self.TRIPLES:
            if relation_type and r != relation_type:
                continue
            if concept_lower in s or concept_lower in o:
                results.append({"subject": s, "relation": r, "object": o})
        # Deduplicate
        seen = set()
        unique = []
        for r in results:
            key = (r["subject"], r["relation"], r["object"])
            if key not in seen:
                seen.add(key)
                unique.append(r)
        if len(self._cache) < self.cache_size:
            self._cache[cache_key] = unique
        return unique

    def infer_implicit(self, statement: str) -> List[str]:
        """Infer implicit knowledge from a statement."""
        inferences = []
        words = statement.lower().split()
        for word in words:
            for s, r, o in self.TRIPLES:
                if word in s or word in o:
                    if "Causes" in r:
                        inferences.append(f"{word} may cause: {o}")
                    if "HasProperty" in r:
                        inferences.append(f"{word} has property: {o}")
                    if "CapableOf" in r:
                        inferences.append(f"{word} can: {o}")
        return list(set(inferences))[:10]

    def ground_entity(self, entity_name: str) -> Dict:
        """Ground an entity to commonsense knowledge."""
        isa = self.query(entity_name, "IsA")
        props = self.query(entity_name, "HasProperty")
        capable = self.query(entity_name, "CapableOf")
        causes = self.query(entity_name, "Causes")
        related = self.query(entity_name, "RelatedTo")
        return {
            "name": entity_name,
            "types": [r["object"] for r in isa[:5]],
            "properties": [r["object"] for r in props[:5]],
            "capabilities": [r["object"] for r in capable[:5]],
            "causes": [r["object"] for r in causes[:5]],
            "related": [r["object"] for r in related[:10]],
        }


# ═══════════════════════════════════════════════════════════════
# Enhanced MCTS with State Prediction
# ═══════════════════════════════════════════════════════════════

class StatePredictor:
    """Predicts future states and outcomes based on past experience."""

    def __init__(self):
        self.outcome_history: Dict[str, List[float]] = defaultdict(list)

    def record_outcome(self, action: str, outcome: float):
        self.outcome_history[action].append(outcome)
        if len(self.outcome_history[action]) > 100:
            self.outcome_history[action] = self.outcome_history[action][-100:]

    def predict(self, action: str, current_state: str = "") -> float:
        outcomes = self.outcome_history.get(action, [])
        if not outcomes:
            return 0.5  # neutral
        return sum(outcomes) / len(outcomes)

    def expected_improvement(self, action: str, baseline: float = 0.5) -> float:
        pred = self.predict(action)
        return max(0.0, pred - baseline)

    def confidence(self, action: str) -> float:
        n = len(self.outcome_history.get(action, []))
        return min(1.0, n / 10.0)


class EnhancedMCTSPlanner:
    """MCTS with state prediction from historical outcomes."""

    def __init__(self, n_simulations: int = 100, exploration: float = 1.4):
        self.base = MCTSPlanner(n_simulations=n_simulations, exploration=exploration)
        self.predictor = StatePredictor()
        self._total_plans = 0

    def plan(self, goal: str, actions: List[str], context: str = "") -> Plan:
        plan = self.base.plan(goal, actions)
        # Enhance with predicted outcomes
        for step in plan.steps:
            pred = self.predictor.predict(step.action, goal)
            step.expected_outcome = f"Predicted success: {pred:.0%}"
            step.duration_estimate = max(0.5, 2.0 - pred)
        self._total_plans += 1
        return plan

    def run_mcts(self, root_state: str, actions: List[str],
                 depth: int = 5, iterations: int = 100) -> List[str]:
        root = MCTSNode(state=root_state)
        for _ in range(iterations):
            node = self.base.select(root)
            if node.visits == 0 or node.depth >= depth:
                reward = self._simulate_weighted(node, depth - node.depth)
                self.base.backpropagate(node, reward)
            else:
                self.base.expand(node, actions)
                child = random.choice(node.children) if node.children else node
                reward = self._simulate_weighted(child, depth - child.depth)
                self.base.backpropagate(child, reward)

        # Extract best path
        path = []
        node = root
        while node.children:
            node = node.best_child() or node
            if node.action:
                path.append(node.action)
        return path

    def _simulate_weighted(self, node: MCTSNode, depth: int) -> float:
        reward = 0.5
        for d in range(depth):
            if node.action:
                pred = self.predictor.predict(node.action)
                reward = reward * 0.7 + pred * 0.3
            else:
                reward += 0.1 * (random.random() - 0.3)
        return max(reward, 0.0)
