"""
LAAP AGI — 统一量子因果引擎: 发现器 (R11 拆分)
============================================================
原 causal.py (1642 行) 拆分出的子模块之一。
完整拆分: causal_models.py(数据模型) / causal_discovery.py(发现器) /
          causal_engine.py(引擎) / causal.py(薄门面, 既有导入零破坏)。
"""

from __future__ import annotations

import os
import logging
import json, math, time, random, uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Callable
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum

import numpy as np

logger = logging.getLogger("laap.agi.causal")


# ═══════════════════════════════════════════════════════════════
# 模式二：量子因果存储 (from aris_brain/ao_metacog.py)
# ═══════════════════════════════════════════════════════════════
class QuantumCausalStore:
    """
    量子因果存储 — 因果链作为量子叠加态。

    |Ψ_causal⟩ = Σ α_i |cause_i⟩ ⊗ |effect_i⟩

    每个因果链是一个纠缠态：
      原因和效应在量子层面纠缠在一起。
    """

    def __init__(self, dim: int = 64, max_links: int = 2000):
        self.dim = dim
        self.max_links = max_links
        # 因果图谱 [(cause_vector, effect_vector, confidence, domain, timestamp)]
        self.causal_links: List[Tuple[np.ndarray, np.ndarray, float, str, float]] = []
        self._total_inferences = 0

    def learn(self, cause: np.ndarray, effect: np.ndarray,
              confidence: float = 0.5, domain: str = "general") -> bool:
        """学习一个因果关系。返回 True 表示新增，False 表示加强已有。"""
        c = cause.flatten()[:self.dim]
        e = effect.flatten()[:self.dim]
        cn = np.linalg.norm(c)
        en = np.linalg.norm(e)
        if cn > 1e-8: c = c / cn
        if en > 1e-8: e = e / en

        # 如果相似的因果链已存在，加强置信度
        for i, (ec, ee, conf, dom, ts) in enumerate(self.causal_links):
            if np.dot(c, ec) > 0.8 and np.dot(e, ee) > 0.8:
                self.causal_links[i] = (ec, ee, min(1.0, conf + 0.1), dom, time.time())
                return False

        # 否则新增
        self.causal_links.append((c, e, confidence, domain, time.time()))

        # 限制数量，保留最可靠的
        if len(self.causal_links) > self.max_links:
            self.causal_links.sort(key=lambda x: -x[2])
            self.causal_links = self.causal_links[:self.max_links]
        return True

    def predict_effect(self, cause: np.ndarray,
                       top_k: int = 5, domain_filter: Optional[str] = None
                       ) -> List[Tuple[np.ndarray, float, str]]:
        """
        给定原因，预测效应。
        在因果叠加态中检索 → 振幅放大 → 最可能的效应坍缩。
        """
        self._total_inferences += 1
        if not self.causal_links:
            return []

        query = cause.flatten()[:self.dim]
        qn = np.linalg.norm(query)
        if qn > 1e-8: query = query / qn

        scored = []
        for c_vec, e_vec, conf, dom, ts in self.causal_links:
            if domain_filter and dom != domain_filter:
                continue
            similarity = float(np.dot(query, c_vec))
            if similarity > 0.3:
                score = similarity * conf
                # 非线性振幅放大：强匹配更强
                if score > 0.6:
                    score = score ** 0.7
                scored.append((e_vec, score, dom))

        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def infer_cause(self, effect: np.ndarray,
                    top_k: int = 5, domain_filter: Optional[str] = None
                    ) -> List[Tuple[np.ndarray, float, str]]:
        """逆向推理：从效应推出可能的原因"""
        query = effect.flatten()[:self.dim]
        qn = np.linalg.norm(query)
        if qn > 1e-8: query = query / qn

        scored = []
        for c_vec, e_vec, conf, dom, ts in self.causal_links:
            if domain_filter and dom != domain_filter:
                continue
            similarity = float(np.dot(query, e_vec))
            if similarity > 0.3:
                score = similarity * conf
                if score > 0.6:
                    score = score ** 0.7
                scored.append((c_vec, score, dom))

        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def stats(self) -> dict:
        return {
            "total_links": len(self.causal_links),
            "total_inferences": self._total_inferences,
            "dim": self.dim,
            "domains": list(set(d for _, _, _, d, _ in self.causal_links)),
        }


# ═══════════════════════════════════════════════════════════════
# 模式三：因果发现 / PC算法 (from laap/agi/v5_upgrade.py)
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# 模式三：PC 算法因果发现 (from laap/agi/v5_upgrade.py)
# ═══════════════════════════════════════════════════════════════
class ConditionalIndependenceTester:
    """条件独立性检验 — PC 算法的基础"""

    @staticmethod
    def partial_correlation(x: List[float], y: List[float],
                            z: Optional[List[float]] = None) -> float:
        """计算偏相关系数"""
        n = len(x)
        if n < 3:
            return 0.0
        xa, ya = np.array(x, dtype=float), np.array(y, dtype=float)
        if z is None:
            r = np.corrcoef(xa, ya)[0, 1]
            return 0.0 if np.isnan(r) else abs(r)
        za = np.array(z, dtype=float)
        # 回归残差
        def resid(a, b):
            A = np.vstack([b, np.ones(len(b))]).T
            coeffs, _, _, _ = np.linalg.lstsq(A, a, rcond=None)
            return a - A @ coeffs
        rx = resid(xa, za)
        ry = resid(ya, za)
        r = np.corrcoef(rx, ry)[0, 1]
        return 0.0 if np.isnan(r) else abs(r)

    @staticmethod
    def test(x: List[float], y: List[float],
             z: Optional[List[float]] = None, alpha: float = 0.05) -> Tuple[float, bool]:
        """检验 x 和 y 是否在给定 z 下条件独立"""
        r = ConditionalIndependenceTester.partial_correlation(x, y, z)
        return r, r < alpha


class CausalDiscovery:
    """
    因果发现引擎 — 从观测数据中发现因果结构 (PC算法)。

    不依赖先验知识，纯从数据中学习变量之间的因果关系。
    """

    def __init__(self, alpha: float = 0.05):
        self.alpha = alpha
        self.tester = ConditionalIndependenceTester()
        self.graph: Dict[str, Set[str]] = {}
        self.directed_edges: List[Tuple[str, str, float]] = []

    def discover(self, data: Dict[str, List[float]]) -> Dict[str, Any]:
        """
        从观测数据中挖掘因果结构。

        Args:
            data: {变量名: [观测值列表]}

        Returns:
            {graph, edges, variables, method}
        """
        variables = list(data.keys())
        n = len(variables)
        if n < 2:
            return {"graph": {}, "edges": [], "variables": variables, "method": "PC-algorithm"}

        # Step 1: 完全无向图
        self.graph = {v: set(variables) - {v} for v in variables}

        # Step 2: PC 骨架发现（逐步剪枝）
        for depth in range(min(3, n)):
            for var in variables:
                neighbors = list(self.graph.get(var, set()))
                for nb in neighbors:
                    if nb not in self.graph.get(var, set()):
                        continue
                    cond_set = list(set(neighbors) - {nb})
                    cond_set = cond_set[:depth] if cond_set else []
                    if len(data[var]) > 2 and len(data[nb]) > 2:
                        if cond_set and cond_set[0] in data:
                            cond_data = data[cond_set[0]]
                            r, indep = self.tester.test(data[var], data[nb], cond_data, self.alpha)
                        else:
                            r, indep = self.tester.test(data[var], data[nb], alpha=self.alpha)
                        if indep:
                            self.graph[var].discard(nb)
                            self.graph[nb].discard(var)

        # Step 3: 边定向
        self.directed_edges = []
        for var in variables:
            for nb in self.graph.get(var, set()):
                if var < nb:
                    strength = self.tester.partial_correlation(data[var], data[nb])
                    self.directed_edges.append((var, nb, 0.0 if np.isnan(strength) else abs(strength)))

        self.directed_edges.sort(key=lambda x: -x[2])
        return {
            "graph": {k: list(v) for k, v in self.graph.items()},
            "edges": [(a, b, round(s, 3)) for a, b, s in self.directed_edges],
            "variables": variables,
            "method": "PC-algorithm",
        }

    def find_causal_relations(self, variables: List[str],
                              observations: Dict[str, List[float]]) -> List[Dict[str, Any]]:
        """便捷方法：直接从数据提取因果关系列表"""
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
# 模式四：因果键 (CausalBond, from ether_wm.py)
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# 模式四：置信度加权因果键 (from ether_wm.py)
# ═══════════════════════════════════════════════════════════════
@dataclass
class CausalBond:
    """
    因果键 — 基于观测的置信度加权因果关系。

    每次观测到因果链成立/不成立，都会更新权重和置信度。
    观测次数越多，置信度越接近 0.99。
    """
    action: str = ""
    target_type: str = ""
    effect_desc: str = ""
    weight: float = 0.5       # 因果强度
    confidence: float = 0.5   # 对这条因果的置信度
    observation_count: int = 0
    positive_count: int = 0
    domain: str = "physics"
    created_at: float = field(default_factory=time.time)
    last_observed: float = field(default_factory=time.time)

    def observe(self, matched: bool):
        """观测到一次因果事件"""
        self.observation_count += 1
        if matched:
            self.positive_count += 1
        # 贝叶斯更新：新证据逐步调整权重
        rate = 1.0 / max(1, self.observation_count)
        target = 0.9 if matched else 0.1
        self.weight += (target - self.weight) * rate
        self.last_observed = time.time()
        # 置信度随观测次数增长
        self.confidence = min(0.99, self.observation_count / max(1, self.observation_count + 3))

    def to_dict(self) -> dict:
        return {
            "action": self.action, "target": self.target_type,
            "effect": self.effect_desc,
            "weight": round(self.weight, 3),
            "confidence": round(self.confidence, 3),
            "observations": self.observation_count,
            "positive": self.positive_count,
            "domain": self.domain,
        }


# ═══════════════════════════════════════════════════════════════
# 模式五：时间因果链 (P1-1a NEW)
# ═══════════════════════════════════════════════════════════════


