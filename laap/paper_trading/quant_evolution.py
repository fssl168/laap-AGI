"""LAAP Paper Trading — 代码级受限递归编排（闭环 B 后半，决策 #4）。

把 M4 的代码级自改进（CodeEvolutionEngine）接入交易场景：
  - QuantScopeGuard   作用域守卫（复用 M4 永久只读清单，限定 paper_trading）
  - QuantEvolutionGate 交易适应度 OOS 门禁（deploy_gate 协议）
  - QuantEvolutionEngine 编排（attach / evolve / approve_and_deploy）

治理约束（延续 M1-M4）：默认关、人工审批、可回滚、fail-closed。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from laap.paper_trading.backtest_runner import BacktestRunner, split_series

logger = logging.getLogger("laap.paper_trading.quant_evolution")


class QuantScopeGuard:
    """paper_trading 作用域守卫（复用 M4 永久只读清单）。"""

    def __call__(self, target: Any, depth: int = 0) -> Tuple[bool, str]:
        from laap.evolution.true_rsi import TrueRSIEngine
        norm = (getattr(target, "file_path", "") or "").replace("\\", "/")
        base = os.path.basename(norm)
        # 永久只读：进化安全基座 + 核心/安全文件（任何深度）
        if base in TrueRSIEngine.PROTECTED_SAFETY:
            return False, f"permanent read-only (evolution safety base): {norm}"
        if base in TrueRSIEngine.PROTECTED_CORE:
            return False, f"permanent read-only (core/security): {norm}"
        # 作用域：仅 paper_trading 业务代码
        if not norm.startswith("laap/paper_trading/"):
            return False, f"out of scope (paper_trading only): {norm}"
        return True, ""


class QuantEvolutionGate:
    """交易适应度 OOS 门禁（deploy_gate 协议）。

    增强 3 后分两种：
      - 目标是 strategy.py → 从 mutated_code AST 提取参数，做 mutation 前后 OOS 对比
        （mutated OOS 不劣于 baseline OOS 才放行）。
      - 其他 paper_trading 代码 → baseline OOS 健康门禁（系统 OOS 不劣化才放行）。
    均 fail-closed：不满足即拒绝。
    """

    def __init__(self, runner: BacktestRunner, price_series: List[float],
                 baseline_params: Optional[Dict[str, Any]] = None):
        self.runner = runner
        self.price_series = price_series
        if baseline_params is None:
            from laap.paper_trading.param_extractor import load_baseline_params
            baseline_params = load_baseline_params()
        self.baseline_params = baseline_params

    def __call__(self, mutation: Any, engine: Any) -> Tuple[bool, str]:
        target = (getattr(getattr(mutation, "target", None), "file_path", "") or "")
        target = target.replace("\\", "/")
        if not target.startswith("laap/paper_trading/"):
            return True, ""  # 非交易业务代码不受交易门禁约束

        n = len(self.price_series)
        if n < 10:
            return False, "price_series too short for OOS gate"
        dates = list(range(n))
        train, valid, oos = split_series(dates)
        oos_start = len(train) + len(valid)

        # 策略参数文件 → mutation 前后 OOS 对比（增强 3）
        if target.endswith("strategy.py"):
            from laap.paper_trading.param_extractor import extract_strategy_params
            mutated_params = extract_strategy_params(mutation.mutated_code)
            if not mutated_params:
                return False, "strategy.py mutation: cannot extract STRATEGY_PARAMS"
            return self._compare_params(mutated_params, oos_start, n)

        # 其他业务代码 → baseline OOS 健康门禁
        train_metrics = self.runner.run_backtest(
            self.price_series, params=self.baseline_params, split=(0, len(train)))
        oos_metrics = self.runner.run_backtest(
            self.price_series, params=self.baseline_params, split=(oos_start, n))
        ok, reason = self.runner.oos_gate(train_metrics, oos_metrics)
        if not ok:
            return False, f"OOS gate blocked: {reason}"
        return True, (f"OOS not degraded (train_sharpe={train_metrics['sharpe_ratio']}, "
                      f"oos_sharpe={oos_metrics['sharpe_ratio']})")

    def _compare_params(self, mutated_params: Dict[str, Any],
                        oos_start: int, n: int) -> Tuple[bool, str]:
        """mutation 前后 OOS 对比：mutated OOS 不劣于 baseline OOS 才放行。"""
        try:
            base_oos = self.runner.run_backtest(
                self.price_series, params=self.baseline_params, split=(oos_start, n))
            mut_oos = self.runner.run_backtest(
                self.price_series, params=mutated_params, split=(oos_start, n))
        except Exception as e:
            return False, f"mutated params backtest failed: {e}"

        if mut_oos["cumulative_return"] < base_oos["cumulative_return"]:
            return False, (f"mutated OOS cumulative_return "
                           f"{mut_oos['cumulative_return']:.2%} < baseline "
                           f"{base_oos['cumulative_return']:.2%}")
        if mut_oos["sharpe_ratio"] < base_oos["sharpe_ratio"]:
            return False, (f"mutated OOS sharpe {mut_oos['sharpe_ratio']:.3f} < "
                           f"baseline {base_oos['sharpe_ratio']:.3f}")
        return True, (f"mutated OOS not degraded (cumret={mut_oos['cumulative_return']:.2%}, "
                      f"sharpe={mut_oos['sharpe_ratio']:.3f})")


class QuantEvolutionEngine:
    """代码级受限递归编排（闭环 B）。"""

    def __init__(self, code_evo_engine: Any, runner: BacktestRunner,
                 price_series: List[float], audit: Any = None, db: Any = None):
        self.engine = code_evo_engine
        self.runner = runner
        self.price_series = price_series
        self.audit = audit if audit is not None else getattr(code_evo_engine, "audit", None)
        self.db = db
        self._scope_guard = QuantScopeGuard()
        self._deploy_gate = QuantEvolutionGate(runner, price_series)

    def attach(self) -> "QuantEvolutionEngine":
        """装双守卫：scope_guard（作用域）+ deploy_gate（交易门禁）。"""
        self.engine.scope_guard = self._scope_guard
        self.engine.deploy_gate = self._deploy_gate
        logger.info("QuantEvolutionEngine attached (scope_guard + deploy_gate)")
        return self

    def evolve(self, max_mutations: int = 1) -> List[Dict[str, Any]]:
        """触发一轮受限进化：扫 paper_trading 业务代码 → 产提案（不自动部署）。"""
        results = self.engine.auto_improve(
            directory="laap/paper_trading/",
            max_mutations=max_mutations,
            auto_deploy=False,  # 走人工审批
            depth=0,
        )
        # 审计双写：SQLite evolutions 表 + jsonl（EvolutionAuditLog）
        for r in results:
            self._audit_to_db(r.get("mutation_id", ""),
                              r.get("status", "unknown"),
                              r.get("reason", ""))
        return results

    def approve_and_deploy(self, mutation_id: str, approver: str = "quant") -> Dict[str, Any]:
        """人工批准并部署一个 mutation。"""
        result = self.engine.approve_and_deploy(mutation_id, approver=approver)
        self._audit_to_db(mutation_id, result.get("status", "unknown"),
                          result.get("error", ""))
        return result

    def rollback_last(self) -> Dict[str, Any]:
        return self.engine.rollback_last()

    def _audit_to_db(self, mutation_id: str, decision: str, reason: str) -> None:
        """进化决策双写到 SQLite evolutions 表（决策 #3 审计）。"""
        if self.db is None:
            return
        import json
        import time as _time
        try:
            conn = self.db.conn()
            conn.execute(
                "INSERT OR REPLACE INTO evolutions "
                "(mutation_id, decision, reason, meta_json, ts) VALUES (?, ?, ?, ?, ?)",
                (mutation_id or "", decision, reason[:200] if reason else "",
                 json.dumps({"mode": "quant-code-evolution"}, ensure_ascii=False),
                 _time.time()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"evolutions audit write failed: {e}")

    def stats(self) -> Dict[str, Any]:
        return {
            "mode": "quant-code-evolution",
            "scope": "laap/paper_trading/",
            "engine_stats": self.engine.stats() if hasattr(self.engine, "stats") else None,
        }
