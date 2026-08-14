"""
M4 True RSI 受限递归引擎测试
============================
功能验证四道约束 (docs/true-rsi-feasibility.md §3 M4):
  1. 作用域限定 — 只允许 laap/agi/ 下非核心、非安全文件
  2. 永久只读   — 进化安全基座 + 核心/安全文件任何深度不可改
  3. 递归深度<=1 — 只允许"改进改进者"一层; 禁止"改进者的改进者"
  4. 不自动部署 — 提案走 M3 授权 API, 不自动落地

运行:
    python -m pytest tests/test_true_rsi.py -v
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.agi.code_evolution import (
    CodeMutation,
    CodeTarget,
    MutationStatus,
)
from laap.evolution.true_rsi import TrueRSIEngine


class _AnalyzerStub:
    """engine.stats() 需要的最小 analyzer 假件。"""
    analyzed_files: set = set()
    targets_found: list = []


@pytest.fixture()
def engine(monkeypatch, tmp_path):
    """轻量 CodeEvolutionEngine, 注入真实 audit + 桩 git/patcher/tester。"""
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
    e.created_at = time.time()
    e.qa = None
    e._lock = threading.Lock()
    e.scope_guard = None
    return e


def make_target(path: str) -> CodeTarget:
    return CodeTarget(file_path=path, function_name="f",
                      current_code="def f():\n    return 1\n")


def make_mutation(target: CodeTarget = None,
                  status: MutationStatus = MutationStatus.TEST_PASSED) -> CodeMutation:
    t = target or make_target("laap/agi/foo.py")
    return CodeMutation(
        id="mut_t1",
        target=t,
        original_code="def f():\n    return 1\n",
        mutated_code="def f():\n    return 2\n",
        unified_diff="--- a\n+++ b\n@@ -1 +1 @@\n",
        status=status,
    )


class _PatchingPatcher:
    """返回 test_passed mutation 的桩 patcher。"""
    def __init__(self, target=None):
        self._target = target

    def generate_patch(self, target):
        return make_mutation(target)


class _PassTester:
    """测试总通过的桩 tester。"""
    def test_mutation(self, *a, **k):
        return {"success": True, "execution_time_ms": 10, "errors": ""}


class _FailClosedPatcher:
    """被调用即失败的桩 patcher — 证明被拒目标不会走到补丁生成。"""
    def generate_patch(self, target):
        raise AssertionError(
            f"patch must not be generated for rejected target: {target.file_path}")


# ════════════════════════════════════════════════════════════
# 0. 接线回归: 裸引擎不受影响 / TrueRSI 注入守卫
# ════════════════════════════════════════════════════════════

def test_scope_guard_none_by_default(engine):
    """未挂载 TrueRSIEngine 时 scope_guard 为 None → M1-M3 行为不变。"""
    assert engine.scope_guard is None


def test_guard_wired_by_true_rsi(engine):
    """TrueRSIEngine 构造即注入守卫。"""
    trsi = TrueRSIEngine(engine)
    assert engine.scope_guard is not None
    assert trsi.MAX_RECURSION_DEPTH == 1


# ════════════════════════════════════════════════════════════
# 1. 永久只读: 安全基座 + 核心/安全文件 (任何深度拒绝)
# ════════════════════════════════════════════════════════════

@pytest.mark.parametrize("path", [
    # 进化安全基座 (M1 起固化)
    "laap/agi/code_evolution.py",
    "laap/agi/evolution_system.py",
    "laap/agi/rsi_engine.py",
    # M2/M3 治理模块
    "laap/agi/evolution_scheduler.py",
    "laap/agi/evolution_audit.py",
    "laap/agi/fitness.py",
    # 核心/安全文件
    "laap/agi/core.py",
    "laap/agi/safety.py",
    "laap/agi/security_system.py",
    "laap/agi/__init__.py",
])
def test_permanent_readonly_rejected(engine, path):
    """永久只读清单内文件 → rejected, 且补丁不被生成 (fail-closed)。"""
    trsi = TrueRSIEngine(engine)
    engine.patcher = _FailClosedPatcher()
    result = engine._improve_single(make_target(path), None, False)
    assert result["status"] == "rejected"
    assert "M4 scope" in result["reason"]
    assert "permanent read-only" in result["reason"]


# ════════════════════════════════════════════════════════════
# 2. 作用域限定: laap/agi/ 之外拒绝
# ════════════════════════════════════════════════════════════

@pytest.mark.parametrize("path", [
    "laap_brain/api.py",
    "psi_core/psi.py",
    "aris_brain/agi_kernel.py",
    "laap/agi/../rsi_engine.py",   # 目录穿越试探 (base 命中只读清单仍拦截)
])
def test_out_of_scope_rejected(engine, path):
    """作用域外文件 → rejected。"""
    trsi = TrueRSIEngine(engine)
    engine.patcher = _FailClosedPatcher()
    result = engine._improve_single(make_target(path), None, False)
    assert result["status"] == "rejected"
    assert "M4 scope" in result["reason"]
    assert ("out of scope" in result["reason"]
            or "permanent read-only" in result["reason"])


# ════════════════════════════════════════════════════════════
# 3. 业务代码允许改进 (深度 0)
# ════════════════════════════════════════════════════════════

def test_business_code_allowed(engine):
    """laap/agi/ 下业务文件可通过守卫, 走到沙箱测试。"""
    trsi = TrueRSIEngine(engine)
    engine.patcher = _PatchingPatcher()
    engine.tester = _PassTester()
    result = engine._improve_single(make_target("laap/agi/tool_router.py"), None, False)
    assert result["status"] == "test_passed"
    assert result["deployed"] is False


# ════════════════════════════════════════════════════════════
# 4. 递归深度 <= 1: 改进者自身仅允许一层
# ════════════════════════════════════════════════════════════

def test_self_improve_depth0_allowed(engine):
    """深度0: true_rsi.py (改进者自身) 允许 — 第一层递归。"""
    trsi = TrueRSIEngine(engine)
    engine.patcher = _PatchingPatcher()
    engine.tester = _PassTester()
    result = engine._improve_single(make_target(trsi.SELF_FILE), None, False)
    assert result["status"] == "test_passed"


def test_self_improve_depth1_rejected(engine):
    """深度1: true_rsi.py 再次作为目标 → 拒绝 (禁止改进者的改进者)。"""
    trsi = TrueRSIEngine(engine)
    engine.patcher = _FailClosedPatcher()
    result = engine._improve_single(make_target(trsi.SELF_FILE), None, False, depth=1)
    assert result["status"] == "rejected"
    assert "recursion depth exceeded" in result["reason"]


def test_recursion_quota_exhausted_after_self_improve(engine):
    """集成: 改进者自身改进 test_passed → 递归配额耗尽 → 后续自我改进被拒。"""
    trsi = TrueRSIEngine(engine)

    # 第一轮: 模拟 auto_improve 产出 true_rsi.py 的 test_passed 提案
    engine.auto_improve = lambda **kw: [
        {"target": trsi.SELF_FILE, "status": "test_passed"}]
    trsi.improve()
    assert trsi.stats()["recursion_depth"] == 1
    assert trsi.stats()["recursion_quota_exhausted"] is True

    # 第二轮: 配额耗尽后, 深度1 下再改 true_rsi.py → 拒绝
    ok, reason = trsi._guard(make_target(trsi.SELF_FILE), trsi._recursion_depth)
    assert ok is False
    assert "recursion depth exceeded" in reason


def test_self_improve_failed_does_not_consume_quota(engine):
    """改进者自身的改进失败 → 配额不消耗, 仍可重试。"""
    trsi = TrueRSIEngine(engine)
    engine.auto_improve = lambda **kw: [
        {"target": trsi.SELF_FILE, "status": "test_failed"}]
    trsi.improve()
    assert trsi.stats()["recursion_depth"] == 0


def test_approve_and_deploy_self_consumes_quota(engine):
    """人工批准部署 true_rsi.py 的提案 → 同步消耗递归配额。"""
    trsi = TrueRSIEngine(engine)
    m = make_mutation(make_target(trsi.SELF_FILE))
    engine.mutations.append(m)

    class _G:
        def deploy(self, m):
            return True, "deadbeef"
    engine.git = _G()

    result = trsi.approve_and_deploy("mut_t1", approver="pytest")
    assert result["status"] == "deployed"
    assert trsi.stats()["recursion_depth"] == 1


# ════════════════════════════════════════════════════════════
# 5. 治理: 被拒审计落库 + stats
# ════════════════════════════════════════════════════════════

def test_rejected_mutation_audited(engine):
    """被拒目标写入审计 (rejected + M4 scope + 目标路径)。"""
    trsi = TrueRSIEngine(engine)
    engine._improve_single(make_target("laap_brain/api.py"), None, False)

    rejected = [en for en in engine.audit.query()
                if en.get("decision") == "rejected"]
    assert any("M4 scope" in (en.get("reason") or "") for en in rejected)
    assert any(en.get("target") == "laap_brain/api.py" for en in rejected)


def test_stats_structure(engine):
    """M4 引擎统计结构完整。"""
    trsi = TrueRSIEngine(engine)
    st = trsi.stats()
    assert st["mode"] == "M4-restricted-recursion"
    assert st["max_recursion_depth"] == 1
    assert "recursion_depth" in st
    assert st["scope"]["allowed_dirs"] == ["laap/agi/", "laap/paper_trading/"]
    assert "laap/evolution/true_rsi.py" in st["scope"]["self_file"]
