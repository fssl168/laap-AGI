"""
LAAP AGI — 统一世界模型: 抽象+后端 (R11 拆分)
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
    EntityType, RelationType, Entity, Relation, SimulationResult,
)
from .world_model_engine import UnifiedWorldModel


# ═══ 抽象世界模型 + 内置后端 (自原 world_model.py 拆分) ═══
class AbstractWorldModel(ABC):
    """抽象世界模型基类 — 保持与旧代码的接口兼容"""

    def __init__(self, name: str = "world"):
        self.name = name
        self.unified = UnifiedWorldModel(name=name)
        self.entities = self.unified.entities
        self.relations = self.unified.relations
        # Per-Sandbox 标签与 ProjectSnapshot 缓存
        self._sandbox_id: Optional[str] = None
        self._project_snapshot: Optional[Any] = None

    @abstractmethod
    def add_entity(self, name: str, entity_type: EntityType = EntityType.UNKNOWN,
                   properties: Dict = None) -> Entity:
        return self.unified.add_entity(name, entity_type, properties)

    @abstractmethod
    def add_relation(self, source_id: str, target_id: str,
                     relation_type: RelationType = RelationType.UNKNOWN,
                     strength: float = 0.5) -> Relation:
        return self.unified.add_relation(source_id, target_id, relation_type, strength)

    @abstractmethod
    def predict(self, entity_id: str, horizon: float = 1.0, **kwargs) -> SimulationResult:
        return self.unified.predict(entity_id, horizon, **kwargs)

    @abstractmethod
    def simulate(self, actions: List[Dict]) -> SimulationResult:
        return SimulationResult()

    def query(self, query: str) -> List[Dict[str, Any]]:
        return self.unified.query(query)

    def stats(self) -> Dict[str, Any]:
        return self.unified.stats()

    def set_causal_engine(self, engine) -> None:
        """转发因果引擎注入到底层 UnifiedWorldModel。

        P0-1: 打通世界模型 ↔ 因果引擎的连接(原 AGIAgent 未调用此桥接)。
        """
        self.unified.set_causal_engine(engine)

    def update_from_snapshot(self, snapshot: Any) -> None:
        """从 ProjectSnapshot 更新世界模型。

        将 git_state、file_tree、tech_debt 等注入到世界模型中，
        作为该沙箱对当前项目状态的理解。

        默认实现仅存储 snapshot 引用到 ``self._project_snapshot``，
        派生类可重写以做更复杂的语义抽取（如将文件树映射为实体、
        将 tech_debt_markers 映射为社会信任度等）。

        Args:
            snapshot: ProjectSnapshot 实例（来自 laap.sandbox._types）。
        """
        self._project_snapshot = snapshot
        logger.debug(
            f"[WorldModel] snapshot updated — sandbox_id={self._sandbox_id}, "
            f"snapshot={getattr(snapshot, 'root_path', '<unknown>')}"
        )


class LocalWorldModel(AbstractWorldModel):
    """本地世界模型 — 基于 UnifiedWorldModel"""

    def __init__(self, name: str = "local-world"):
        super().__init__(name)

    def add_entity(self, name: str, entity_type: EntityType = EntityType.UNKNOWN,
                   properties: Dict = None, **kwargs) -> Entity:
        return self.unified.add_entity(name, entity_type, properties)

    def add_relation(self, source_id: str, target_id: str,
                     relation_type: RelationType = RelationType.UNKNOWN,
                     strength: float = 0.5, **kwargs) -> Relation:
        return self.unified.add_relation(source_id, target_id, relation_type, strength)

    def predict(self, entity_id: str, horizon: float = 1.0, **kwargs) -> SimulationResult:
        return self.unified.predict(entity_id, horizon, **kwargs)

    def simulate(self, actions: List[Dict]) -> SimulationResult:
        return SimulationResult()


class QuantumWorldModelAdapter(AbstractWorldModel):
    """量子世界模型适配器 — 组合 UnifiedWorldModel + QuantumWorldModel。

    P0-3: 修复 create_world_model("quantum") 类型欺骗问题。
    原工厂对所有类型都返回 LocalWorldModel,QUANTUM 枚举形同虚设。

    本适配器同时持有:
      - UnifiedWorldModel(符号化实体/关系/因果/反事实,通过 self.unified)
      - QuantumWorldModel(量子叠加态/酉演化/Born 坍缩,通过 self.quantum)
    并在 predict/simulate 中融合两者结果。
    """

    def __init__(self, name: str = "quantum-world"):
        super().__init__(name)
        self.quantum = None
        try:
            # 延迟导入,避免根目录模块缺失时影响主流程
            import sys
            import os
            _laap_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if _laap_root not in sys.path:
                sys.path.insert(0, _laap_root)
            from quantum_world_model import QuantumWorldModel
            self.quantum = QuantumWorldModel()
            logger.info("[QuantumWorldModelAdapter] 量子世界模型已加载")
        except Exception as e:
            logger.warning(f"[QuantumWorldModelAdapter] 量子模型不可用,降级为纯符号模式: {e}")
            self.quantum = None

    def add_entity(self, name: str, entity_type: EntityType = EntityType.UNKNOWN,
                   properties: Dict = None, **kwargs) -> Entity:
        return self.unified.add_entity(name, entity_type, properties)

    def add_relation(self, source_id: str, target_id: str,
                     relation_type: RelationType = RelationType.UNKNOWN,
                     strength: float = 0.5, **kwargs) -> Relation:
        return self.unified.add_relation(source_id, target_id, relation_type, strength)

    def predict(self, entity_id: str, horizon: float = 1.0, **kwargs) -> SimulationResult:
        """融合符号预测与量子坍缩。

        若量子模型中存在同名实体,则用其 measure() 结果增强预测置信度;
        否则回退到 UnifiedWorldModel.predict。
        """
        sym_result = self.unified.predict(entity_id, horizon, **kwargs)
        if self.quantum is not None and entity_id in self.quantum.entities:
            try:
                qe = self.quantum.entities[entity_id]
                collapsed = qe.observe("state") if hasattr(qe, "observe") else {}
                # 量子坍缩结果作为 possible_outcomes 的一部分
                outcomes = list(sym_result.possible_outcomes) if sym_result.possible_outcomes else []
                for k, v in collapsed.items() if isinstance(collapsed, dict) else []:
                    outcomes.append(f"quantum:{k}={v}")
                # 量子熵提高置信度(熵低 → 高置信)
                entropy = qe.entropy() if hasattr(qe, "entropy") else 0.5
                q_conf = max(0.1, min(0.99, 1.0 / (1.0 + entropy)))
                # 与符号置信度几何平均
                sym_conf = sym_result.confidence or 0.5
                sym_result.confidence = (sym_conf * q_conf) ** 0.5
                sym_result.possible_outcomes = outcomes
                sym_result.details = {**(sym_result.details or {}), "quantum_entropy": entropy}
            except Exception as e:
                logger.debug(f"[QuantumWorldModelAdapter] 量子增强失败: {e}")
        return sym_result

    def simulate(self, actions: List[Dict]) -> SimulationResult:
        """对量子模型施加酉演化,与符号模拟结果合并。"""
        sym_result = SimulationResult()
        if self.quantum is not None and actions:
            try:
                for action in actions:
                    a_type = action.get("action") or action.get("type", "")
                    target = action.get("target", "")
                    instrument = action.get("instrument", "")
                    if a_type in self.quantum.UNITARY_MAP and target in self.quantum.entities:
                        q_result = self.quantum.simulate(a_type, target, instrument)
                        if isinstance(q_result, dict):
                            sym_result.details = {**(sym_result.details or {}), "quantum": q_result}
                            sym_result.confidence = q_result.get("confidence", 0.5)
            except Exception as e:
                logger.debug(f"[QuantumWorldModelAdapter] 量子模拟失败: {e}")
        return sym_result

    def quantum_stats(self) -> Dict[str, Any]:
        """返回量子模型统计(若可用)。"""
        if self.quantum is None:
            return {"available": False}
        return {
            "available": True,
            "entities": len(self.quantum.entities),
            "interactions": len(self.quantum.known_interactions),
            "total_interactions": getattr(self.quantum, "_total_interactions", 0),
        }


# 工厂函数（内部实现，保持向后兼容）

