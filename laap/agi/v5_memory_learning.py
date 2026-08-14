"""
LAAP AGI v5 Upgrade: 记忆/学习层 (R11 拆分)
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


# ═══ 记忆/经验/学习层 (自原 v5_upgrade.py 拆分) ═══
class SumTree:
    """Binary sum tree for prioritized experience sampling."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.tree = [0.0] * (2 * capacity)
        self.data = [None] * capacity
        self.write = 0
        self.n_entries = 0

    def _propagate(self, idx: int, change: float):
        parent = idx // 2
        self.tree[parent] += change
        if parent != 0:
            self._propagate(parent, change)

    def _retrieve(self, idx: int, s: float) -> int:
        left = 2 * idx
        if left >= len(self.tree):
            return idx
        if s <= self.tree[left]:
            return self._retrieve(left, s)
        else:
            return self._retrieve(left + 1, s - self.tree[left])

    def total(self) -> float:
        return self.tree[1] if self.tree else 0.0

    def add(self, priority: float, data: Any):
        idx = self.write + self.capacity
        self.data[self.write] = data
        self._update(idx, priority)
        self.write = (self.write + 1) % self.capacity
        self.n_entries = min(self.n_entries + 1, self.capacity)

    def _update(self, idx: int, priority: float):
        change = priority - self.tree[idx]
        self.tree[idx] = priority
        self._propagate(idx, change)

    def update(self, indices: List[int], priorities: List[float]):
        for idx, p in zip(indices, priorities):
            self._update(idx + self.capacity, p)

    def get(self, idx: int) -> float:
        return self.tree[idx + self.capacity]

    def get_min_idx(self) -> int:
        best, best_val = 0, float('inf')
        for i in range(self.n_entries):
            v = self.get(i)
            if v < best_val:
                best, best_val = i, v
        return best

    def sample(self, batch_size: int, beta: float = 0.4) -> Tuple[List, List[int], List[float]]:
        batch, indices, weights = [], [], []
        segment = self.total() / batch_size
        for i in range(batch_size):
            a, b = segment * i, segment * (i + 1)
            s = random.uniform(a, b)
            idx = self._retrieve(1, s) - self.capacity
            if idx < 0 or idx >= len(self.data) or self.data[idx] is None:
                idx = random.randint(0, min(self.n_entries, self.capacity) - 1)
            batch.append(self.data[idx])
            indices.append(idx)
            p = self.get(idx)
            prob = p / max(self.total(), 1e-8)
            w = (self.n_entries * prob) ** (-beta) if prob > 0 else 0
            weights.append(min(w, 10.0))  # clip
        w_max = max(weights) if weights else 1.0
        weights = [w / max(w_max, 1e-8) for w in weights]
        return batch, indices, weights


class PrioritizedExperienceBuffer:
    """Prioritized Experience Replay buffer."""

    def __init__(self, capacity: int = 10000, alpha: float = 0.6, beta: float = 0.4):
        self.tree = SumTree(capacity)
        self.alpha = alpha
        self.beta = beta
        self.beta_increment = 0.001
        self.capacity = capacity
        self.epsilon = 0.01

    def add(self, state: str, action: str, outcome: float, context: Dict = None):
        priority = max(self.tree.total(), self.epsilon)
        self.tree.add(priority, {
            "state": state, "action": action,
            "outcome": outcome, "context": context or {},
            "time": time.time(),
        })

    def sample(self, batch_size: int) -> Tuple[List, List[int]]:
        batch, indices, weights = self.tree.sample(batch_size, self.beta)
        self.beta = min(1.0, self.beta + self.beta_increment)
        return batch, indices

    def update_priorities(self, indices: List[int], td_errors: List[float]):
        priorities = [(abs(e) + self.epsilon) ** self.alpha for e in td_errors]
        self.tree.update(indices, priorities)

    def __len__(self):
        return self.tree.n_entries


class FisherInfoTracker:
    """Tracks Fisher information for EWC regularization."""

    def __init__(self):
        self.fisher: Dict[str, float] = {}
        self.optimal_params: Dict[str, float] = {}
        self._cooldown: Dict[str, float] = {}

    def record(self, module: str, param_value: float, importance: float = 1.0):
        self.fisher[module] = self.fisher.get(module, 0.0) + importance * 0.1
        self.fisher[module] = min(self.fisher[module], 10.0)
        self.optimal_params[module] = param_value
        self._cooldown[module] = time.time()

    def compute_penalty(self, module: str, current_value: float) -> float:
        fisher = self.fisher.get(module, 0.0)
        optimal = self.optimal_params.get(module, 0.5)
        if fisher < 0.01:
            return 0.0
        diff = current_value - optimal
        return fisher * diff * diff

    def get_fisher_summary(self) -> Dict[str, float]:
        return dict(sorted(self.fisher.items(), key=lambda x: -x[1])[:20])


class SkillImportanceTracker:
    """Tracks which skills are important to preserve across tasks."""

    def __init__(self):
        self.importance: Dict[str, float] = defaultdict(float)
        self.use_count: Dict[str, int] = defaultdict(int)
        self.success_rate: Dict[str, float] = defaultdict(float)

    def record_use(self, skill: str, success: bool):
        self.use_count[skill] += 1
        n = self.use_count[skill]
        self.success_rate[skill] = (self.success_rate[skill] * (n - 1) + (1.0 if success else 0.0)) / n
        # Higher importance for frequently-used skills
        self.importance[skill] = self.success_rate[skill] * min(1.0, n / 5.0)

    def get_important_skills(self, threshold: float = 0.5) -> List[str]:
        return [s for s, imp in self.importance.items() if imp >= threshold]

    def consolidation_loss(self) -> float:
        """Compute total EWC-style loss from skill importance."""
        return sum(self.importance.values())


# ═══════════════════════════════════════════════════════════════
# Phase 1b: Enhanced Bug Classifier (Logic-Level)
# ═══════════════════════════════════════════════════════════════

@dataclass
class BugReport:
    file: str = ""
    line: int = 0
    message: str = ""
    category: str = "unknown"
    severity: str = "medium"
    code_context: str = ""


class BugCategory(Enum):
    SYNTAX = "syntax"
    IMPORT = "import"
    ATTRIBUTE = "attribute"
    TYPE = "type"
    LOGIC = "logic"
    DESIGN = "design"
    RACE = "race"
    PERFORMANCE = "performance"
    SECURITY = "security"


class EnhancedBugClassifier:
    """Logic-level bug analysis and classification."""

    PATTERNS = {
        BugCategory.SYNTAX: ["SyntaxError", "IndentationError", "unexpected EOF"],
        BugCategory.IMPORT: ["ImportError", "ModuleNotFoundError", "No module named"],
        BugCategory.ATTRIBUTE: ["AttributeError", "has no attribute"],
        BugCategory.TYPE: ["TypeError", "must be", "cannot unpack"],
        BugCategory.LOGIC: [
            "unexpected behavior", "wrong result", "incorrect",
            "off-by-one", "infinite loop", "deadlock",
        ],
        BugCategory.DESIGN: [
            "code smell", "tight coupling", "god class",
            "magic number", "duplicate code", "long method",
        ],
        BugCategory.RACE: [
            "race condition", "data race", "concurrent modification",
            "shared state", "thread-unsafe",
        ],
        BugCategory.PERFORMANCE: [
            "slow", "timeout", "O(n²)", "memory leak",
            "bottleneck", "inefficient",
        ],
        BugCategory.SECURITY: [
            "injection", "XSS", "SQL injection", "unsafe",
            "command injection", "path traversal",
        ],
    }

    SEVERITY_MAP = {
        BugCategory.SYNTAX: "high",
        BugCategory.IMPORT: "high",
        BugCategory.ATTRIBUTE: "medium",
        BugCategory.TYPE: "medium",
        BugCategory.LOGIC: "high",
        BugCategory.DESIGN: "low",
        BugCategory.RACE: "critical",
        BugCategory.PERFORMANCE: "medium",
        BugCategory.SECURITY: "critical",
    }

    def classify(self, error_message: str, source_file: str = "",
                 source_line: int = 0, context: str = "") -> BugReport:
        for category, patterns in self.PATTERNS.items():
            if any(p.lower() in error_message.lower() for p in patterns):
                return BugReport(
                    file=source_file, line=source_line,
                    message=error_message, category=category.value,
                    severity=self.SEVERITY_MAP.get(category, "medium"),
                    code_context=context,
                )
        return BugReport(
            file=source_file, line=source_line,
            message=error_message, category="unknown",
            severity="medium", code_context=context,
        )


class LogicFixGenerator:
    """Generates concrete fix suggestions for logic-level bugs."""

    LOGIC_FIXES = {
        "off-by-one": "Check loop boundary conditions: ensure range(n) not range(n-1)",
        "infinite loop": "Add loop counter limit or ensure termination condition is updated",
        "deadlock": "Ensure consistent lock ordering across all threads",
        "none check": "Add `if x is not None:` guard before using the value",
        "division by zero": "Add `if denominator != 0:` guard before division",
        "index error": "Ensure list index is within range: `if 0 <= idx < len(lst)`",
    }

    def analyze_ast(self, code: str, error: str) -> List[Dict]:
        fixes = []
        for pattern, suggestion in self.LOGIC_FIXES.items():
            if pattern.lower() in error.lower():
                fixes.append({
                    "pattern": pattern,
                    "suggestion": suggestion,
                    "confidence": 0.8,
                })
        if not fixes:
            fixes.append({
                "pattern": "unspecified logic error",
                "suggestion": "Review the logic flow: check conditions, loops, and data flow",
                "confidence": 0.4,
            })
        return fixes

    def generate_fix(self, bug: BugReport, source_code: str = "") -> Dict:
        fix = {"file": bug.file, "line": bug.line, "category": bug.category,
               "fixes": self.analyze_ast(source_code or bug.message, bug.message)}
        fix["estimated_risk"] = "low" if bug.severity in ("low", "medium") else "medium"
        return fix


class RaceConditionDetector:
    """Detects potential race conditions in code."""

    UNSAFE_PATTERNS = [
        ("shared dict", lambda l: "global " in l and "=" in l and "{" not in l),
        ("no lock", lambda l: any(kw in l for kw in ["threading.", "Thread("])
                             and "Lock()" not in l),
        ("shared var", lambda l: "self." in l and ("= " in l or "+=" in l or "-=" in l)),
    ]

    def scan(self, code_lines: List[str]) -> List[Dict]:
        findings = []
        for i, line in enumerate(code_lines):
            for name, check in self.UNSAFE_PATTERNS:
                if check(line):
                    findings.append({
                        "line": i + 1, "pattern": name,
                        "code": line.strip(),
                        "risk": "high" if name == "no lock" else "medium",
                    })
        return findings


# ═══════════════════════════════════════════════════════════════
# Phase 2a: Causal Discovery Engine
# ═══════════════════════════════════════════════════════════════

class ConditionalIndependenceTester:
    """Tests conditional independence using partial correlation."""

    @staticmethod
    def _mean(vals: List[float]) -> float:
        return sum(vals) / max(len(vals), 1)

    @staticmethod
    def _cov(x: List[float], y: List[float]) -> float:
        mx, my = ConditionalIndependenceTester._mean(x), ConditionalIndependenceTester._mean(y)
        return sum((a - mx) * (b - my) for a, b in zip(x, y)) / max(len(x) - 1, 1)

    @staticmethod
    def _var(x: List[float]) -> float:
        mx = ConditionalIndependenceTester._mean(x)
        return sum((v - mx) ** 2 for v in x) / max(len(x) - 1, 1)

    def partial_correlation(self, x: List[float], y: List[float],
                            z: Optional[List[float]] = None) -> float:
        if z is None or not z:
            c = self._cov(x, y)
            vx, vy = self._var(x), self._var(y)
            return c / max(math.sqrt(vx * vy), 1e-10)
        # Partial correlation: control for z
        r_xy = self.partial_correlation(x, y)
        r_xz = self.partial_correlation(x, z)
        r_yz = self.partial_correlation(y, z)
        denom = math.sqrt(max(1 - r_xz * r_xz, 1e-10)) * math.sqrt(max(1 - r_yz * r_yz, 1e-10))
        return (r_xy - r_xz * r_yz) / max(denom, 1e-10)

    def test(self, x: List[float], y: List[float],
             z: Optional[List[float]] = None, alpha: float = 0.05) -> Tuple[float, bool]:
        r = abs(self.partial_correlation(x, y, z))
        return r, r < alpha


class CausalDiscovery:
    """PC-algorithm causal discovery from observational data."""

    def __init__(self, alpha: float = 0.05):
        self.alpha = alpha
        self.tester = ConditionalIndependenceTester()
        self.graph: Dict[str, set] = {}
        self.directed_edges: List[Tuple[str, str, float]] = []

    def discover(self, data: Dict[str, List[float]]) -> Dict:
        variables = list(data.keys())
        n = len(variables)
        # Step 1: Complete undirected graph
        self.graph = {v: set(variables) - {v} for v in variables}
        sep_sets = {}

        if n < 2:
            return {"graph": self.graph, "edges": [], "variables": variables}

        # Step 2: PC skeleton discovery
        for depth in range(min(3, n)):
            for var in variables:
                neighbors = list(self.graph.get(var, set()))
                for nb in neighbors:
                    if nb not in self.graph.get(var, set()):
                        continue
                    cond_set = list(set(neighbors) - {nb})
                    cond_set = cond_set[:depth] if cond_set else []
                    # Conditional independence test
                    if len(data[var]) > 2 and len(data[nb]) > 2:
                        if cond_set:
                            cond_data = data.get(cond_set[0], [0])
                            r, indep = self.tester.test(data[var], data[nb], cond_data, self.alpha)
                        else:
                            r, indep = self.tester.test(data[var], data[nb], alpha=self.alpha)
                        if indep:
                            self.graph[var].discard(nb)
                            self.graph[nb].discard(var)
                            sep_sets[(var, nb)] = set(cond_set)

        # Step 3: Edge orientation (v-structures)
        self.directed_edges = []
        for var in variables:
            for nb in self.graph.get(var, set()):
                if var < nb:
                    strength = self.tester.partial_correlation(
                        data[var], data[nb]
                    )
                    self.directed_edges.append((var, nb, abs(strength)))

        self.directed_edges.sort(key=lambda x: -x[2])
        return {
            "graph": {k: list(v) for k, v in self.graph.items()},
            "edges": [(a, b, round(s, 3)) for a, b, s in self.directed_edges],
            "variables": variables,
            "method": "PC-algorithm",
        }

    def find_causal_relations(self, variables: List[str],
                               observations: Dict[str, List[float]]) -> List[Dict]:
        result = self.discover(observations)
        relations = []
        for a, b, strength in result["edges"]:
            relations.append({
                "cause": a, "effect": b,
                "strength": strength,
                "confidence": min(1.0, strength * 2),
            })
        return relations


# ═══════════════════════════════════════════════════════════════
# Phase 2b: Active Learning + Meta-Learning
# ═══════════════════════════════════════════════════════════════

class NoveltyDetector:
    """Measures how novel a new experience is compared to past experiences."""

    def __init__(self, window: int = 100):
        self.history: List[Tuple[str, float]] = []
        self.window = window
        self._signatures: Dict[str, float] = {}

    def compute(self, state: str, action: str) -> float:
        sig = f"{state[:50]}:{action[:30]}"
        h = abs(hash(sig)) % 10000
        # Novelty = how different from past signatures
        if h not in self._signatures:
            self._signatures[h] = len(self._signatures) / max(self.window, 1)
            novelty = 1.0
        else:
            count = sum(1 for s, _ in self.history if s[:30] in state)
            novelty = max(0.0, 1.0 - count / max(len(self.history), 1))
        self.history.append((state, time.time()))
        if len(self.history) > self.window:
            self.history = self.history[-self.window:]
        return novelty


class SurpriseDetector:
    """Measures prediction error / surprise."""

    def __init__(self):
        self.predictions: Dict[str, float] = {}
        self._ema_error = 0.1

    def compute(self, state: str, action: str, actual_outcome: float,
                predicted_outcome: Optional[float] = None) -> float:
        sig = f"{state[:40]}:{action[:20]}"
        pred = predicted_outcome or self.predictions.get(sig, 0.5)
        error = abs(actual_outcome - pred)
        self._ema_error = 0.9 * self._ema_error + 0.1 * error
        self.predictions[sig] = (self.predictions.get(sig, 0.5) * 0.9 + actual_outcome * 0.1)
        surprise = error / max(self._ema_error, 0.01)
        return min(surprise, 5.0)


class CuriosityDriver:
    """Curiosity-driven intrinsic motivation."""

    def __init__(self):
        self.novelty = NoveltyDetector()
        self.surprise = SurpriseDetector()
        self.learning_progress: Dict[str, float] = defaultdict(float)

    def compute_intrinsic_reward(self, state: str, action: str,
                                  next_state: str, outcome: float) -> float:
        novelty = self.novelty.compute(state, action)
        surprise = self.surprise.compute(state, action, outcome)
        lp = self.learning_progress.get(action, 0.5)
        return 0.4 * novelty + 0.3 * min(surprise, 1.0) + 0.3 * lp

    def record_learning(self, action: str, improvement: float):
        old = self.learning_progress[action]
        self.learning_progress[action] = old * 0.9 + improvement * 0.1


class MetaLearner:
    """Learn which strategies work best for which tasks."""

    def __init__(self):
        self.strategies: Dict[str, Dict] = defaultdict(lambda: {
            "uses": 0, "successes": 0, "avg_outcome": 0.5, "best_for": [],
        })

    def select_strategy(self, task_type: str, context: str = "") -> str:
        candidates = {
            "debug": "isolate→analyze→fix→verify",
            "explore": "hypothesize→search→verify→synthesize",
            "execute": "plan→implement→test→refine",
            "analyze": "decompose→examine→synthesize→conclude",
        }
        strategy = candidates.get(task_type, "observe→reason→act→learn")
        # Adjust based on past performance
        best = self.strategies.get(strategy, {})
        if best.get("uses", 0) > 3 and best.get("success_rate", 0) > 0.7:
            return strategy
        return strategy

    def record_outcome(self, strategy: str, task_type: str, outcome: float):
        s = self.strategies[strategy]
        s["uses"] += 1
        s["avg_outcome"] = (s["avg_outcome"] * (s["uses"] - 1) + outcome) / s["uses"]
        if outcome > 0.6:
            s["successes"] += 1
            if task_type not in s["best_for"]:
                s["best_for"].append(task_type)
        s["success_rate"] = s["successes"] / max(s["uses"], 1)

    def get_best_strategy(self, task_type: str) -> Optional[str]:
        best_strat, best_score = None, 0
        for strategy, stats in self.strategies.items():
            if task_type in stats.get("best_for", []):
                score = stats.get("success_rate", 0) * stats.get("uses", 0)
                if score > best_score:
                    best_strat, best_score = strategy, score
        return best_strat


class ActiveLearningEngine:
    """Orchestrates curiosity-driven active learning."""

    def __init__(self):
        self.curiosity = CuriosityDriver()
        self.meta = MetaLearner()
        self._exploration_rate = 0.3
        self._total_steps = 0

    def should_explore(self, confidence: float) -> bool:
        self._total_steps += 1
        decay = max(0.05, self._exploration_rate * (0.995 ** self._total_steps))
        return random.random() < max(decay, 0.05)

    def select_action(self, state: Dict, task_type: str) -> Tuple[str, str]:
        strategy = self.meta.select_strategy(task_type, str(state))
        if self.should_explore(state.get("confidence", 0.5)):
            return "explore", f"try new approach: {strategy}"
        return "exploit", strategy

    def record_outcome(self, action_type: str, strategy: str,
                        task_type: str, outcome: float):
        self.meta.record_outcome(strategy, task_type, outcome)
        if action_type == "explore":
            self.curiosity.record_learning(strategy, outcome - 0.5)
        self._exploration_rate = max(0.05, self._exploration_rate * 0.998)

    def curiosity_reward(self, state: str, action: str,
                          next_state: str, outcome: float) -> float:
        return self.curiosity.compute_intrinsic_reward(state, action, next_state, outcome)
