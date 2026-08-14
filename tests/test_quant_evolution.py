"""P2 代码级受限递归编排测试（双守卫 / evolve / 批准 / 审计）。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.backtest_runner import BacktestRunner
from laap.paper_trading.quant_evolution import (
    QuantScopeGuard,
    QuantEvolutionGate,
    QuantEvolutionEngine,
)
from laap.agi.code_evolution import CodeTarget


class _AnalyzerStub:
    analyzed_files: set = set()
    targets_found: list = []


@pytest.fixture()
def engine(monkeypatch, tmp_path):
    """轻量 CodeEvolutionEngine（同 test_true_rsi 模式）。"""
    from laap.agi.code_evolution import CodeEvolutionEngine as CEE
    from laap.agi.code_evolution import GitIntegrator, PatchGenerator, SandboxTester
    from laap.agi.evolution_audit import EvolutionAuditLog

    monkeypatch.setattr(CEE, "__init__", lambda self, repo_root="", llm_fn=None: None)
    e = object.__new__(CEE)
    e.repo_root = str(tmp_path)
    e.mutations = []
    e.deployed_count = 0
    e.rollback_count = 0
    e.audit = EvolutionAuditLog(repo_root=str(tmp_path))
    e.patcher = PatchGenerator(llm_generate_fn=None)
    e.tester = SandboxTester(restrict_resources=False)
    e.git = GitIntegrator(str(tmp_path))
    e.analyzer = _AnalyzerStub()
    e.created_at = 0.0
    e.qa = None
    e._lock = threading.Lock()
    e.scope_guard = None
    e.deploy_gate = None
    return e


def _target(path):
    return CodeTarget(file_path=path, function_name="f",
                      current_code="def f():\n    return 1\n")


# ════════════════════════════════════════════════════════════
# QuantScopeGuard
# ════════════════════════════════════════════════════════════

def test_scope_guard_allows_paper_trading():
    g = QuantScopeGuard()
    ok, _ = g(_target("laap/paper_trading/strategy.py"))
    assert ok is True


def test_scope_guard_rejects_safety_base():
    g = QuantScopeGuard()
    ok, reason = g(_target("laap/agi/code_evolution.py"))
    assert ok is False
    assert "permanent read-only" in reason


def test_scope_guard_rejects_out_of_scope():
    g = QuantScopeGuard()
    ok, reason = g(_target("laap/agi/foo.py"))
    assert ok is False
    assert "out of scope" in reason


# ════════════════════════════════════════════════════════════
# QuantEvolutionGate
# ════════════════════════════════════════════════════════════

def test_gate_skips_non_paper_trading_target():
    from laap.agi.code_evolution import CodeMutation
    gate = QuantEvolutionGate(BacktestRunner(), [100.0] * 60)
    m = CodeMutation(id="m1", target=_target("laap/agi/foo.py"))
    ok, _ = gate(m, None)
    assert ok is True  # 非交易代码不受门禁


def test_gate_blocks_on_short_series():
    from laap.agi.code_evolution import CodeMutation
    gate = QuantEvolutionGate(BacktestRunner(), [100.0, 101.0])
    m = CodeMutation(id="m2", target=_target("laap/paper_trading/strategy.py"))
    ok, reason = gate(m, None)
    assert ok is False
    assert "too short" in reason


# ════════════════════════════════════════════════════════════
# QuantEvolutionEngine
# ════════════════════════════════════════════════════════════

def test_attach_wires_both_guards(engine):
    runner = BacktestRunner()
    qe = QuantEvolutionEngine(engine, runner, [100.0 + i for i in range(60)])
    qe.attach()
    assert engine.scope_guard is not None
    assert engine.deploy_gate is not None


def test_evolve_calls_auto_improve_with_auto_deploy_false(engine, monkeypatch):
    captured = {}
    monkeypatch.setattr(engine, "auto_improve",
                        lambda **kw: captured.update(kw) or [{"status": "test_passed"}])
    qe = QuantEvolutionEngine(engine, BacktestRunner(), [100.0] * 60)
    qe.evolve(max_mutations=1)
    assert captured["directory"] == "laap/paper_trading/"
    assert captured["auto_deploy"] is False


def test_approve_and_deploy_wraps_engine(engine, monkeypatch):
    monkeypatch.setattr(engine, "approve_and_deploy",
                        lambda mid, approver="x": {"status": "deployed", "mutation_id": mid})
    qe = QuantEvolutionEngine(engine, BacktestRunner(), [100.0] * 60)
    r = qe.approve_and_deploy("mut_x", approver="pytest")
    assert r["status"] == "deployed"


def test_stats_structure(engine):
    qe = QuantEvolutionEngine(engine, BacktestRunner(), [100.0] * 60)
    st = qe.stats()
    assert st["mode"] == "quant-code-evolution"
    assert st["scope"] == "laap/paper_trading/"
