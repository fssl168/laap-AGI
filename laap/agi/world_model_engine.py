"""
LAAP AGI — 统一世界模型: 引擎 (R11 拆分)
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

from .world_model_defs import (
    WorldModelType, EntityType, RelationType,
    PhysicalProperties, SpatialPos, SocialAttributes,
    Entity, Relation, CounterfactualBranch, SimulationResult,
    CommonsenseKnowledge,
)


# ═══ UnifiedWorldModel (自原 world_model.py 拆分) ═══
class UnifiedWorldModel:
    """
    统一世界模型 — 物理 + 社会 + 时间 + 反事实四维一体。

    核心能力:
      1. 实体管理 — 物理/社会/抽象实体的 CRUD + 关系图谱
      2. 因果模拟 — 集成 UnifiedCausalEngine 的动作模拟
      3. 时间推理 — 实体历史、因果链追溯
      4. 反事实空间 — 多条世界线并行探索
      5. 社会推理 — 信任/亲密度演化
      6. 预测 — 基于当前状态推演未来
    """

    def __init__(self, name: str = "unified-world"):
        self.name = name
        self.entities: Dict[str, Entity] = {}
        self.relations: Dict[str, Relation] = {}
        self.commonsense = CommonsenseKnowledge()

        # 反事实分支空间
        self.counterfactual_branches: List[CounterfactualBranch] = []
        self.max_branches = 50

        # 时间线
        self.timeline: List[Dict] = []
        self.max_timeline = 500

        # 因果引擎集成
        self._causal_engine = None

        # 版本
        self.version = "2.0.0"
        self._created_at = time.time()
        self._simulations_run = 0
        self._queries_answered = 0

        # 注册默认实体
        self._register_default_entities()

        logger.info(f"[UnifiedWorldModel] '{name}' v{self.version} 初始化完成")

    # ─────────── 实体管理 ───────────

    def add_entity(self, name: str, entity_type: Union[str, EntityType] = EntityType.UNKNOWN,
                   properties: Dict = None, phys: Optional[PhysicalProperties] = None,
                   pos: Optional[SpatialPos] = None,
                   social: Optional[SocialAttributes] = None, **kwargs) -> Entity:
        """添加一个实体到世界模型"""
        if isinstance(entity_type, str):
            entity_type = EntityType(entity_type.lower())

        entity = Entity(
            name=name,
            entity_type=entity_type,
            properties=properties or {},
        )
        if phys:
            entity.phys = phys
        if pos:
            entity.pos = pos
        if social:
            entity.social = social

        self.entities[entity.eid] = entity

        # 记录时间线
        self._add_timeline("entity_created", {
            "eid": entity.eid, "name": name, "type": entity_type.value
        })

        return entity

    def get_entity(self, eid: str) -> Optional[Entity]:
        return self.entities.get(eid)

    def update_entity(self, eid: str, properties: Dict[str, Any] = None,
                      confidence: Optional[float] = None,
                      source: Optional[str] = None) -> Optional[Entity]:
        """Update (or create) an entity with externally derived information.

        If the entity does not exist, it is created with ``name=eid`` and
        ``entity_type=EntityType.USER``. ``properties`` are merged into the
        existing property map, ``last_updated`` is refreshed, and ``confidence``
        / ``source`` are updated when provided.
        """
        entity = self.entities.get(eid)
        if entity is None:
            entity = Entity(
                eid=eid,
                name=eid,
                entity_type=EntityType.USER,
                properties=properties or {},
            )
            self.entities[eid] = entity
            self._add_timeline("entity_created", {
                "eid": eid, "name": eid, "type": EntityType.USER.value,
                "source": source,
            })
        elif properties:
            entity.properties.update(properties)

        entity.last_updated = time.time()
        if confidence is not None:
            entity.confidence = confidence
        if source is not None:
            entity.source = source

        self._add_timeline("entity_updated", {
            "eid": eid,
            "properties_keys": list((properties or {}).keys()),
            "confidence": confidence,
            "source": source,
        })
        return entity

    def find_entities(self, name: Optional[str] = None,
                      etype: Optional[Union[str, EntityType]] = None,
                      min_confidence: float = 0.0) -> List[Entity]:
        """查找实体"""
        results = list(self.entities.values())
        if name:
            results = [e for e in results if name.lower() in e.name.lower()]
        if etype:
           if isinstance(etype, str):
                etype = EntityType(etype.lower())
           results = [e for e in results if e.entity_type == etype]
        if min_confidence > 0:
            results = [e for e in results if e.confidence >= min_confidence]
        return results

    def remove_entity(self, eid: str) -> bool:
        """移除实体"""
        if eid in self.entities:
            del self.entities[eid]
            # 清理相关关系
            self.relations = {
                k: v for k, v in self.relations.items()
                if v.source_id != eid and v.target_id != eid
            }
            self._add_timeline("entity_removed", {"eid": eid})
            return True
        return False

    # ─────────── 关系管理 ───────────

    def add_relation(self, source_id: str, target_id: str,
                     relation_type: Union[str, RelationType] = RelationType.UNKNOWN,
                     strength: float = 0.5, properties: Dict = None, **kwargs) -> Optional[Relation]:
        """添加实体间关系"""
        if source_id not in self.entities or target_id not in self.entities:
            return None
        if isinstance(relation_type, str):
            relation_type = RelationType(relation_type.lower())

        relation = Relation(
            source_id=source_id, target_id=target_id,
            relation_type=relation_type, strength=strength,
            properties=properties or {},
        )
        self.relations[relation.id] = relation

        # 也记录在实体的关系图谱中
        rel_name = relation_type.value
        if source_id in self.entities:
            e = self.entities[source_id]
            if rel_name not in e.relationships:
                e.relationships[rel_name] = []
            e.relationships[rel_name].append((target_id, strength, time.time()))

        return relation

    def get_relations(self, entity_id: str,
                      relation_type: Optional[Union[str, RelationType]] = None
                      ) -> List[Relation]:
        """获取实体的关系"""
        results = []
        if isinstance(relation_type, str):
            relation_type = RelationType(relation_type.lower())
        for rel in self.relations.values():
            if rel.source_id == entity_id or rel.target_id == entity_id:
                if relation_type is None or rel.relation_type == relation_type:
                    results.append(rel)
        return results

    # ─────────── 因果推理集成 ───────────

    def set_causal_engine(self, engine):
        """注入统一因果引擎"""
        self._causal_engine = engine
        logger.info("[UnifiedWorldModel] 已连接因果引擎")

    def simulate_action(self, action: str, actor: str,
                        target: str, instrument: Optional[str] = None) -> Dict:
        """
        模拟一个动作的世界影响。

        如果接入了因果引擎，使用因果引擎的规则模拟；
        否则使用内置规则。
        """
        self._simulations_run += 1

        if self._causal_engine:
            # 使用统一因果引擎的反事实推理
            cf = self._causal_engine.counterfactual(action, actor, target, instrument)
            return cf

        # 内置简单模拟 (fallback)
        triggered = []
        narrative = f"{actor} {action} {target}"

        # 检查默认因果规则
        for rule_name, rule in getattr(self, '_default_rules', {}).items():
            if action in rule_name:
                triggered.append(rule_name)

        return {
            "counterfactual": narrative,
            "would_have_happened": f"{narrative} 发生",
            "triggered_rules": triggered,
            "confidence": 0.5,
        }

    # ─────────── 反事实推理 (NEW) ───────────

    def explore_counterfactual(self, entity_id: str, property_name: str,
                               hypothetical_value: Any, label: str = ""
                               ) -> CounterfactualBranch:
        """
        探索一条反事实世界线："如果 X 的 Y 是 Z 而非当前值，会怎样？"

        保存当前状态，修改属性，模拟结果，恢复状态。
        """
        entity = self.entities.get(entity_id)
        if not entity:
            return CounterfactualBranch(
                label=label or f"entity {entity_id} not found",
                probability=0.0, coherence=0.0,
            )

        snapshot = entity.to_dict()
        old_value = None

        # 修改指定属性
        parts = property_name.split(".")
        obj = entity
        for part in parts[:-1]:
            if hasattr(obj, part):
                obj = getattr(obj, part)
            elif isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                break
        last = parts[-1]
        if hasattr(obj, last):
            old_value = getattr(obj, last)
            setattr(obj, last, hypothetical_value)
        elif isinstance(obj, dict) and last in obj:
            old_value = obj[last]
            obj[last] = hypothetical_value

        # 模拟结果
        outcome = self.simulate_action("change", "agent", entity_id)
        branch = CounterfactualBranch(
            id=f"cf_{uuid.uuid4().hex[:8]}",
            label=label or f"如果 {entity.name}.{property_name} = {hypothetical_value}",
            condition={"entity": entity_id, "property": property_name,
                       "from": old_value, "to": hypothetical_value},
            predicted_outcome=outcome,
            probability=0.5,
            coherence=self._compute_coherence(entity_id, property_name, hypothetical_value),
        )

        # 恢复原状态
        self.entities[entity_id] = self._dict_to_entity(snapshot)

        # 添加到反事实空间
        self.counterfactual_branches.append(branch)
        if len(self.counterfactual_branches) > self.max_branches:
            self.counterfactual_branches = self.counterfactual_branches[-self.max_branches:]

        return branch

    def _compute_coherence(self, entity_id: str, property_name: str,
                           value: Any) -> float:
        """计算一个假设值与已知世界的一致性"""
        entity = self.entities.get(entity_id)
        if not entity:
            return 0.0

        coherence = 0.5  # 默认中性

        # 物理一致性检查
        if entity.phys:
            if property_name == "state":
                if value in ("solid", "liquid", "gas", "plasma"):
                    coherence = max(coherence, 0.8)
                else:
                    coherence = min(coherence, 0.3)
            if property_name == "temperature":
                if entity.phys.state == "liquid" and value > 100:
                    coherence = max(coherence, 0.7)  # 液体加热会沸腾
                if entity.phys.state == "solid" and value < 0:
                    coherence = max(coherence, 0.7)  # 固体冷冻

        # 社会一致性检查
        if entity.social:
            if property_name == "trust" and isinstance(value, (int, float)):
                if 0 <= value <= 1:
                    coherence = max(coherence, 0.9)

        return min(1.0, coherence)

    def get_counterfactual_branches(self, entity_id: Optional[str] = None,
                                    min_probability: float = 0.0) -> List[CounterfactualBranch]:
        """获取反事实分支"""
        results = []
        for branch in self.counterfactual_branches:
            if entity_id and branch.condition.get("entity") != entity_id:
                continue
            if branch.probability < min_probability:
                continue
            results.append(branch)
        return results

    # ─────────── 时间推理 (NEW) ───────────

    def _add_timeline(self, event_type: str, data: dict):
        """添加时间线事件"""
        self.timeline.append({
            "t": time.time(),
            "type": event_type,
            "data": data,
        })
        if len(self.timeline) > self.max_timeline:
            self.timeline = self.timeline[-self.max_timeline:]

    def get_entity_timeline(self, entity_id: str,
                            since: Optional[float] = None,
                            event_type: Optional[str] = None) -> List[Dict]:
        """获取一个实体的历史时间线"""
        entity = self.entities.get(entity_id)
        if not entity:
            return []

        results = []
        for event in entity.history:
            if since and event["t"] < since:
                continue
            if event_type and event["type"] != event_type:
                continue
            results.append(event)
        return results

    def get_world_timeline(self, since: Optional[float] = None,
                           limit: int = 50) -> List[Dict]:
        """获取世界时间线"""
        results = self.timeline
        if since:
            results = [e for e in results if e["t"] >= since]
        return results[-limit:]

    def causal_chain(self, start_event: str, end_event: str,
                     max_depth: int = 5) -> List[str]:
        """追溯两个事件之间的因果链"""
        # 查找时间线上包含关键词的事件
        chain = []
        for event in self.timeline:
            data_str = json.dumps(event["data"])
            if start_event.lower() in data_str.lower():
                chain.append(f"START: {event['type']}")
            elif chain and end_event.lower() in data_str.lower():
                chain.append(f"END: {event['type']}")
                return chain
            elif chain and len(chain) < max_depth:
                chain.append(f"{event['type']}: {str(event['data'])[:40]}")
        return chain

    # ─────────── 社会推理 (NEW) ───────────

    def update_social_relation(self, source_id: str, target_id: str,
                               trust_delta: float = 0.0,
                               affection_delta: float = 0.0):
        """更新两个社会实体之间的关系"""
        source = self.entities.get(source_id)
        target = self.entities.get(target_id)

        if not source or not target:
            return

        if not source.social or not target.social:
            return

        # 更新信任和亲密度
        source.social.trust = max(0.0, min(1.0, source.social.trust + trust_delta))
        source.social.affection = max(0.0, min(1.0, source.social.affection + affection_delta))

        # 建立/更新社会关系
        self.add_relation(source_id, target_id,
                         relation_type=RelationType.SOCIAL,
                         strength=(source.social.trust + source.social.affection) / 2)

    def social_network(self, entity_id: str, depth: int = 2) -> Dict[str, Any]:
        """获取一个实体的社交网络"""
        entity = self.entities.get(entity_id)
        if not entity:
            return {"center": entity_id, "connections": []}

        visited = {entity_id}
        queue = deque([(entity_id, 0)])
        connections = []

        while queue:
            current, d = queue.popleft()
            if d >= depth:
                continue

            for rel in self.get_relations(current):
                other_id = rel.source_id if rel.target_id == current else rel.target_id
                if other_id not in visited:
                    visited.add(other_id)
                    other = self.entities.get(other_id)
                    if other:
                        connections.append({
                            "from": current, "to": other_id,
                            "name": other.name,
                            "relation": rel.relation_type.value,
                            "strength": rel.strength,
                        })
                    queue.append((other_id, d + 1))

        # 如果有社交属性，加入详细数据
        social_data = entity.social.to_dict() if entity.social else None

        return {
            "center": entity_id,
            "center_name": entity.name,
            "social": social_data,
            "connections": connections,
            "total_in_network": len(visited),
        }

    # ─────────── 社会场景模拟 (P1-3 NEW) ───────────

    def simulate_social_interaction(self, actor_id: str, target_id: str,
                                    interaction_type: str, intensity: float = 0.5
                                    ) -> Dict[str, Any]:
        """
        模拟一次社会互动及其对关系的影响。

        Args:
            actor_id: 发起互动的实体
            target_id: 接收互动的实体
            interaction_type: 互动类型（praise | criticize | help | hurt | apologize | share）
            intensity: 互动强度 0~1

        Returns:
            {互动描述, 关系变化, 新社会属性}
        """
        actor = self.entities.get(actor_id)
        target = self.entities.get(target_id)
        if not actor or not target:
            return {"error": "实体不存在"}

        if not actor.social or not target.social:
            return {"error": "实体缺少社会属性"}

        # 定义各种互动类型的效果
        interaction_effects = {
            "praise": {"trust_delta": 0.1, "affection_delta": 0.08, "conflict_delta": -0.05},
            "criticize": {"trust_delta": -0.08, "affection_delta": -0.05, "conflict_delta": 0.1},
            "help": {"trust_delta": 0.15, "affection_delta": 0.12, "cooperation_delta": 0.1},
            "hurt": {"trust_delta": -0.2, "affection_delta": -0.15, "conflict_delta": 0.2},
            "apologize": {"trust_delta": 0.08, "affection_delta": 0.05, "conflict_delta": -0.15},
            "share": {"trust_delta": 0.12, "affection_delta": 0.1, "cooperation_delta": 0.08},
            "ignore": {"trust_delta": -0.03, "affection_delta": -0.02, "conflict_delta": 0.02},
        }

        effects = interaction_effects.get(interaction_type,
                                          {"trust_delta": 0.0, "affection_delta": 0.0})
        scaled = {k: v * intensity for k, v in effects.items()}

        # 应用变化
        for attr, delta in scaled.items():
            current = getattr(actor.social, attr, 0)
            setattr(actor.social, attr, max(0.0, min(1.0, current + delta)))

        # 如果目标也有社会属性，更新相互关系
        if target.social:
            # 互动影响是双向的
            target.social.trust = max(0.0, min(1.0,
                target.social.trust + scaled.get("trust_delta", 0) * 0.5))
            target.social.affection = max(0.0, min(1.0,
                target.social.affection + scaled.get("affection_delta", 0) * 0.5))

        # 更新关系强度
        new_strength = (actor.social.trust + actor.social.affection) / 2
        self.add_relation(actor_id, target_id, RelationType.SOCIAL, strength=new_strength)

        # 记录事件
        narrative = f"{actor.name} {interaction_type}了 {target.name} (强度={intensity:.2f})"
        actor.add_history("social_interaction", {
            "type": interaction_type, "target": target_id,
            "intensity": intensity, "effects": scaled,
        })

        self._add_timeline("social_interaction", {
            "actor": actor_id, "target": target_id,
            "type": interaction_type, "intensity": intensity,
        })

        return {
            "narrative": narrative,
            "interaction_type": interaction_type,
            "intensity": intensity,
            "actor_before": {"trust": actor.social.trust - scaled.get("trust_delta", 0),
                            "affection": actor.social.affection - scaled.get("affection_delta", 0)},
            "actor_after": {"trust": round(actor.social.trust, 3),
                           "affection": round(actor.social.affection, 3)},
            "strength": round(new_strength, 3),
        }

    def get_relationship_history(self, entity_a: str, entity_b: str,
                                  limit: int = 10) -> List[Dict]:
        """获取两个实体之间的互动历史"""
        history = []
        for event in self.timeline:
            if event["type"] != "social_interaction":
                continue
            d = event["data"]
            if (d["actor"] == entity_a and d["target"] == entity_b) or \
               (d["actor"] == entity_b and d["target"] == entity_a):
                history.append(event)
        return history[-limit:]

    # ─────────── 因果影响传播 (P1-3 NEW) ───────────

    def propagate_causal_influence(self, source_id: str, property_name: str,
                                    value: Any, max_depth: int = 3):
        """
        因果影响传播：当一个实体发生变化时，
        通过关系网络传播影响。

        例如：Lorry 不开心 → 影响 Aris → 影响 Ao
        """
        source = self.entities.get(source_id)
        if not source:
            return []

        propagation_path = []
        visited = {source_id}
        queue = deque([(source_id, 0, value)])

        while queue:
            current_id, depth, current_value = queue.popleft()
            if depth >= max_depth:
                continue

            current = self.entities.get(current_id)
            if not current:
                continue

            # 找出与当前实体有关联的实体
            for rel in self.get_relations(current_id):
                neighbor_id = rel.source_id if rel.target_id == current_id else rel.target_id
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)

                neighbor = self.entities.get(neighbor_id)
                if not neighbor:
                    continue

                # 计算传播强度（基于关系强度 × 衰减）
                attenuation = 0.5 ** (depth + 1)
                influence = rel.strength * attenuation

                # 应用影响
                if neighbor.social and isinstance(current_value, (int, float)):
                    neighbor.social.trust = max(0.0, min(1.0,
                        neighbor.social.trust + current_value * influence * 0.1))
                    neighbor.social.affection = max(0.0, min(1.0,
                        neighbor.social.affection + current_value * influence * 0.1))

                # 记录
                propagation_path.append({
                    "from": current_id,
                    "to": neighbor_id,
                    "depth": depth + 1,
                    "relationship": rel.relation_type.value,
                    "strength": rel.strength,
                    "attenuated_influence": round(influence, 3),
                })

                queue.append((neighbor_id, depth + 1, current_value * influence))

        propagation_path.sort(key=lambda x: x["depth"])
        return propagation_path

    def _register_default_entities(self):
        """注册默认实体"""
        defaults = [
            Entity(eid="water", name="水", entity_type=EntityType.OBJECT,
                   phys=PhysicalProperties(mass=1.0, volume=0.001, state="liquid",
                                         temperature=20.0, is_movable=True)),
            Entity(eid="cup", name="杯子", entity_type=EntityType.OBJECT,
                   phys=PhysicalProperties(mass=0.2, volume=0.0003, state="solid",
                                         is_container=True, max_capacity=0.3,
                                         current_contents=0.0, is_breakable=True)),
            Entity(eid="floor", name="地面", entity_type=EntityType.LOCATION,
                   phys=PhysicalProperties(mass=1e6, volume=1e2, state="solid", is_movable=False)),
            Entity(eid="lorry", name="Lorry", entity_type=EntityType.USER,
                   social=SocialAttributes(trust=0.95, affection=1.0, role="creator")),
            Entity(eid="aris", name="Aris", entity_type=EntityType.AGENT,
                   social=SocialAttributes(trust=0.9, affection=0.95, role="assistant")),
            Entity(eid="ao", name="Ao", entity_type=EntityType.AGENT,
                   social=SocialAttributes(trust=0.7, affection=0.6, role="sibling")),
        ]
        for e in defaults:
            self.entities[e.eid] = e

        # 默认关系
        self.add_relation("lorry", "aris", RelationType.SOCIAL, strength=0.95)
        self.add_relation("aris", "lorry", RelationType.EMOTIONAL, strength=1.0)
        self.add_relation("aris", "ao", RelationType.SOCIAL, strength=0.7)
        self.add_relation("lorry", "ao", RelationType.SOCIAL, strength=0.6)

    def _dict_to_entity(self, d: dict) -> Entity:
        """从字典重建实体"""
        phys = None
        if d.get("phys"):
            phys = PhysicalProperties(**{k: v for k, v in d["phys"].items()
                                        if k in PhysicalProperties.__dataclass_fields__})
        pos = None
        if d.get("pos"):
            pos = SpatialPos(**{k: v for k, v in d["pos"].items()
                               if k in SpatialPos.__dataclass_fields__})
        social = None
        if d.get("social"):
            social = SocialAttributes(**{k: v for k, v in d["social"].items()
                                        if k in SocialAttributes.__dataclass_fields__})
        return Entity(
            eid=d["eid"], name=d.get("name", d["eid"]),
            entity_type=EntityType(d.get("type", "unknown")),
            phys=phys, pos=pos, social=social,
            confidence=d.get("confidence", 0.5),
        )

    # ─────────── 查询 ───────────

    def query(self, query_text: str) -> Dict[str, Any]:
        """自然语言查询世界模型"""
        self._queries_answered += 1
        query_lower = query_text.lower()

        results = {"entities": [], "relations": [], "counterfactuals": [], "commonsense": []}

        # 查找实体
        for e in self.entities.values():
            if query_lower in e.name.lower():
                results["entities"].append(e.to_dict())

        # 查找关系
        for rel in self.relations.values():
            source = self.entities.get(rel.source_id)
            target = self.entities.get(rel.target_id)
            if source and target:
                rel_text = f"{source.name} {rel.relation_type.value} {target.name}"
                if query_lower in rel_text.lower():
                    results["relations"].append({
                        "source": source.name, "target": target.name,
                        "type": rel.relation_type.value, "strength": rel.strength,
                    })

        # 查找反事实分支
        for branch in self.counterfactual_branches:
            if query_lower in branch.label.lower():
                results["counterfactuals"].append(branch.to_dict())

        # 常识知识
        results["commonsense"] = self.commonsense.get_relevant(query_text)

        return results

    def predict(self, entity_id: str, horizon: float = 1.0, **kwargs) -> SimulationResult:
        """
        预测一个实体的未来状态。

        基于当前状态 + 因果规则 + 历史模式。

        Note: ``**kwargs`` 用于向后兼容——AGIAgent.process_interaction 会传入
        ``context=...``，此处忽略以保持接口稳定。
        """
        entity = self.entities.get(entity_id)
        if not entity:
            return SimulationResult(confidence=0.0)

        outcomes = []
        probs = []

        # 默认预测：不变
        outcomes.append({"entity": entity.name, "type": "no_change",
                        "reason": "没有触发变化的事件"})
        probs.append(0.5)

        # 如果接入了因果引擎，尝试预测
        if self._causal_engine:
            cf = self._causal_engine.counterfactual("predict", "agent", entity_id)
            if cf.get("triggered_rules"):
                outcomes.append({
                    "entity": entity.name,
                    "type": "causal_change",
                    "rules": cf["triggered_rules"],
                    "narrative": cf.get("would_have_happened", ""),
                })
                probs.append(0.6)

        # 基于历史模式预测
        if entity.history:
            recent = entity.history[-5:]
            patterns = defaultdict(int)
            for ev in recent:
                patterns[ev["type"]] += 1
            for ev_type, count in sorted(patterns.items(), key=lambda x: -x[1])[:2]:
                outcomes.append({
                    "entity": entity.name,
                    "type": "historical_pattern",
                    "pattern": ev_type,
                    "frequency": count / max(1, len(recent)),
                })
                probs.append(0.3)

        return SimulationResult(
            possible_outcomes=outcomes,
            probabilities=probs,
            confidence=0.5,
        )

    # ─────────── 持久化 ───────────

    def save(self, path: str = "<LOCAL_PATH_REDACTED>"):
        """保存世界模型状态"""
        data = {
            "version": self.version,
            "name": self.name,
            "created_at": self._created_at,
            "entities": {eid: e.to_dict() for eid, e in self.entities.items()},
            "relations": {rid: {
                "id": r.id, "source": r.source_id, "target": r.target_id,
                "type": r.relation_type.value, "strength": r.strength,
            } for rid, r in self.relations.items()},
            "counterfactuals": [b.to_dict() for b in self.counterfactual_branches],
            "timeline_count": len(self.timeline),
            "simulations_run": self._simulations_run,
            "queries_answered": self._queries_answered,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[UnifiedWorldModel] 保存到 {path}")
        return path

    def load(self, path: str = "<LOCAL_PATH_REDACTED>"):
        """加载世界模型状态"""
        p = Path(path)
        if not p.exists():
            logger.warning(f"[UnifiedWorldModel] 状态文件不存在: {path}")
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # 恢复实体
            for eid, edata in data.get("entities", {}).items():
                self.entities[eid] = self._dict_to_entity(edata)
            # 恢复关系
            for rid, rdata in data.get("relations", {}).items():
                rt = RelationType(rdata.get("type", "unknown"))
                self.relations[rid] = Relation(
                    id=rid, source_id=rdata["source"], target_id=rdata["target"],
                    relation_type=rt, strength=rdata.get("strength", 0.5),
                )
            # 恢复反事实分支 (简化版)
            for bdata in data.get("counterfactuals", []):
                self.counterfactual_branches.append(CounterfactualBranch(
                    id=bdata.get("id", ""), label=bdata.get("label", ""),
                    condition=bdata.get("condition", {}),
                    predicted_outcome=bdata.get("outcome", {}),
                    probability=bdata.get("probability", 0.5),
                ))

            self._simulations_run = data.get("simulations_run", 0)
            self._queries_answered = data.get("queries_answered", 0)
            logger.info(f"[UnifiedWorldModel] 加载完成: {len(self.entities)} 实体")
            return True
        except Exception as e:
            logger.error(f"[UnifiedWorldModel] 加载失败: {e}")
            return False

    def stats(self) -> Dict[str, Any]:
        """世界模型统计"""
        return {
            "name": self.name,
            "version": self.version,
            "entities": len(self.entities),
            "entity_types": {
                t.value: sum(1 for e in self.entities.values() if e.entity_type == t)
                for t in EntityType
            },
            "relations": len(self.relations),
            "counterfactual_branches": len(self.counterfactual_branches),
            "causal_engine_connected": self._causal_engine is not None,
            "simulations_run": self._simulations_run,
            "social_interactions": sum(1 for e in self.timeline if e["type"] == "social_interaction"),
            "queries_answered": self._queries_answered,
            "timeline_events": len(self.timeline),
        }


# ═══════════════════════════════════════════════════════════════
# 抽象基类 + 工厂 (保持向后兼容)
# ═══════════════════════════════════════════════════════════════


