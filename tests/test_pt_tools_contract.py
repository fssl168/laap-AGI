"""
paper_trading 工具契约测试 (前置任务: pt_* 悬空清理)
====================================================
验证规则引擎所有规则引用的工具均已注册 (零悬空), 并覆盖
修复的 3 个 pt_* 命名不一致工具的功能冒烟。

背景: rules_defs.py 的 8 条 pt_* 规则中, pt_account_show /
pt_account_positions / pt_strategy_list 曾悬空 (注册表命名
不一致: pt_positions/pt_strategies 且缺 pt_account_show)。
本测试守护"规则引用 = 注册表"契约, 防命名漂移回归。

运行:
    python -m pytest tests/test_pt_tools_contract.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from aris_brain.rules_defs import DEFAULT_RULES, ToolRegistry
from aris_brain.rules_tools import register_default_tools


@pytest.fixture(scope="module")
def registry():
    reg = ToolRegistry()
    register_default_tools(reg)
    return reg


def test_zero_dangling_references(registry):
    """契约: 每条规则引用的工具必须已注册 (零悬空)。"""
    missing = []
    for rule in DEFAULT_RULES:
        for step in rule.steps:
            if step.tool not in registry.list():
                missing.append(f"{rule.name}->{step.tool}")
    assert missing == [], f"悬空工具引用: {missing}"


def test_pt_rules_present():
    """8 条 pt_* 规则应存在。"""
    pt_rules = [r.name for r in DEFAULT_RULES if r.name.startswith("pt_")]
    assert "pt_account_list" in pt_rules
    assert "pt_backtest_run" in pt_rules
    assert "pt_risk_check" in pt_rules
    assert "pt_performance" in pt_rules
    assert "pt_health" in pt_rules


def test_fixed_dangling_tools_registered(registry):
    """修复的 3 个工具名必须注册。"""
    for name in ("pt_account_show", "pt_account_positions", "pt_strategy_list"):
        assert name in registry.list(), f"{name} 未注册"


def test_aliases_keep_old_names(registry):
    """旧名保留 (pt_positions/pt_strategies) 不破坏潜在引用。"""
    assert "pt_positions" in registry.list()
    assert "pt_strategies" in registry.list()


def test_pt_tools_are_callable(registry):
    """3 个修复工具可调用 (返回 str, 不抛异常)。"""
    for name in ("pt_account_show", "pt_account_positions", "pt_strategy_list"):
        fn = registry.get(name)
        result = fn()
        assert isinstance(result, str)
        assert len(result) > 0
