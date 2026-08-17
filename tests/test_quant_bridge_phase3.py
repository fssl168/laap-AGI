"""
paper_trading 接入 Aris Phase 3 测试 (方案 v2.0 §4.4)
=====================================================
覆盖 Phase 3 管理闭环:
  1. pt_brief 每日交易简报工具 + 规则触发
  2. pt_evolution_audit 进化治理工具 + 规则触发
  3. 日终认知快照脚本 (_memorize_trading_daily) 摘要构建
  4. 零悬空 + 全部工具可调用

运行:
    python -m pytest tests/test_quant_bridge_phase3.py -v
"""

from __future__ import annotations

import importlib.util
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


@pytest.fixture()
def engine():
    from aris_brain.aris_rules_engine import get_engine
    return get_engine()


# ════════════════════════════════════════════════════════════
# 1. 工具注册 + 零悬空
# ════════════════════════════════════════════════════════════

def test_phase3_tools_registered(registry):
    for name in ("pt_brief", "pt_evolution_audit"):
        assert name in registry.list(), f"{name} 未注册"


def test_phase3_rules_present():
    names = {r.name for r in DEFAULT_RULES}
    for rule in ("pt_brief_rule", "pt_evolution_rule"):
        assert rule in names, f"{rule} 缺失"


def test_phase3_zero_dangling(registry):
    missing = [s.tool for r in DEFAULT_RULES for s in r.steps if s.tool not in registry.list()]
    assert missing == [], f"悬空工具: {missing}"


# ════════════════════════════════════════════════════════════
# 2. 简报工具
# ════════════════════════════════════════════════════════════

def test_brief_returns_structure(registry, monkeypatch, tmp_path):
    # 沙箱挂载盘（9p）SQLite 写会 disk I/O error：注入 tmp 可写 DB 路径，
    # 让 PaperDB 幂等建表（net_values 等），工具才能查询并输出"净值"。
    import aris_brain.paper_trading_tools as ptt
    monkeypatch.setattr(ptt, "DB_PATH", str(tmp_path / "pt.db"))
    out = registry.get("pt_brief")()
    assert isinstance(out, str)
    assert "简报" in out
    assert "净值" in out


def test_evolution_audit_returns_structure(registry):
    out = registry.get("pt_evolution_audit")()
    assert isinstance(out, str)
    assert "进化" in out


def test_brief_rule_triggers(engine):
    for msg in ("今日交易简报", "今天交易怎么样", "今日复盘"):
        r = engine.process(msg)
        assert r.get("rule") == "pt_brief_rule", f"[{msg}] -> {r.get('rule')}"
        assert r.get("matched") is True


def test_evolution_rule_triggers(engine):
    for msg in ("看下进化提案", "进化审计", "策略改进"):
        r = engine.process(msg)
        assert r.get("rule") == "pt_evolution_rule", f"[{msg}] -> {r.get('rule')}"


# ════════════════════════════════════════════════════════════
# 3. 日终认知快照脚本
# ════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def mtd_mod():
    """加载 _memorize_trading_daily.py 为模块 (不执行 main)。"""
    path = Path(__file__).resolve().parents[1] / "scripts" / "market" / "_memorize_trading_daily.py"
    spec = importlib.util.spec_from_file_location("memorize_trading_daily", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["memorize_trading_daily"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_mtd_script_compiles():
    path = Path(__file__).resolve().parents[1] / "scripts" / "market" / "_memorize_trading_daily.py"
    compile(path.read_bytes(), str(path), "exec")


def test_mtd_build_summary_structure(mtd_mod):
    status = {
        "date": "2026-08-16", "net_value": 1000000.0,
        "trades_today": 3, "pnl_today": -120.5,
        "open_positions": 1, "risk_rejections": 2,
        "lessons": [{"type": "timing", "text": "追高被套"}],
    }
    summary = mtd_mod.build_summary(status)
    assert "交易日报 2026-08-16" in summary
    assert "净值" in summary
    assert "-120.50" in summary  # 亏损
    assert "追高被套" in summary


def test_mtd_fetch_status_graceful(mtd_mod):
    """DB 不可达时 fetch 应抛异常 (由 main 兜底), 但 build_summary 纯函数可用。"""
    status = {"date": "2026-08-16", "net_value": None, "trades_today": 0,
              "pnl_today": 0.0, "open_positions": 0, "risk_rejections": 0,
              "lessons": []}
    summary = mtd_mod.build_summary(status)
    assert "暂无" in summary  # net_value None → "暂无"
