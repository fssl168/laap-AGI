"""
LAAP AGI — 统一世界模型: 工厂 (R11 拆分)
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

from .world_model_defs import WorldModelType
from .world_model_abstract import (
    AbstractWorldModel, LocalWorldModel, QuantumWorldModelAdapter,
)


# ═══ 工厂 (自原 world_model.py 拆分) ═══
def _create_world_model_internal(model_type: Union[str, "WorldModelType"] = "local",
                       name: str = None, **kwargs) -> AbstractWorldModel:
    """创建世界模型实例（内部实现）。

    被 ``create_world_model`` 包装以支持 per-sandbox 实例化。
    保留此函数以维持向后兼容——既有调用方仍可使用
    ``_create_world_model_internal(model_type="local")``。
    """
    # WorldModelType 已在模块顶层定义
    if isinstance(model_type, str):
        model_type = WorldModelType(model_type.lower())

    if not name:
        name = f"{model_type.value}-world"

    # P0-3: QUANTUM 类型真正返回 QuantumWorldModelAdapter(组合量子+符号)
    if model_type == WorldModelType.QUANTUM:
        return QuantumWorldModelAdapter(name=name)
    if model_type in (WorldModelType.LOCAL, WorldModelType.HYBRID):
        return LocalWorldModel(name=name)

    # 尝试加载外部后端
    try:
        if model_type == WorldModelType.OPENWORLDLIB:
            from laap.agi.world_models.openworldlib import OpenWorldLibModel
            return OpenWorldLibModel(name=name, **kwargs)
        elif model_type == WorldModelType.LINGBOT:
            from laap.agi.world_models.lingbot import LingBotWorldModel
            return LingBotWorldModel(name=name, **kwargs)
        elif model_type == WorldModelType.HUNYUAN:
            from laap.agi.world_models.hunyuan import HunYuanWorldModel
            return HunYuanWorldModel(name=name, **kwargs)
        elif model_type == WorldModelType.GENESIS:
            from laap.agi.world_models.genesis import GenesisWorldModel
            return GenesisWorldModel(name=name, **kwargs)
    except ImportError as e:
        logger.warning(f"World model {model_type} not available: {e}")

    return LocalWorldModel(name=name)


def create_world_model(sandbox_id: Optional[str] = None,
                       model_type: Union[str, "WorldModelType"] = "local",
                       name: str = None, **kwargs) -> AbstractWorldModel:
    """为指定 sandbox 创建独立的世界模型实例。

    新签名（LAAP 2.0）：
        ``create_world_model(sandbox_id, model_type="local")``

    向后兼容模式：当 ``sandbox_id`` 为 None 时，等同于旧的
    ``_create_world_model_internal(model_type, name, **kwargs)``。
    这保证了既有调用方 ``create_world_model(model_type="local")``
    仍然可用。

    Args:
        sandbox_id: 沙箱唯一标识。为 None 时进入向后兼容模式
            （不注入 sandbox 标签，行为与旧 API 完全一致）。
        model_type: 模型类型（默认 "local"）。当 ``sandbox_id``
            为 None 时，此参数也可作为第一个位置参数传入。
        name: 模型名称。为 None 时自动生成。
        **kwargs: 透传给底层世界模型构造器。

    Returns:
        独立的 WorldModel 实例。若 ``sandbox_id`` 不为 None，
        实例的 ``_sandbox_id`` 与 ``_project_snapshot`` 属性会被
        正确初始化。
    """
    # ── 向后兼容分支 ──
    # 旧 API: create_world_model(model_type="local", name=None, **kwargs)
    # 当 sandbox_id 是字符串形式的 WorldModelType（如 "local"/"hybrid"），
    # 或 sandbox_id 显式为 None 时，进入兼容路径。
    if sandbox_id is not None and isinstance(sandbox_id, str):
        # 检查 sandbox_id 是否其实是 model_type（旧 API 调用）
        try:
            WorldModelType(sandbox_id.lower())
            # sandbox_id 实际上是 model_type 字符串——走旧 API
            # 将 sandbox_id 推到 model_type 位置
            old_model_type = sandbox_id  # type: ignore[assignment]
            return _create_world_model_internal(
                model_type=old_model_type, name=name, **kwargs
            )
        except ValueError:
            # sandbox_id 不是合法的 WorldModelType——视为正常 sandbox_id
            pass

    # ── 新 API 分支 ──
    instance = _create_world_model_internal(
        model_type=model_type, name=name, **kwargs
    )

    if sandbox_id is not None:
        instance._sandbox_id = sandbox_id
        instance._project_snapshot = None
        logger.info(
            f"Per-sandbox WorldModel created — sandbox_id={sandbox_id}, "
            f"type={model_type}"
        )

    return instance


# ═══════════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════════


