"""
LAAP AGI — 统一世界模型: 数据定义 (R11 拆分)
============================================================
原 world_model.py (1479 行) 拆分出的子模块之一。
完整拆分: world_model_defs.py(数据) / world_model_engine.py(引擎) /
          world_model_abstract.py(抽象+内置后端) / world_model_factory.py(工厂) /
          world_model.py(薄门面, 既有导入零破坏)。
"""

from __future__ import annotations

import logging
import json, math, time, random, uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union, Callable
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod
from collections import defaultdict, deque

import numpy as np

logger = logging.getLogger("laap.agi.world_model")


# ═══ 枚举与数据结构 (自原 world_model.py 拆分) ═══
class WorldModelType(str, Enum):
    LOCAL = "local"
    OPENWORLDLIB = "openworldlib"
    LINGBOT = "lingbot"
    HUNYUAN = "hunyuan"
    HYBRID = "hybrid"
    QUANTUM = "quantum"
    GENESIS = "genesis"       # Genesis World 物理仿真引擎


# ═══════════════════════════════════════════════════════════════
# 核心类型系统 (from laap/agi/world_model.py)
# ═══════════════════════════════════════════════════════════════

class EntityType(str, Enum):
    """实体类型"""
    OBJECT = "object"           # 物理对象
    AGENT = "agent"             # AI Agent
    USER = "user"               # 人类用户
    LOCATION = "location"       # 位置
    ACTION = "action"           # 动作
    EVENT = "event"             # 事件
    CONCEPT = "concept"         # 抽象概念
    RELATIONSHIP = "relationship"  # 关系
    SOCIAL = "social"           # 社会实体
    UNKNOWN = "unknown"


class RelationType(str, Enum):
    """关系类型"""
    SPATIAL = "spatial"             # 空间关系
    TEMPORAL = "temporal"           # 时间关系
    CAUSAL = "causal"               # 因果关系
    HIERARCHICAL = "hierarchical"   # 层级关系
    SOCIAL = "social"               # 社会关系
    FUNCTIONAL = "functional"       # 功能关系
    TEMPORAL_SEQUENCE = "temporal_sequence"  # 时序关系
    EMOTIONAL = "emotional"         # 情感关系
    OWNERSHIP = "ownership"         # 所有权
    UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════════
# 物理属性 (from root/world_model.py)
# ═══════════════════════════════════════════════════════════════

@dataclass
class PhysicalProperties:
    """实体的物理属性"""
    mass: float = 1.0
    volume: float = 1.0
    state: str = "solid"           # solid | liquid | gas | plasma
    temperature: float = 20.0
    is_container: bool = False
    max_capacity: float = 0.0
    current_contents: float = 0.0
    is_breakable: bool = False
    is_living: bool = False
    is_movable: bool = True

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


@dataclass
class SpatialPos:
    """空间位置"""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    container_id: Optional[str] = None
    surface_of: Optional[str] = None

    def distance_to(self, other: "SpatialPos") -> float:
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2 + (self.z - other.z)**2)

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "z": self.z,
                "container_id": self.container_id, "surface_of": self.surface_of}


# ═══════════════════════════════════════════════════════════════
# 社会属性 (NEW)
# ═══════════════════════════════════════════════════════════════

@dataclass
class SocialAttributes:
    """实体的社会属性"""
    trust: float = 0.5             # 信任度 0~1
    affection: float = 0.5         # 亲密度 0~1
    power_relation: float = 0.0    # 权力关系 (-1 服从 ~ +1 支配)
    cooperation: float = 0.5       # 合作倾向 0~1
    conflict: float = 0.0          # 冲突程度 0~1
    role: str = "unknown"          # 社会角色
    group_id: Optional[str] = None # 所属群体

    def to_dict(self) -> dict:
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in self.__dict__.items()}


# ═══════════════════════════════════════════════════════════════
# 统一实体 (合并物理 + 社会 + 抽象)
# ═══════════════════════════════════════════════════════════════

@dataclass
class Entity:
    """统一实体 — 物理/社会/抽象三位一体"""
    eid: str = ""
    name: str = ""
    entity_type: EntityType = EntityType.UNKNOWN

    # 物理层
    phys: Optional[PhysicalProperties] = None

    # 空间层
    pos: Optional[SpatialPos] = None

    # 社会层 (NEW)
    social: Optional[SocialAttributes] = None

    # 通用属性
    properties: Dict[str, Any] = field(default_factory=dict)

    # 关系图谱: {relation_type: [(target_id, strength, timestamp)]}
    relationships: Dict[str, List[Tuple[str, float, float]]] = field(default_factory=dict)

    # 时间线
    history: List[Dict] = field(default_factory=list)
    max_history: int = 100

    # 元数据
    confidence: float = 0.5
    source: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    version: int = 1

    def __post_init__(self):
        if not self.eid:
            self.eid = f"ent_{uuid.uuid4().hex[:8]}"
        if self.phys is None and self.entity_type in (EntityType.OBJECT, EntityType.LOCATION, EntityType.UNKNOWN):
            self.phys = PhysicalProperties()
        if self.pos is None:
            self.pos = SpatialPos()
        if self.social is None and self.entity_type in (EntityType.AGENT, EntityType.USER, EntityType.SOCIAL):
            self.social = SocialAttributes()

    def add_history(self, event_type: str, data: dict):
        """记录一个历史事件"""
        self.history.append({
            "t": time.time(),
            "type": event_type,
            "data": data,
        })
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
        self.version += 1
        self.last_seen = time.time()

    def to_dict(self) -> dict:
        return {
            "eid": self.eid, "name": self.name,
            "type": self.entity_type.value,
            "phys": self.phys.to_dict() if self.phys else None,
            "pos": self.pos.to_dict() if self.pos else None,
            "social": self.social.to_dict() if self.social else None,
            "properties_keys": list(self.properties.keys()),
            "relationships": {
                k: [(t, round(s, 3), ts) for t, s, ts in v]
                for k, v in self.relationships.items()
            },
            "history_count": len(self.history),
            "confidence": self.confidence,
            "source": self.source,
            "version": self.version,
            "last_updated": self.last_updated,
        }


# ═══════════════════════════════════════════════════════════════
# 关系
# ═══════════════════════════════════════════════════════════════

@dataclass
class Relation:
    """实体间关系"""
    id: str = ""
    source_id: str = ""
    target_id: str = ""
    relation_type: RelationType = RelationType.UNKNOWN
    strength: float = 0.5
    properties: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.id:
            self.id = f"rel_{uuid.uuid4().hex[:8]}"


# ═══════════════════════════════════════════════════════════════
# 反事实空间 (NEW)
# ═══════════════════════════════════════════════════════════════

@dataclass
class CounterfactualBranch:
    """一条反事实世界线"""
    id: str = ""
    label: str = ""                     # "如果没关门" / "如果早起了"
    condition: Dict[str, Any] = field(default_factory=dict)
    predicted_outcome: Dict[str, Any] = field(default_factory=dict)
    probability: float = 0.5
    coherence: float = 0.5              # 与已有知识的一致性
    causal_chain: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label,
            "condition": self.condition,
            "outcome": self.predicted_outcome,
            "probability": round(self.probability, 3),
            "coherence": round(self.coherence, 3),
            "causal_chain": self.causal_chain,
        }


# ═══════════════════════════════════════════════════════════════
# 模拟结果
# ═══════════════════════════════════════════════════════════════

@dataclass
class SimulationResult:
    """世界模型模拟结果"""
    possible_outcomes: List[Dict[str, Any]] = field(default_factory=list)
    probabilities: List[float] = field(default_factory=list)
    confidence: float = 0.0
    simulation_time: float = 0.0
    counterfactuals: List[CounterfactualBranch] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    # 向后兼容字段：AGIAgent.process_interaction 引用 steps / assumptions
    steps: List[Dict[str, Any]] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 常识知识库
# ═══════════════════════════════════════════════════════════════

@dataclass
class CommonsenseKnowledge:
    """常识知识库"""
    physical_rules: Dict[str, float] = field(default_factory=dict)
    social_rules: Dict[str, float] = field(default_factory=dict)
    causal_heuristics: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if not self.physical_rules:
            self.physical_rules = {
                "gravity": 1.0, "solidity": 1.0, "causality": 1.0,
                "objects_fall": 0.95, "liquids_flow": 0.9,
                "heat_rises": 0.8, "breakable_breaks": 0.7,
            }
        if not self.social_rules:
            self.social_rules = {
                "greeting_reciprocity": 0.9,
                "question_answer": 0.95,
                "trust_builds_over_time": 0.7,
                "apology_restores_trust": 0.6,
                "repeated_interaction_strengthens_bond": 0.8,
            }
        if not self.causal_heuristics:
            self.causal_heuristics = {
                "same_cause_same_effect": 0.8,
                "correlation_not_causation": 0.5,
                "common_cause": 0.6,
                "temporal_precedence": 0.9,
            }

    def get_relevant(self, query: str) -> List[Tuple[str, float]]:
        """获取与查询相关的常识规则"""
        results = []
        query_lower = query.lower()
        for rules in [self.physical_rules, self.social_rules, self.causal_heuristics]:
            for name, strength in rules.items():
                if query_lower in name.lower() or any(
                    word in name.lower() for word in query_lower.split()
                ):
                    results.append((name, strength))
        return results[:10]


# ═══════════════════════════════════════════════════════════════
# 统一世界模型
# ═══════════════════════════════════════════════════════════════


