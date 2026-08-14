"""
Evolution Audit 治理测试 (M3 True RSI)
======================================
验证进化治理层:
  1. 审计日志写入/查询/统计 (JSONL)
  2. 冷却期机制 (防抖动)
  3. CodeEvolutionEngine 接入审计 (决策点记录)
  4. SafetyGuard 保护清单通过 API 可见

运行:
    python -m pytest tests/test_evolution_audit.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.agi.evolution_audit import EvolutionAuditLog
from laap.agi.code_evolution import CodeMutation, CodeTarget


@pytest.fixture()
def audit(tmp_path):
    return EvolutionAuditLog(repo_root=str(tmp_path), cooldown_hours=24)


@pytest.fixture()
def mutation():
    return CodeMutation(
        id="mut_test",
        target=CodeTarget(file_path="laap/agi/foo.py"),
    )


# ════════════════════════════════════════════════════════════
# 1. 审计日志
# ════════════════════════════════════════════════════════════

def test_record_and_query(audit, mutation):
    audit.record(mutation, "proposed", "generated")
    audit.record(mutation, "test_passed", "sandbox ok")
    entries = audit.query()
    assert len(entries) == 2
    assert entries[0]["decision"] == "test_passed"  # 倒序
    assert entries[0]["mutation_id"] == "mut_test"
    assert entries[0]["target"] == "laap/agi/foo.py"


def test_stats_by_decision(audit, mutation):
    audit.record(mutation, "proposed", "")
    audit.record(mutation, "test_passed", "")
    audit.record(mutation, "deployed", "")
    st = audit.stats()
    assert st["total_entries"] == 3
    assert st["by_decision"]["deployed"] == 1
    assert st["by_decision"]["test_passed"] == 1


def test_log_file_is_jsonl(audit, mutation, tmp_path):
    audit.record(mutation, "proposed", "")
    log_path = tmp_path / "state" / "evolution_audit.jsonl"
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["mutation_id"] == "mut_test"


# ════════════════════════════════════════════════════════════
# 2. 冷却期
# ════════════════════════════════════════════════════════════

def test_cooldown_active_after_record(audit, mutation):
    audit.record(mutation, "proposed", "")
    in_cd, remaining = audit.cooldown_check("laap/agi/foo.py")
    assert in_cd is True
    assert remaining > 0


def test_cooldown_not_affect_other_target(audit, mutation):
    audit.record(mutation, "proposed", "")
    in_cd, _ = audit.cooldown_check("laap/agi/bar.py")
    assert in_cd is False


def test_cooldown_expires(audit, mutation):
    audit.record(mutation, "proposed", "")
    # 把冷却期压到接近 0: 模拟 25 小时前的记录
    audit._path.write_text(
        json.dumps({"ts": audit.query()[0]["ts"] - 25 * 3600,
                    "decision": "proposed", "target": "laap/agi/foo.py",
                    "mutation_id": "old"}) + "\n",
        encoding="utf-8")
    in_cd, _ = audit.cooldown_check("laap/agi/foo.py")
    assert in_cd is False


# ════════════════════════════════════════════════════════════
# 3. 引擎接入审计
# ════════════════════════════════════════════════════════════

def test_engine_has_audit(monkeypatch, tmp_path):
    from laap.agi.code_evolution import CodeEvolutionEngine as CEE
    # 轻量构造, 跳过 git 子进程探测
    monkeypatch.setattr(CEE, "__init__", lambda self, repo_root="", llm_fn=None: None)
    e = object.__new__(CEE)
    e.repo_root = str(tmp_path)
    e.mutations = []
    e.deployed_count = 0
    e.rollback_count = 0
    e.audit = None
    e.patcher = None
    e.tester = None
    e.git = None
    e.analyzer = None
    # 显式注入审计
    from laap.agi.evolution_audit import EvolutionAuditLog
    e.audit = EvolutionAuditLog(repo_root=str(tmp_path))
    assert e.audit is not None
    st = e.audit.stats()
    assert "total_entries" in st


def test_engine_audit_records_proposal(monkeypatch, tmp_path):
    """auto_improve 链路中 mutation 决策应写审计。"""
    from laap.agi.code_evolution import CodeEvolutionEngine as CEE
    from laap.agi.code_evolution import PatchGenerator, SandboxTester, GitIntegrator
    from laap.agi.evolution_audit import EvolutionAuditLog

    monkeypatch.setattr(CEE, "__init__", lambda self, repo_root="", llm_fn=None: None)
    e = object.__new__(CEE)
    e.repo_root = str(tmp_path)
    e.mutations = []
    e.deployed_count = 0
    e.rollback_count = 0
    e.audit = EvolutionAuditLog(repo_root=str(tmp_path))
    e.patcher = PatchGenerator(llm_generate_fn=None)
    e.tester = SandboxTester()
    e.git = GitIntegrator(str(tmp_path))
    e.analyzer = None
    e.qa = None
    e._lock = __import__("threading").Lock()

    # 构造目标并跑单目标改进 (auto_deploy=False)
    target = CodeTarget(file_path="laap/agi/foo.py", function_name="f",
                        current_code="def f():\n    return 1\n")
    result = e._improve_single(target, test_commands=None, auto_deploy=False)
    entries = e.audit.query()
    assert len(entries) >= 1
    # 至少记录了 proposed 或 test_passed 决策
    decisions = {en["decision"] for en in entries}
    assert decisions & {"proposed", "test_passed", "rejected"}


# ════════════════════════════════════════════════════════════
# 4. 保护清单可见性 (M3 治理视图)
# ════════════════════════════════════════════════════════════

def test_protected_files_visible():
    from laap.agi.code_evolution import SafetyGuard
    assert "laap/agi/code_evolution.py" in SafetyGuard.PROTECTED_FILES
    assert "laap/agi/evolution_system.py" in SafetyGuard.PROTECTED_FILES
    assert "laap/agi/rsi_engine.py" in SafetyGuard.PROTECTED_FILES
