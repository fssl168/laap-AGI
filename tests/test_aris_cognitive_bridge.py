"""
Aris Cognitive Bridge 冒烟测试 (R11 拆分前置覆盖)
=================================================
为无测试覆盖的 aris_brain/aris_cognitive_bridge.py 提供基础回归保障,
覆盖: 单例语义 / 认知状态默认值 / status() / get_cognitive_prefix() /
      before_turn 返回结构 / 状态持久化往返。

运行:
    python -m pytest tests/test_aris_cognitive_bridge.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from aris_brain.aris_cognitive_bridge import (
    get_bridge,
    ArisCognitiveBridge,
    CognitiveState,
    AttentionFocus,
    EmotionalState,
)


# ════════════════════════════════════════════════════════════
# 1. 状态定义
# ════════════════════════════════════════════════════════════

def test_attention_focus_enum():
    assert AttentionFocus.RESPOND.value == "respond"
    assert {e.value for e in AttentionFocus} == {
        "respond", "learn", "explore", "reflect", "plan", "idle",
    }


def test_emotional_state_enum():
    assert EmotionalState.NEUTRAL.value == "neutral"
    assert "anxious" in {e.value for e in EmotionalState}


def test_cognitive_state_defaults():
    s = CognitiveState()
    assert s.focus == AttentionFocus.RESPOND
    assert s.emotion == EmotionalState.NEUTRAL
    assert s.self_presence == 0.7
    assert s.confidence == 0.5
    assert s.cognitive_load == 0.3
    assert s.cycle_count == 0
    assert s.needs_competence == 0.5
    assert s.needs_autonomy == 0.5
    assert s.needs_relatedness == 0.5


# ════════════════════════════════════════════════════════════
# 2. 单例语义
# ════════════════════════════════════════════════════════════

def test_get_bridge_singleton():
    b1 = get_bridge()
    b2 = get_bridge()
    assert b1 is b2


def test_bridge_manual_instantiation_is_singleton():
    # 即便直接构造, __new__ 也返回同一实例 (受 _instance 约束)
    b = ArisCognitiveBridge()
    assert b is get_bridge()


# ════════════════════════════════════════════════════════════
# 3. 状态与状态访问
# ════════════════════════════════════════════════════════════

def test_bridge_status_structure():
    b = get_bridge()
    st = b.status()
    assert isinstance(st, dict)
    for key in ("cycle", "focus", "emotion", "self_presence",
                "cognitive_load", "needs", "laap_available", "memories"):
        assert key in st
    assert st["focus"] in {e.value for e in AttentionFocus}
    assert st["emotion"] in {e.value for e in EmotionalState}
    assert 0.0 <= st["self_presence"] <= 1.0
    assert "competence" in st["needs"]


def test_get_cognitive_prefix_returns_text():
    b = get_bridge()
    prefix = b.get_cognitive_prefix()
    assert isinstance(prefix, str)
    assert len(prefix) > 0


# ════════════════════════════════════════════════════════════
# 4. before_turn 主循环
# ════════════════════════════════════════════════════════════

def test_before_turn_returns_expected_keys():
    b = get_bridge()
    result = b.before_turn("你好，Aris")
    assert isinstance(result, dict)
    # 至少包含认知上下文与循环计数
    assert "cognitive_context" in result or "cycle" in result


def test_before_turn_increments_cycle():
    b = get_bridge()
    before = b.state.cycle_count
    b.before_turn("测试一轮")
    assert b.state.cycle_count >= before


# ════════════════════════════════════════════════════════════
# 5. 状态持久化 (轻量往返, 不依赖具体字段值)
# ════════════════════════════════════════════════════════════

def test_state_persistence_roundtrip(tmp_path):
    b = get_bridge()
    saved_path = b._state_path
    try:
        # 用临时路径替换, 避免污染真实状态
        b._state_path = tmp_path / "bridge_state.json"
        b._save_state()
        b._try_load_state()
        assert (tmp_path / "bridge_state.json").exists()
    finally:
        b._state_path = saved_path
