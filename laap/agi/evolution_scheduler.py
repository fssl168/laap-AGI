"""
LAAP AGI — 进化调度器 (EvolutionScheduler) — M2 True RSI
========================================================
让 CodeEvolutionEngine 周期性"跑起来"的闭环心跳。

流程 (每 tick):
  1. 计算当前适应度 (baseline)
  2. scan_targets → auto_improve(max_mutations=1, auto_deploy=False)
     (M2 阶段默认不自动部署 — 提案经测试后留给 M3 授权/人工决策)
  3. 若测试通过, 记录提案供后续评估

安全:
  - 由 LAAP_EVO_ENABLED=1 环境变量显式开启 (默认关闭)
  - daemon 线程, stop() 可干净退出
  - 每 tick 最多 1 个 mutation (渐进式, 防抖动)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("laap.agi.evolution_scheduler")


class EvolutionScheduler:
    """周期性代码进化调度器 (M2)。"""

    def __init__(self,
                 engine: Any,
                 fitness_fn: Optional[Callable[[], float]] = None,
                 interval_seconds: int = 3600,
                 max_mutations_per_tick: int = 1):
        """
        Args:
            engine: CodeEvolutionEngine 实例
            fitness_fn: 无参返回当前适应度的可调用对象; 缺省用 FitnessEvaluator
            interval_seconds: tick 间隔 (默认 3600s = 1 小时)
            max_mutations_per_tick: 每 tick 最多尝试的 mutation 数
        """
        self.engine = engine
        self.interval = interval_seconds
        self.max_mutations = max_mutations_per_tick
        self._fitness_fn = fitness_fn
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.tick_count = 0
        self.last_results: List[Dict[str, Any]] = []

        if fitness_fn is None:
            try:
                from laap.agi.fitness import FitnessEvaluator
                _fe = FitnessEvaluator()
                self._fitness_fn = lambda: _fe.composite()["score"]
            except Exception:
                self._fitness_fn = lambda: 0.5

    # ════════════════════════════════════════════════════════
    # 生命周期
    # ════════════════════════════════════════════════════════

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """启动调度线程 (幂等)。"""
        if self.is_running:
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="evolution-scheduler", daemon=True)
        self._thread.start()
        logger.info(f"EvolutionScheduler started (interval={self.interval}s)")
        return True

    def stop(self) -> bool:
        """停止调度线程 (幂等)。"""
        if not self.is_running:
            return False
        self._stop_event.set()
        self._thread.join(timeout=10)
        self._thread = None
        logger.info("EvolutionScheduler stopped")
        return True

    # ════════════════════════════════════════════════════════
    # 主循环
    # ════════════════════════════════════════════════════════

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.error(f"Evolution tick failed: {e}")
            # 等待间隔 (可中断)
            self._stop_event.wait(self.interval)

    def _tick(self) -> Dict[str, Any]:
        """执行一轮进化 (单 mutation, 不自动部署)。"""
        self.tick_count += 1
        baseline = self._fitness_fn() if self._fitness_fn else 0.5

        results = self.engine.auto_improve(
            directory="laap/agi/",
            max_mutations=self.max_mutations,
            auto_deploy=False,  # M2 默认不部署, 由 M3 授权
        )
        self.last_results = results

        tick = {
            "tick": self.tick_count,
            "baseline_fitness": round(baseline, 4),
            "results": results,
            "ts": time.time(),
        }
        logger.info(f"Evolution tick #{self.tick_count}: "
                    f"{sum(1 for r in results if r.get('status') == 'test_passed')} test-passed")
        return tick

    # ════════════════════════════════════════════════════════
    # 状态
    # ════════════════════════════════════════════════════════

    def stats(self) -> Dict[str, Any]:
        return {
            "running": self.is_running,
            "tick_count": self.tick_count,
            "interval_seconds": self.interval,
            "max_mutations_per_tick": self.max_mutations,
            "last_tick_results": len(self.last_results),
            "fitness_fn_configured": self._fitness_fn is not None,
        }
