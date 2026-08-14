"""
LAAP AGI — 统一量子因果引擎: 数据模型 (R11 拆分)
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
# 模式一：物理因果规则 (from world_model.py)
# ═══════════════════════════════════════════════════════════════
@dataclass
class CausalCondition:
    """因果条件 — 触发规则的前提"""
    source: str = "target"       # "target" | "instrument" | "actor"
    property: str = ""           # 属性路径
    operator: str = "eq"         # eq | gte | lte | neq | contains
    value: Any = None

    def check(self, entity_state: dict) -> bool:
        """检查一个实体状态是否满足此条件"""
        val = entity_state
        for part in self.property.split("."):
            if isinstance(val, dict):
                val = val.get(part, None)
            elif hasattr(val, part):
                val = getattr(val, part)
            else:
                return False
        if val is None:
            return False
        if self.operator == "eq":
            return val == self.value
        elif self.operator == "neq":
            return val != self.value
        elif self.operator == "gte":
            return val >= self.value
        elif self.operator == "lte":
            return val <= self.value
        elif self.operator == "contains":
            return self.value in val if isinstance(val, (list, str)) else False
        return False


@dataclass
class CausalEffect:
    """因果效应 — 规则触发后的变化"""
    target: str = "target"       # "target" | "instrument" | "actor"
    property: str = ""           # 属性路径
    operation: str = "set"       # set | add | mult | append | remove
    value: Any = None

    def apply(self, entity_state: dict) -> dict:
        """在实体状态的副本上应用此效应"""
        result = dict(entity_state)
        parts = self.property.split(".")
        obj = result
        for part in parts[:-1]:
            if part not in obj:
                obj[part] = {}
            obj = obj[part]
        last_key = parts[-1]
        if self.operation == "set":
            obj[last_key] = self.value
        elif self.operation == "add":
            obj[last_key] = (obj.get(last_key, 0) or 0) + self.value
        elif self.operation == "mult":
            obj[last_key] = (obj.get(last_key, 1) or 1) * self.value
        elif self.operation == "append":
            if last_key not in obj:
                obj[last_key] = []
            obj[last_key].append(self.value)
        elif self.operation == "remove":
            if last_key in obj and isinstance(obj[last_key], list) and self.value in obj[last_key]:
                obj[last_key].remove(self.value)
        return result


@dataclass
class CausalRule:
    """因果规则 — 如果条件满足 → 则效应发生"""
    name: str = ""
    action: str = ""                     # 关联的动作类型
    conditions: List[CausalCondition] = field(default_factory=list)
    effects: List[CausalEffect] = field(default_factory=list)
    probability: float = 1.0             # 发生概率 0~1
    delay_seconds: float = 0.0           # 延迟时间
    domain: str = "physics"              # 领域标签
    enabled: bool = True
    confidence: float = 0.5              # 对这条规则的置信度
    observation_count: int = 0           # 被观测到的次数
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "action": self.action,
            "conditions": [(c.source, c.property, c.operator, str(c.value)) for c in self.conditions],
            "effects": [(e.target, e.property, e.operation, str(e.value)) for e in self.effects],
            "probability": self.probability, "delay": self.delay_seconds,
            "domain": self.domain, "confidence": round(self.confidence, 3),
            "observations": self.observation_count,
        }


# ═══════════════════════════════════════════════════════════════
# 模式二：量子因果编码 (from aris_brain/ao_metacog.py)
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# P1-1a: 时间因果 (TemporalCausalLink / TemporalCausalChain)
# ═══════════════════════════════════════════════════════════════
@dataclass
class TemporalCausalLink:
    """
    时间因果链中的一环。

    A --[Δt]--> B
    每个环记录了从原因到效应的预期时间间隔。
    """
    cause_name: str = ""
    effect_name: str = ""
    delay_mean: float = 0.0       # 平均延迟（秒）
    delay_std: float = 0.0        # 延迟标准差
    confidence: float = 0.5
    observation_count: int = 0
    domain: str = "general"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "cause": self.cause_name, "effect": self.effect_name,
            "delay_mean": round(self.delay_mean, 3),
            "delay_std": round(self.delay_std, 3),
            "confidence": round(self.confidence, 3),
            "observations": self.observation_count,
            "domain": self.domain,
        }


@dataclass
class TemporalCausalChain:
    """
    完整的时间因果链。

    A --[Δt1]--> B --[Δt2]--> C --[Δt3]--> D
    支持链式传递推理：如果 A→B 且 B→C，则 A→C。
    """

    name: str = ""
    links: List[TemporalCausalLink] = field(default_factory=list)
    total_delay: float = 0.0
    confidence: float = 0.5
    domain: str = "general"
    created_at: float = field(default_factory=time.time)

    def add_link(self, link: TemporalCausalLink):
        """在链尾添加一环"""
        self.links.append(link)
        self.total_delay = sum(l.delay_mean for l in self.links)
        # 链的置信度是各环的几何平均
        if self.links:
            prod = 1.0
            for l in self.links:
                prod *= max(0.01, l.confidence)
            self.confidence = prod ** (1.0 / len(self.links))

    def get_effective_delay(self, from_step: int = 0, to_step: int = -1) -> float:
        """计算链中两点的累计延迟"""
        if to_step == -1:
            to_step = len(self.links)
        return sum(l.delay_mean for l in self.links[from_step:to_step])

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "links": [l.to_dict() for l in self.links],
            "total_delay": round(self.total_delay, 3),
            "confidence": round(self.confidence, 3),
            "domain": self.domain,
        }


# ═══════════════════════════════════════════════════════════════
# 模式六：多因素因果 (P1-1b NEW)
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# P1-1b: 多因素规则 (FactorOperator / CausalFactor / MultiFactorRule)
# ═══════════════════════════════════════════════════════════════
class FactorOperator(Enum):
    """多因素组合算子"""
    AND = "and"      # 所有因素必须同时满足
    OR = "or"        # 任一因素满足即可
    XOR = "xor"      # 恰好一个因素满足
    WEIGHTED = "weighted"  # 加权组合：各因素按权重贡献


@dataclass
class CausalFactor:
    """因果因素 — 多因素因果中的一个输入"""
    name: str = ""
    weight: float = 1.0           # 对此因素的权重
    threshold: float = 0.3        # 激活阈值
    is_present: bool = False      # 当前是否激活
    confidence: float = 0.5
    domain: str = "general"

    def to_dict(self) -> dict:
        return {
            "name": self.name, "weight": round(self.weight, 3),
            "threshold": self.threshold, "confidence": round(self.confidence, 3),
            "domain": self.domain,
        }


@dataclass
class MultiFactorRule:
    """
    多因素因果规则 — 多个原因共同导致一个结果。

    例如："高温度" AND "有氧气" → "燃烧"
    或："受伤" OR "生病" → "需要治疗"
    """
    name: str = ""
    effect: str = ""                         # 结果描述
    factors: List[CausalFactor] = field(default_factory=list)
    operator: FactorOperator = FactorOperator.AND
    base_probability: float = 0.5            # 无因素时的基准概率
    confidence: float = 0.5
    observation_count: int = 0
    domain: str = "general"
    enabled: bool = True
    created_at: float = field(default_factory=time.time)

    def compute_activation(self) -> Tuple[float, List[str]]:
        """
        计算当前因素组合下的激活程度。

        Returns:
            (activation_level, activated_factor_names)
        """
        activated = [f.name for f in self.factors if f.is_present]
        active_weights = [f.weight for f in self.factors if f.is_present]

        if self.operator == FactorOperator.AND:
            all_active = len(activated) == len(self.factors) and len(self.factors) > 0
            activation = 1.0 if all_active else 0.0

        elif self.operator == FactorOperator.OR:
            activation = 1.0 if len(activated) > 0 else 0.0

        elif self.operator == FactorOperator.XOR:
            activation = 1.0 if len(activated) == 1 else 0.0

        elif self.operator == FactorOperator.WEIGHTED:
            total_weight = sum(f.weight for f in self.factors) or 1.0
            activation = sum(active_weights) / total_weight
            activation = min(1.0, activation)

        else:
            activation = 0.0

        # 置信度调制
        effective = activation * self.confidence
        return effective, activated

    def to_dict(self) -> dict:
        return {
            "name": self.name, "effect": self.effect,
            "factors": [f.to_dict() for f in self.factors],
            "operator": self.operator.value,
            "base_probability": self.base_probability,
            "confidence": round(self.confidence, 3),
            "observations": self.observation_count,
            "domain": self.domain,
        }


# ═══════════════════════════════════════════════════════════════
# 模式七：因果干预模拟器 (P1-1d NEW, do-calculus)
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# P1-1d/e: 干预结果 / 反事实情感
# ═══════════════════════════════════════════════════════════════
@dataclass
class InterventionResult:
    """
    干预模拟的结果。

    do(X=x) 前后对比：
      - 干预前 P(Y) — 自然状态下的结果分布
      - 干预后 P(Y | do(X=x)) — 强制设定 X=x 后的结果
      - 因果效应估计: E[Y | do(X=x)] - E[Y]
    """
    intervention_var: str = ""
    intervention_value: Any = None
    pre_intervention: Dict[str, float] = field(default_factory=dict)
    post_intervention: Dict[str, float] = field(default_factory=dict)
    causal_effect: float = 0.0
    confidence: float = 0.5
    assumptions: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "intervention": f"do({self.intervention_var}={self.intervention_value})",
            "pre": self.pre_intervention,
            "post": self.post_intervention,
            "causal_effect": round(self.causal_effect, 4),
            "confidence": round(self.confidence, 3),
            "assumptions": self.assumptions,
        }


# ═══════════════════════════════════════════════════════════════
# 模式八：反事实情感标签 (P1-1e NEW)
# ═══════════════════════════════════════════════════════════════

@dataclass
class CounterfactualEmotion:
    """
    反事实情感标签 — "如果我没做X，我会感觉..."

    把反事实推理和情感系统连接起来：
      - actual_emotion: 实际发生后的感受
      - counterfactual_emotion: 如果没发生会怎样
      - emotional_regret: 遗憾度 (0~1)
      - emotional_relief: 庆幸度 (0~1)
    """
    scenario: str = ""
    action: str = ""
    actual_outcome: str = ""
    counterfactual_outcome: str = ""
    actual_emotion: str = "neutral"
    counterfactual_emotion: str = "neutral"
    emotional_regret: float = 0.0    # 0=不后悔, 1=非常后悔
    emotional_relief: float = 0.0    # 0=不庆幸, 1=非常庆幸
    intensity: float = 0.5           # 情感强度
    timestamp: float = field(default_factory=time.time)

    def compute(self):
        """基于实际和反事实结果自动计算遗憾/庆幸"""
        # 如果实际结果差但反事实好 → 后悔
        if self.actual_emotion in ("sad", "angry", "frustrated", "disappointed") and \
           self.counterfactual_emotion in ("happy", "relieved", "content"):
            self.emotional_regret = min(1.0, self.intensity * 0.8)
            self.emotional_relief = 0.0
        # 如果实际结果好但反事实差 → 庆幸
        elif self.actual_emotion in ("happy", "content", "proud", "relieved") and \
             self.counterfactual_emotion in ("sad", "angry", "afraid"):
            self.emotional_relief = min(1.0, self.intensity * 0.8)
            self.emotional_regret = 0.0
        # 如果结果一致 → 没有反事实情感
        else:
            self.emotional_regret = 0.0
            self.emotional_relief = 0.0

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "action": self.action,
            "actual": self.actual_outcome,
            "counterfactual": self.counterfactual_outcome,
            "actual_emotion": self.actual_emotion,
            "cf_emotion": self.counterfactual_emotion,
            "regret": round(self.emotional_regret, 3),
            "relief": round(self.emotional_relief, 3),
            "intensity": round(self.intensity, 3),
        }


# ═══════════════════════════════════════════════════════════════
# 统一因果引擎 — 融合四种模式
# ═══════════════════════════════════════════════════════════════


