"""
V5 Upgrade Engine 冒烟测试 (R11 拆分前置覆盖)
==============================================
为无测试覆盖的 laap/agi/v5_upgrade.py 提供基础回归保障,
覆盖: 单例 / 引擎核心方法 / 记忆缓冲 / 安全扫描 / 基准套件。

运行:
    python -m pytest tests/test_v5_upgrade.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.agi.v5_upgrade import (
    V5UpgradeEngine,
    get_v5_engine,
    V5_VERSION,
    SumTree,
    PrioritizedExperienceBuffer,
    FormalVerifier,
    SecureSandboxScanner,
    BenchmarkSuite,
)


# ════════════════════════════════════════════════════════════
# 1. 单例与版本
# ════════════════════════════════════════════════════════════

def test_get_v5_engine_singleton():
    assert get_v5_engine() is get_v5_engine()


def test_engine_version():
    e = get_v5_engine()
    assert e.version == V5_VERSION == "5.0.0"


# ════════════════════════════════════════════════════════════
# 2. 引擎核心方法
# ════════════════════════════════════════════════════════════

def test_discover_causality_returns_dict():
    e = get_v5_engine()
    r = e.discover_causality({"A": [1, 2, 3, 4], "B": [2, 4, 6, 8]})
    assert isinstance(r, dict)


def test_engine_has_core_components():
    e = get_v5_engine()
    for attr in ("ewc", "experience_buffer", "causal_discovery",
                 "bug_classifier", "goal_creator", "mcts_planner",
                 "verifier", "sandbox"):
        assert hasattr(e, attr), f"missing {attr}"


# ════════════════════════════════════════════════════════════
# 3. 记忆/学习组件
# ════════════════════════════════════════════════════════════

def test_sum_tree_basic():
    st = SumTree(capacity=4)
    st.add(1.0, "a")
    st.add(2.0, "b")
    assert st.get_min_idx() == 0
    assert st.get(0) == 1.0


def test_per_buffer_roundtrip():
    buf = PrioritizedExperienceBuffer(capacity=8)
    for i in range(4):
        buf.add(f"state{i}", "act", 1.0)
    batch, indices = buf.sample(batch_size=2)
    assert isinstance(batch, list)
    assert len(batch) == 2
    assert len(indices) == 2


# ════════════════════════════════════════════════════════════
# 4. 质量/安全组件
# ════════════════════════════════════════════════════════════

def test_formal_verifier_instantiates():
    assert FormalVerifier() is not None


def test_sandbox_scanner_instantiates():
    assert SecureSandboxScanner() is not None


def test_benchmark_suite_instantiates():
    suite = BenchmarkSuite()
    assert suite is not None
