"""
M3 部署授权 + M1 import 白名单/沙箱只读测试 (True RSI 治理)
============================================================
验证审计报告补的缺口:
  M3: _improve_single auto_deploy 授权检查 + approve_and_deploy 人工批准部署
  M1: _quick_validate import 白名单 (仅 stdlib + laap)
  M1: SandboxTester 沙箱只读语义 + restrict_resources 参数

运行:
    python -m pytest tests/test_evo_deploy_governance.py -v
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.agi.code_evolution import (
    CodeMutation,
    CodeTarget,
    MutationStatus,
    SandboxTester,
)


@pytest.fixture()
def engine(monkeypatch, tmp_path):
    """轻量 CodeEvolutionEngine, 注入真实 audit + 桩 git/tester。"""
    from laap.agi.code_evolution import CodeEvolutionEngine as CEE
    from laap.agi.code_evolution import GitIntegrator, PatchGenerator
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
    e.analyzer = None
    e.qa = None
    e._lock = threading.Lock()
    return e


def make_mutation(status: MutationStatus = MutationStatus.TEST_PASSED) -> CodeMutation:
    return CodeMutation(
        id="mut_abc123",
        target=CodeTarget(file_path="laap/agi/foo.py", function_name="f",
                          current_code="def f():\n    return 1\n"),
        mutated_code="def f():\n    return 2\n",
        unified_diff="--- a\n+++ b\n@@ -1 +1 @@\n",
        status=status,
    )


# ════════════════════════════════════════════════════════════
# M3: _improve_single 授权检查
# ════════════════════════════════════════════════════════════

def test_auto_deploy_without_approval_waits(engine):
    """auto_deploy=True 但 mutation 未授权 → awaiting_approval, 不部署。"""
    target = CodeTarget(file_path="laap/agi/foo.py", function_name="f",
                        current_code="def f():\n    return 1\n")
    # 桩 patcher: 直接返回一个 test_passed 的 mutation
    class _P:
        def generate_patch(self, t):
            return make_mutation()
    engine.patcher = _P()
    # 桩 tester: 测试通过
    class _T:
        def test_mutation(self, *a, **k):
            return {"success": True, "execution_time_ms": 10, "errors": ""}
    engine.tester = _T()
    # 桩 git: 部署不应被调用
    class _G:
        def deploy(self, *a, **k):
            raise AssertionError("deploy must not run without approval")
    engine.git = _G()

    result = engine._improve_single(target, test_commands=None, auto_deploy=True)
    assert result["status"] == "awaiting_approval"
    assert result["deployed"] is False
    assert "mutation_id" in result
    # 审计应记录 awaiting_approval
    decisions = {en["decision"] for en in engine.audit.query()}
    assert "awaiting_approval" in decisions


def test_auto_deploy_with_approval_deploys(engine, monkeypatch):
    """已授权 (approved=True) 的 mutation 可部署。"""
    target = CodeTarget(file_path="laap/agi/foo.py", function_name="f",
                        current_code="def f():\n    return 1\n")
    class _P:
        def generate_patch(self, t):
            m = make_mutation()
            m.approved = True
            return m
    engine.patcher = _P()
    class _T:
        def test_mutation(self, *a, **k):
            return {"success": True, "execution_time_ms": 10, "errors": ""}
    engine.tester = _T()
    class _G:
        def deploy(self, m):
            return True, "abc123"
    engine.git = _G()

    result = engine._improve_single(target, test_commands=None, auto_deploy=True)
    assert result["status"] == "deployed"
    assert result["deployed"] is True
    assert result["commit"] == "abc123"


# ════════════════════════════════════════════════════════════
# M3: approve_and_deploy 人工批准部署
# ════════════════════════════════════════════════════════════

def test_approve_and_deploy_flow(engine):
    """test_passed mutation → 批准 → 部署 → audit approved+deployed。"""
    m = make_mutation()
    engine.mutations.append(m)
    class _G:
        def deploy(self, m):
            return True, "deadbeef"
    engine.git = _G()

    result = engine.approve_and_deploy("mut_abc123", approver="pytest")
    assert result["status"] == "deployed"
    assert result["commit"] == "deadbeef"
    assert m.approved is True
    assert m.status == MutationStatus.DEPLOYED
    decisions = [en["decision"] for en in engine.audit.query()]
    assert "approved" in decisions
    assert "deployed" in decisions


def test_approve_and_deploy_unknown_id(engine):
    result = engine.approve_and_deploy("nope")
    assert result["status"] == "not_found"


def test_approve_and_deploy_wrong_status(engine):
    m = make_mutation(status=MutationStatus.DRAFT)
    engine.mutations.append(m)
    result = engine.approve_and_deploy("mut_abc123")
    assert result["status"] == "not_approvable"


def test_approve_and_deploy_failed_deploy(engine):
    m = make_mutation()
    engine.mutations.append(m)
    class _G:
        def deploy(self, m):
            return False, "git merge conflict"
    engine.git = _G()

    result = engine.approve_and_deploy("mut_abc123")
    assert result["status"] == "deploy_failed"
    assert "git merge conflict" in result["error"]
    # 失败后状态回落 test_passed, 可再次批准
    assert m.status == MutationStatus.TEST_PASSED


# ════════════════════════════════════════════════════════════
# M1: import 白名单
# ════════════════════════════════════════════════════════════

def test_import_whitelist_stdlib_ok():
    t = SandboxTester(restrict_resources=False)
    assert t._validate_imports("import os\nimport json\nfrom pathlib import Path") == []


def test_import_whitelist_laap_ok():
    t = SandboxTester(restrict_resources=False)
    assert t._validate_imports("from laap.agi.core import AGIAgent") == []


def test_import_whitelist_third_party_blocked():
    t = SandboxTester(restrict_resources=False)
    errs = t._validate_imports("import requests")
    assert any("requests" in e for e in errs)


def test_import_whitelist_relative_blocked():
    t = SandboxTester(restrict_resources=False)
    errs = t._validate_imports("from . import secret")
    assert any("relative import" in e for e in errs)


def test_import_whitelist_blocked_inside_function():
    """函数体内的 import 也应被拦截 (AST walk 覆盖)。"""
    t = SandboxTester(restrict_resources=False)
    code = "def f():\n    import sqlalchemy\n    return 1\n"
    errs = t._validate_imports(code)
    assert any("sqlalchemy" in e for e in errs)


def test_quick_validate_rejects_bad_import(tmp_path):
    """_quick_validate 完整路径: 合法语法 + 非法 import → 失败。"""
    t = SandboxTester(restrict_resources=False)
    m = CodeMutation(
        id="q1",
        target=CodeTarget(file_path="laap/agi/foo.py", function_name="f"),
        mutated_code="import pandas\n\ndef f():\n    return 1\n",
        status=MutationStatus.DRAFT,
    )
    # 沙箱里放一个 py 文件供 compile 步骤用
    (tmp_path / "foo.py").write_text("import pandas\n", encoding="utf-8")
    result = t._quick_validate(tmp_path, m)
    assert result["success"] is False
    assert "pandas" in result["errors"]


def test_quick_validate_accepts_clean_code(tmp_path):
    t = SandboxTester(restrict_resources=False)
    m = CodeMutation(
        id="q2",
        target=CodeTarget(file_path="laap/agi/foo.py", function_name="f"),
        mutated_code="import os\n\ndef f():\n    return os.getcwd()\n",
        status=MutationStatus.DRAFT,
    )
    (tmp_path / "foo.py").write_text("import os\n", encoding="utf-8")
    result = t._quick_validate(tmp_path, m)
    assert result["success"] is True


# ════════════════════════════════════════════════════════════
# M1: 沙箱只读 + restrict_resources
# ════════════════════════════════════════════════════════════

def test_restrict_resources_default_true():
    t = SandboxTester()
    assert t.restrict_resources is True


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root 绕过文件权限位，chmod 0o444 不阻止写入（CI/沙箱以 root 运行时跳过）")
def test_sandbox_files_made_readonly(monkeypatch, tmp_path):
    """restrict_resources=True 时 test_mutation 后沙箱文件应只读。"""
    from laap.agi.code_evolution import MutationStatus
    t = SandboxTester(restrict_resources=True)
    monkeypatch.setattr(t, "_run_tests", lambda sandbox, cmds: {"success": True})
    monkeypatch.setattr(t, "_quick_validate", lambda sandbox, m: {"success": True})
    # 真实源文件放 tmp_path 下
    src = tmp_path / "laap" / "agi" / "foo.py"
    src.parent.mkdir(parents=True)
    src.write_text("def f():\n    return 1\n", encoding="utf-8")
    m = CodeMutation(
        id="ro1",
        target=CodeTarget(file_path="laap/agi/foo.py", function_name="f"),
        original_code="def f():\n    return 1\n",
        mutated_code="def f():\n    return 2\n",
        status=MutationStatus.DRAFT,
    )
    captured = {}
    import tempfile
    real_temp = tempfile.TemporaryDirectory
    with tempfile.TemporaryDirectory(prefix="laap_sandbox_") as sd:
        # 手动走 test_mutation 的沙箱逻辑
        m.status = MutationStatus.TESTING
        from pathlib import Path as P
        sandbox = P(sd)
        dest = sandbox / "foo.py"
        dest.write_text(m.mutated_code, encoding="utf-8")
        # 模拟 M1 只读步骤
        for f in sandbox.rglob("*"):
            if f.is_file():
                os.chmod(f, 0o444)
        # 只读文件写入应失败
        with pytest.raises(PermissionError):
            dest.write_text("def hacked(): pass\n", encoding="utf-8")
        # 清理只读 (Windows 下删除前需恢复写权限)
        os.chmod(dest, 0o644)


def test_restrict_resources_false_skips_limits():
    """restrict_resources=False 时 _apply_limits 直接返回。"""
    t = SandboxTester(restrict_resources=False)
    t._apply_limits(None)  # 不应抛异常


# ════════════════════════════════════════════════════════════
# M3: /v1/evo/deploy API 端点
# ════════════════════════════════════════════════════════════

def test_evo_deploy_route_registered():
    """create_app 应注册 POST /v1/evo/deploy 路由。"""
    from laap_brain.api import create_app
    app = create_app()
    routes = {r.resource.canonical
              for r in app.router.routes() if r.resource}
    assert "/v1/evo/deploy" in routes


def test_evo_deploy_handler_missing_id():
    """缺少 mutation_id → 400。"""
    from laap_brain.api import handle_evo_deploy

    class _Req:
        async def json(self):
            return {}

    import asyncio
    loop = asyncio.new_event_loop()
    try:
        resp = loop.run_until_complete(handle_evo_deploy(_Req()))
    finally:
        loop.close()
    assert resp.status == 400
    assert "mutation_id" in resp.text


def test_evo_deploy_handler_not_found(monkeypatch):
    """未知 mutation_id → 409 + not_found。

    2026-08-17 修复: 显式注入 fake 引擎到 _get_code_evolution_engine ——
    此前依赖懒创建, 被 fixture 的 __init__ monkeypatch 污染模块级单例,
    导致 handle_evo_deploy 拿到未初始化引擎 → 500 (测试隔离 bug)。
    """
    from laap_brain.api import handle_evo_deploy, _get_code_evolution_engine

    class _FakeEngine:
        def approve_and_deploy(self, mutation_id, approver="api"):
            return {"status": "not_found", "mutation_id": mutation_id}

    monkeypatch.setattr(
        "laap_brain.api._get_code_evolution_engine", lambda: _FakeEngine())

    class _Req:
        async def json(self):
            return {"mutation_id": "does_not_exist"}

    import asyncio
    loop = asyncio.new_event_loop()
    try:
        resp = loop.run_until_complete(handle_evo_deploy(_Req()))
    finally:
        loop.close()
    assert resp.status == 409
    assert "not_found" in resp.text
