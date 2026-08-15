"""params→code 收敛测试：搜索成果落回 strategy.py 走 M4 受限递归治理。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.param_extractor import (
    extract_strategy_params,
    params_to_code,
    serialize_params,
)
from laap.paper_trading.backtest_runner import BacktestRunner


def _full_params(overrides=None):
    from laap.paper_trading.strategy import STRATEGY_PARAMS
    p = dict(STRATEGY_PARAMS)
    if overrides:
        p.update(overrides)
    return p


class _AnalyzerStub:
    analyzed_files: set = set()
    targets_found: list = []


@pytest.fixture()
def qengine(monkeypatch, tmp_path):
    """tmp_path 下真实 strategy.py + 桩 CodeEvolutionEngine + QuantEvolutionEngine。"""
    from laap.agi.code_evolution import CodeEvolutionEngine as CEE
    from laap.agi.code_evolution import GitIntegrator, PatchGenerator, SandboxTester
    from laap.agi.evolution_audit import EvolutionAuditLog
    from laap.paper_trading.quant_evolution import QuantEvolutionEngine

    # 在 tmp_path 建真实 strategy.py（简化但含 STRATEGY_PARAMS）
    strat_dir = tmp_path / "laap" / "paper_trading"
    strat_dir.mkdir(parents=True)
    (strat_dir / "strategy.py").write_text(
        serialize_params(_full_params()), encoding="utf-8")

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

    prices = [100.0 + i * 0.5 for i in range(120)]
    qe = QuantEvolutionEngine(e, BacktestRunner(), prices, db=None).attach()
    return qe, e, tmp_path


# ════════════════════════════════════════════════════════════
# 往返契约：serialize/params_to_code → extract
# ════════════════════════════════════════════════════════════

def test_serialize_params_roundtrip():
    params = _full_params({"fast_ma": 6, "rsi_overbought": 75.0})
    code = "HEADER\n" + serialize_params(params) + "\n"
    assert extract_strategy_params(code) == params


def test_params_to_code_replaces_existing():
    old = "HEADER\n" + serialize_params(_full_params()) + "\n"
    new_params = _full_params({"fast_ma": 3, "slow_ma": 30})
    new = params_to_code(old, new_params)
    # 保留 HEADER，替换 STRATEGY_PARAMS
    assert "HEADER" in new
    assert extract_strategy_params(new) == new_params


def test_params_to_code_appends_when_missing():
    old = "x = 1\n"
    new = params_to_code(old, _full_params())
    assert "STRATEGY_PARAMS" in new
    assert extract_strategy_params(new) == _full_params()


# ════════════════════════════════════════════════════════════
# apply_params_to_code：M4 治理流程
# ════════════════════════════════════════════════════════════

def test_apply_params_to_code_awaiting_approval(qengine, monkeypatch):
    """deploy_gate 通过 → awaiting_approval + mutation 进 history。"""
    qe, engine, _ = qengine
    monkeypatch.setattr(engine, "deploy_gate", lambda m, e: (True, "oos not degraded"))
    r = qe.apply_params_to_code(_full_params({"fast_ma": 6}), rationale="t1")
    assert r["status"] == "awaiting_approval"
    assert r["mutation_id"]
    assert any(m.id == r["mutation_id"] for m in engine.mutations)
    # mutation 未自动部署（approved=False）
    m = next(x for x in engine.mutations if x.id == r["mutation_id"])
    assert m.approved is False


def test_apply_params_to_code_gate_blocked(qengine, monkeypatch):
    """OOS 门禁拒绝 → gate_blocked，不产生待审批项。"""
    qe, engine, _ = qengine
    monkeypatch.setattr(engine, "deploy_gate", lambda m, e: (False, "oos degraded"))
    r = qe.apply_params_to_code(_full_params({"fast_ma": 6}))
    assert r["status"] == "gate_blocked"
    assert "oos" in r["reason"]
    assert not any(m.id == r.get("mutation_id", "") for m in engine.mutations)


def test_apply_params_to_code_no_change(qengine):
    """参数与当前一致 → no_change。"""
    qe, _, _ = qengine
    r = qe.apply_params_to_code(_full_params())
    assert r["status"] == "no_change"


def test_apply_params_to_code_sandbox_rejects_bad_code(qengine, monkeypatch):
    """沙箱测试失败（构造语法损坏参数序列化不可达）→ test_failed。"""
    qe, engine, tmp = qengine
    monkeypatch.setattr(engine, "deploy_gate", lambda m, e: (True, "ok"))
    # 直接塞一个 mutated_code 语法错误的 mutation 到流程：绕过 params_to_code
    from laap.agi.code_evolution import CodeMutation, CodeTarget, MutationStatus
    m = CodeMutation(
        id="bad", target=CodeTarget(file_path="laap/paper_trading/strategy.py"),
        original_code=(tmp / "laap" / "paper_trading" / "strategy.py").read_text(),
        mutated_code="STRATEGY_PARAMS = {",
        status=MutationStatus.DRAFT,
    )
    # 手动走 SafetyGuard + 沙箱（应失败）
    from laap.agi.code_evolution import SafetyGuard
    ok, _ = SafetyGuard.validate_mutation(m)
    if ok:
        test = engine.tester.test_mutation(m, engine.repo_root, None)
        assert test["success"] is False  # 语法损坏必失败


def test_apply_params_to_code_approve_deploys_updates_file(qengine, monkeypatch):
    """awaiting_approval → 人工批准 → 部署 → strategy.py 文件被更新。"""
    qe, engine, tmp = qengine
    monkeypatch.setattr(engine, "deploy_gate", lambda m, e: (True, "oos not degraded"))
    r = qe.apply_params_to_code(_full_params({"fast_ma": 9}), rationale="approve-t")
    assert r["status"] == "awaiting_approval"

    # 真实 git.deploy（tmp 下无 git 仓库可能失败）→ 用 stub 写文件
    from laap.agi.code_evolution import CodeMutation
    m = next(x for x in engine.mutations if x.id == r["mutation_id"])
    assert isinstance(m, CodeMutation)

    def _fake_deploy(mut):
        (tmp / "laap" / "paper_trading" / "strategy.py").write_text(
            mut.mutated_code, encoding="utf-8")
        return True, "deadbeef"
    monkeypatch.setattr(engine.git, "deploy", _fake_deploy)

    ar = qe.approve_and_deploy(r["mutation_id"], approver="pytest")
    assert ar["status"] == "deployed"
    # strategy.py 已更新，fast_ma=9
    updated = (tmp / "laap" / "paper_trading" / "strategy.py").read_text()
    assert extract_strategy_params(updated)["fast_ma"] == 9


# ════════════════════════════════════════════════════════════
# TradingSelf 审核（judge_proposal + self_review）
# ════════════════════════════════════════════════════════════

def _trading_self():
    from laap.paper_trading.trading_self import TradingSelf
    from laap.agi.self_model import EmergentSelfModel
    from laap.agi.unified_memory import UnifiedMemory
    return TradingSelf(personality={}, self_model=EmergentSelfModel("T"),
                       memory=UnifiedMemory())


def test_judge_proposal_approve():
    ts = _trading_self()
    d = ts.judge_proposal(_full_params({"position_scale": 0.3}),
                          oos_metrics={"cumulative_return": 0.05,
                                       "sharpe_ratio": 0.8, "score": 0.6})
    assert d["verdict"] == "approve"
    assert "meaning" in d and d["meaning"]
    assert "benefit" in d


def test_judge_proposal_reject_oos_negative():
    ts = _trading_self()
    d = ts.judge_proposal(_full_params(),
                          oos_metrics={"cumulative_return": -0.05,
                                       "sharpe_ratio": -0.3, "score": 0.1})
    assert d["verdict"] == "reject"
    assert any("OOS" in r for r in d["reasons"])


def test_judge_proposal_position_over_personality_limit():
    ts = _trading_self()
    # 空 traits → risk_appetite=0.6 → pos_max≈0.66；0.9 超限
    d = ts.judge_proposal(_full_params({"position_scale": 0.9}),
                          oos_metrics={"cumulative_return": 0.05,
                                       "sharpe_ratio": 0.8, "score": 0.6})
    assert d["verdict"] == "abstain"  # OOS 正但人格顾虑
    assert any("position_scale" in r for r in d["reasons"])


def test_apply_params_to_code_self_review_blocks(qengine, monkeypatch):
    """self_review=True：自我审核拒绝（OOS 负）→ self_blocked。"""
    qe, engine, _ = qengine
    qe.trading_self = _trading_self()
    monkeypatch.setattr(engine, "deploy_gate", lambda m, e: (True, "oos ok"))
    monkeypatch.setattr(qe, "_oos_metrics",
                        lambda p: {"cumulative_return": -0.05,
                                   "sharpe_ratio": -0.3, "score": 0.1})
    r = qe.apply_params_to_code(_full_params({"fast_ma": 6}), rationale="sr")
    assert r["status"] == "self_blocked"
    assert r["self_verdict"] == "reject"
    assert not any(m.id == r.get("mutation_id", "") for m in engine.mutations)


def test_apply_params_to_code_self_review_disabled(qengine, monkeypatch):
    """self_review=False → 仅 OOS 门禁，走 awaiting_approval。"""
    qe, engine, _ = qengine
    qe.trading_self = _trading_self()
    monkeypatch.setattr(engine, "deploy_gate", lambda m, e: (True, "oos ok"))
    r = qe.apply_params_to_code(_full_params({"fast_ma": 6}),
                                rationale="nosr", self_review=False)
    assert r["status"] == "awaiting_approval"


# ════════════════════════════════════════════════════════════
# T4/M4: 真实 git repo 端到端（apply→approve→git.deploy→文件更新+commit）
# ════════════════════════════════════════════════════════════

def test_apply_params_to_code_real_git_e2e(tmp_path, monkeypatch):
    """真实 repo（git init）全链路：apply→approve→git.deploy→strategy.py 更新+commit。"""
    import subprocess
    import threading as _t
    repo = tmp_path
    strat_dir = repo / "laap" / "paper_trading"
    strat_dir.mkdir(parents=True)
    (strat_dir / "strategy.py").write_text(
        serialize_params(_full_params()), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@test"],
                   cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "test"],
                   cwd=str(repo), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(repo), check=True)

    from laap.agi.code_evolution import CodeEvolutionEngine as CEE
    from laap.agi.code_evolution import (GitIntegrator, PatchGenerator,
                                         SandboxTester)
    from laap.agi.evolution_audit import EvolutionAuditLog
    from laap.paper_trading.quant_evolution import QuantEvolutionEngine
    from laap.paper_trading.backtest_runner import BacktestRunner

    monkeypatch.setattr(CEE, "__init__", lambda self, repo_root="", llm_fn=None: None)
    e = object.__new__(CEE)
    e.repo_root = str(repo)
    e.mutations = []
    e.deployed_count = 0
    e.rollback_count = 0
    e.audit = EvolutionAuditLog(repo_root=str(repo))
    e.patcher = PatchGenerator(llm_generate_fn=None)
    e.tester = SandboxTester(restrict_resources=False)
    e.git = GitIntegrator(str(repo))  # 真实 git 集成器
    e.analyzer = _AnalyzerStub()
    e.created_at = 0.0
    e.qa = None
    e._lock = _t.Lock()
    e.scope_guard = None
    e.deploy_gate = None

    prices = [100.0 + i * 0.5 for i in range(120)]
    qe = QuantEvolutionEngine(e, BacktestRunner(), prices, db=None).attach()
    monkeypatch.setattr(e, "deploy_gate", lambda m, eng: (True, "oos ok"))

    # apply（self_review=False 避免人格约束干扰）→ approve → 真实 git.deploy
    r = qe.apply_params_to_code(_full_params({"fast_ma": 9}),
                                rationale="real-git", self_review=False)
    assert r["status"] == "awaiting_approval"
    ar = qe.approve_and_deploy(r["mutation_id"], approver="pytest")
    assert ar["status"] == "deployed"

    # strategy.py 已更新
    updated = (repo / "laap" / "paper_trading" / "strategy.py").read_text()
    assert extract_strategy_params(updated)["fast_ma"] == 9
    # git 产生 AGI commit
    log = subprocess.run(["git", "log", "--oneline"], cwd=str(repo),
                         capture_output=True, text=True)
    assert "AGI:" in log.stdout
