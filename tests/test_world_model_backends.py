"""
World Models 后端可导入回归测试 (GAP-B)
========================================
验证 openworldlib/hunyuan/lingbot 三个后端在 CausalLink 修复后
可正常导入与实例化 (上游依赖不可用时优雅降级 available=False)。

运行:
    python -m pytest tests/test_world_model_backends.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.agi.world_model import CausalLink
import laap.agi.world_models as wm


def test_causal_link_dataclass():
    cl = CausalLink(
        id="x", condition="a", effect="b",
        probability=0.8, confidence=0.5, domain="test", latency=0.1,
    )
    assert cl.id == "x"
    assert cl.condition == "a"
    assert cl.effect == "b"
    assert cl.probability == 0.8


def test_backends_no_longer_degraded_to_none():
    # 修复前这三个后端因缺失 CausalLink 被 try/except 降级为 None
    assert wm.OpenWorldLibModel is not None
    assert wm.HunYuanWorldModel is not None
    assert wm.LingBotWorldModel is not None
    assert wm.GenesisWorldModel is not None


def test_backends_instantiate_with_graceful_degrade():
    from laap.agi.world_models.openworldlib import OpenWorldLibModel
    from laap.agi.world_models.hunyuan import HunYuanWorldModel
    from laap.agi.world_models.lingbot import LingBotWorldModel
    for cls in (OpenWorldLibModel, HunYuanWorldModel, LingBotWorldModel):
        obj = cls()
        assert obj is not None
        assert hasattr(obj, "_is_available")


def test_openworldlib_add_causal_link_returns_link():
    from laap.agi.world_models.openworldlib import OpenWorldLibModel
    obj = OpenWorldLibModel()
    cl = obj.add_causal_link("cond", "effect", probability=0.7, confidence=0.4)
    assert cl.condition == "cond"
    assert cl.effect == "effect"
    assert cl.probability == 0.7
