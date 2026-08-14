"""
LAAP AGI — True RSI 受限递归引擎 (M4)
========================================
在 M1-M3 (硬隔离沙箱 / 闭环调度 / 治理审计) 基础上, 开放"代码级自改进"的
**受限**形态。文档: docs/true-rsi-feasibility.md §3 M4。

四道约束 (缺一不可):

  1. 作用域限定   — 只允许改进 `laap/agi/` 下非核心、非安全文件。
  2. 永久只读     — 进化安全基座 (code_evolution/scheduler/audit/fitness/...)
                     与核心/安全文件 (core/safety/security_system/__init__)
                     在任何递归深度均不可作为 target。
  3. 递归深度<=1  — 只允许"改进改进者"一层 (深度 0→1), 禁止改进
                     "改进者的改进者" (深度>=2)。唯一可递归目标是
                     `laap/evolution/true_rsi.py` (改进者自身)。
  4. 不自动部署   — 默认 auto_deploy=False; 提案经 M3 授权 API 人工批准
                     (`/v1/evo/deploy`), 契合文档"谨慎、受限、可放弃"。

实现方式:
  - TrueRSIEngine 作为编排层, 复用 CodeEvolutionEngine 的
    analyzer/patcher/tester/audit/git 基础设施。
  - 通过 `engine.scope_guard` 钩子插入既有 `_improve_single` 流程,
    对每个候选 target 做 M4 守卫判定; 不启用时 M1-M3 行为完全不变。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("laap.evolution.true_rsi")


class TrueRSIEngine:
    """受限递归代码进化编排层 (M4 True RSI)。

    Args:
        engine: CodeEvolutionEngine 实例 (M1-M3 已装配)
        auto_deploy: 是否允许自动部署 (M4 默认 False; 即使为 True,
                     改进者自身的递归提案仍强制走人工批准)

    Usage:
        from laap.evolution.true_rsi import TrueRSIEngine
        trsi = TrueRSIEngine(code_evolution_engine)
        results = trsi.improve()          # 产提案 (默认不部署)
        trsi.approve_and_deploy(mutation_id, approver="ops")
    """

    # 唯一允许"递归"的目标 — 改进者自身 (深度 0 → 1 的唯一合法入口)
    SELF_FILE = "laap/evolution/true_rsi.py"

    # 递归深度上限: 只允许"改进改进者"一层
    MAX_RECURSION_DEPTH = 1

    # 永久只读 — 进化安全基座 (从 M1 起固化, M4 也不放开)
    PROTECTED_SAFETY = frozenset({
        "code_evolution.py",
        "evolution_system.py",
        "rsi_engine.py",
        "evolution_scheduler.py",
        "evolution_audit.py",
        "fitness.py",
    })

    # 永久只读 — 核心 / 安全文件
    PROTECTED_CORE = frozenset({
        "core.py",
        "safety.py",
        "security_system.py",
        "__init__.py",
    })

    # 业务作用域目录 (递归深度 0 的目标必须位于此处)
    ALLOWED_DIRS = ("laap/agi/",)

    def __init__(self, engine: Any, auto_deploy: bool = False):
        self.engine = engine
        self.auto_deploy = auto_deploy
        # 递归配额记账: 0 = 未使用自我改进; 1 = 已改进改进者一层 (配额耗尽)
        self._recursion_depth = 0

        # 注入 M4 守卫到既有引擎 (仅本引擎持有; 裸 CodeEvolutionEngine 不受影响)
        if engine is not None:
            engine.scope_guard = self._guard

    # ════════════════════════════════════════════════════════
    # M4 守卫 (由 CodeEvolutionEngine._improve_single 调用)
    # ════════════════════════════════════════════════════════

    def _guard(self, target: Any, depth: int = 0) -> Tuple[bool, str]:
        """M4 四道约束判定。

        Returns (is_allowed, reason).
        """
        norm = (getattr(target, "file_path", "") or "").replace("\\", "/")
        if not norm:
            return False, "empty target file_path"

        base = os.path.basename(norm)

        # 约束 2: 永久只读 — 安全基座 + 核心/安全文件, 任何深度均拒绝
        if base in self.PROTECTED_SAFETY:
            return False, f"permanent read-only (evolution safety base): {norm}"
        if base in self.PROTECTED_CORE:
            return False, f"permanent read-only (core/security): {norm}"

        # 改进者自身 — 约束 3 的递归入口
        if norm == self.SELF_FILE:
            if depth >= self.MAX_RECURSION_DEPTH:
                return False, (
                    f"recursion depth exceeded ({depth} >= "
                    f"{self.MAX_RECURSION_DEPTH}): "
                    "self-improvement of the improver already applied")
            return True, ""

        # 约束 1: 作用域限定 — 仅 laap/agi/ 下非保护文件
        if not norm.startswith(self.ALLOWED_DIRS[0]):
            return False, (
                f"out of scope: {norm} "
                f"(only {self.ALLOWED_DIRS[0]} and {self.SELF_FILE} allowed)")

        return True, ""

    # ════════════════════════════════════════════════════════
    # 主入口
    # ════════════════════════════════════════════════════════

    def improve(self,
                max_mutations: int = 1,
                directory: str = "laap/agi/") -> List[Dict[str, Any]]:
        """执行一轮受限递归进化 (默认不自动部署)。

        递归记账: 改进者自身 (`true_rsi.py`) 的改进测试通过后,
        递归深度升到上限 — 之后对该文件的新提案一律被守卫拒绝。
        """
        results = self.engine.auto_improve(
            directory=directory,
            max_mutations=max_mutations,
            auto_deploy=False,          # 约束 4: M4 永不自动部署, 提案走人工批准
            depth=self._recursion_depth,
        )

        # 递归配额记账: 改进者自身的改进一旦测试通过, 即视为已消耗一层递归
        for r in results:
            tgt = (r.get("target") or "").replace("\\", "/")
            if tgt.startswith(self.SELF_FILE) and r.get("status") == "test_passed":
                self._recursion_depth = self.MAX_RECURSION_DEPTH
                logger.warning(
                    "M4 recursion quota exhausted: true_rsi.py self-improvement "
                    "test-passed (depth=1). Further self-improvement is denied.")

        return results

    # ════════════════════════════════════════════════════════
    # 治理 (包装 M3 API, 追加递归配额语义)
    # ════════════════════════════════════════════════════════

    def approve_and_deploy(self, mutation_id: str,
                           approver: str = "m4") -> Dict[str, Any]:
        """人工批准并部署一个 mutation (复用 M3 /v1/evo/deploy 语义)。

        若被部署的是改进者自身, 同步消耗递归配额。
        """
        result = self.engine.approve_and_deploy(mutation_id, approver=approver)
        if result.get("status") == "deployed":
            m = next((x for x in getattr(self.engine, "mutations", [])
                      if x.id == mutation_id), None)
            if m is not None:
                tgt = getattr(getattr(m, "target", None), "file_path", "")
                if tgt.replace("\\", "/") == self.SELF_FILE:
                    self._recursion_depth = self.MAX_RECURSION_DEPTH
        return result

    def rollback_last(self) -> Dict[str, Any]:
        """回滚最近一次部署 (包装 M3)。"""
        return self.engine.rollback_last()

    # ════════════════════════════════════════════════════════
    # 状态
    # ════════════════════════════════════════════════════════

    def stats(self) -> Dict[str, Any]:
        return {
            "mode": "M4-restricted-recursion",
            "max_recursion_depth": self.MAX_RECURSION_DEPTH,
            "recursion_depth": self._recursion_depth,
            "recursion_quota_exhausted":
                self._recursion_depth >= self.MAX_RECURSION_DEPTH,
            "auto_deploy": self.auto_deploy,
            "scope": {
                "allowed_dirs": list(self.ALLOWED_DIRS),
                "self_file": self.SELF_FILE,
                "protected_safety": sorted(self.PROTECTED_SAFETY),
                "protected_core": sorted(self.PROTECTED_CORE),
            },
            "engine_stats": self.engine.stats() if hasattr(self.engine, "stats")
                            else None,
        }
