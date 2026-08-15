"""
paper_trading 接入 Aris Phase 2 测试 (方案 v2.0 §4.3)
=====================================================
覆盖 Phase 2 使用接入:
  1. 动作工具注册 (pt_decide/pt_execute/pt_close) + 零悬空
  2. extract_intent 参数提取 (symbol/action/qty/confirm_word)
  3. use_decide 审核 (建议不下单) / use_execute 二次确认门 / use_close
  4. fail-closed: 无确认词拒绝 / auto_execute 默认 false

运行:
    python -m pytest tests/test_quant_bridge_phase2.py -v
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


# ════════════════════════════════════════════════════════════
# 1. 动作工具注册 + 零悬空
# ════════════════════════════════════════════════════════════

def test_action_tools_registered(registry):
    for name in ("pt_decide", "pt_execute", "pt_close"):
        assert name in registry.list(), f"{name} 未注册"


def test_phase2_rules_present():
    names = {r.name for r in DEFAULT_RULES}
    for rule in ("pt_decide_rule", "pt_execute_rule", "pt_close_rule"):
        assert rule in names, f"{rule} 缺失"


def test_phase2_zero_dangling(registry):
    missing = [s.tool for r in DEFAULT_RULES for s in r.steps if s.tool not in registry.list()]
    assert missing == [], f"悬空工具: {missing}"


# ════════════════════════════════════════════════════════════
# 2. extract_intent 参数提取
# ════════════════════════════════════════════════════════════

@pytest.fixture()
def engine():
    from aris_brain.aris_rules_engine import get_engine
    return get_engine()


def test_extract_symbol_from_chinese_context(engine):
    """中文上下文中提取 6 位股票代码 (无空格边界)。"""
    intent = engine.extract_intent("帮我看下600519要不要买")
    assert intent["params"].get("symbol") == "600519"


def test_extract_action_buy():
    from aris_brain.aris_rules_engine import get_engine
    intent = get_engine().extract_intent("五粮液值得买吗")
    assert intent["params"].get("action") == "buy"


def test_extract_qty_and_confirm():
    from aris_brain.aris_rules_engine import get_engine
    intent = get_engine().extract_intent("确认执行 买入 600519 100股")
    assert intent["params"].get("symbol") == "600519"
    assert intent["params"].get("action") == "buy"
    assert intent["params"].get("qty") == 100
    assert intent["params"].get("confirm_word") == "确认执行"


# ════════════════════════════════════════════════════════════
# 3. quant_bridge 审核与确认门
# ════════════════════════════════════════════════════════════

def test_use_decide_never_executes(monkeypatch):
    """use_decide 只给建议, 永不下单。"""
    from laap.paper_trading.quant_bridge import get_bridge
    b = get_bridge()
    # 隔离 TradingSelf: 返回 approve
    class _FakeTS:
        def judge(self, *a, **k):
            return {"verdict": "approve", "meaning": "test", "benefit": "test", "reasons": []}
    monkeypatch.setattr(b, "_get_trading_self", lambda cls: _FakeTS())
    r = b.use_decide("600519", "buy", 100)
    assert r["decision"] == "approve"
    assert r["executed"] is False
    assert r["auto_execute"] is False


def test_use_execute_needs_confirmation(monkeypatch):
    """无确认词 → need_confirmation (fail-closed)。"""
    from laap.paper_trading.quant_bridge import get_bridge
    b = get_bridge()
    r = b.use_execute(symbol="600519", action="buy", qty=100, confirm_word="")
    assert r["status"] == "need_confirmation"
    assert r["executed"] is False


def test_use_execute_judge_blocked(monkeypatch):
    """确认词给了但 judge 非 approve → 拒绝。"""
    from laap.paper_trading.quant_bridge import get_bridge
    b = get_bridge()

    class _FakeTS:
        def judge(self, *a, **k):
            return {"verdict": "reject", "meaning": "", "benefit": "",
                    "reasons": ["风险过高"]}
    monkeypatch.setattr(b, "_get_trading_self", lambda cls: _FakeTS())
    r = b.use_execute(symbol="600519", action="buy", qty=100, confirm_word="确认执行")
    assert r["status"] == "judge_blocked"
    assert r["executed"] is False


def test_use_close_needs_confirmation():
    """平仓无确认词 → 拒绝。"""
    from laap.paper_trading.quant_bridge import get_bridge
    b = get_bridge()
    r = b.use_close("600519", 100, confirm_word="")
    assert r["status"] == "need_confirmation"
    assert r["executed"] is False


# ════════════════════════════════════════════════════════════
# 4. 规则触发端到端
# ════════════════════════════════════════════════════════════

def test_rule_decide_trigger(engine):
    r = engine.process("帮我看下600519要不要买")
    assert r.get("rule") == "pt_decide_rule"
    assert r.get("matched") is True


def test_rule_close_trigger_no_confirm(engine):
    """平仓无确认词 → 输出含"需要明确确认词" (fail-closed 透传)。"""
    r = engine.process("平仓 600519")
    assert r.get("rule") == "pt_close_rule"
    assert "确认" in str(r.get("output"))
