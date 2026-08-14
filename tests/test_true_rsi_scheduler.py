"""
M4 True RSI 服务链路挂载测试 (LAAP_TRSI_ENABLED)
================================================
守护 `laap_brain/api._start_evolution_scheduler` 入口对 M4 的支持:
  1. 默认 (无开关) 不启动, _true_rsi_engine 保持 None
  2. LAAP_TRSI_ENABLED=1 → 启动调度器, 且调度器驱动 TrueRSIEngine (受限递归)
  3. M4 守卫已注入: TrueRSIEngine.engine.scope_guard 非 None
  4. M3 API 单例仍指向原始 CodeEvolutionEngine (/v1/evo/* 语义不变)
  5. 仅 LAAP_EVO_ENABLED=1 → 回归 M2 (调度器驱动 CodeEvolutionEngine)
  6. 幂等 / LAAP_EVO_INTERVAL 周期控制
  7. TrueRSIEngine.auto_improve 与调度器兼容且恒强制 auto_deploy=False

重要: 注入 fake CodeEvolutionEngine / EvolutionScheduler, 绝不启动真实 daemon
线程 (全量回归时残留线程会导致 pytest 退出崩溃)。线程生命周期由
tests/test_evolution_scheduler.py 单独覆盖。

运行:
    python -m pytest tests/test_true_rsi_scheduler.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.evolution.true_rsi import TrueRSIEngine


class _FakeEngine:
    """fake CodeEvolutionEngine — 无 git 探测/扫描副作用; 支持动态挂 scope_guard。"""

    def __init__(self, repo_root=""):
        self.repo_root = repo_root


class _FakeScheduler:
    """fake EvolutionScheduler — 不启动线程, 仅记录构造参数。"""

    def __init__(self, engine=None, interval_seconds=3600, fitness_fn=None):
        self.engine = engine
        self.interval_seconds = interval_seconds
        self._fitness_fn = fitness_fn
        self._started = False

    def start(self):
        self._started = True
        return True

    def stop(self):
        self._started = False
        return True

    @property
    def is_running(self):
        return self._started

    def stats(self):
        return {"running": self._started, "interval_seconds": self.interval_seconds}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """重置单例与环境变量, 注入 fake 组件。"""
    import laap_brain.api as api
    import laap.agi.code_evolution as ce_mod
    import laap.agi.evolution_scheduler as es_mod

    monkeypatch.setattr(api, "_evolution_scheduler", None)
    monkeypatch.setattr(api, "_code_evolution_engine", None)
    monkeypatch.setattr(api, "_true_rsi_engine", None)
    monkeypatch.delenv("LAAP_EVO_ENABLED", raising=False)
    monkeypatch.delenv("LAAP_TRSI_ENABLED", raising=False)
    monkeypatch.delenv("LAAP_EVO_INTERVAL", raising=False)
    monkeypatch.setattr(ce_mod, "CodeEvolutionEngine", _FakeEngine)
    monkeypatch.setattr(es_mod, "EvolutionScheduler", _FakeScheduler)
    yield
    # 清理: stop 任何残留 (fake, 无副作用)
    s = getattr(api, "_evolution_scheduler", None)
    if s is not None:
        try:
            s.stop()
        except Exception:
            pass


# ════════════════════════════════════════════════════════════
# 1. 默认关闭
# ════════════════════════════════════════════════════════════

def test_default_disabled():
    """无任何开关时不得启动调度器, M4 引擎不挂载。"""
    import laap_brain.api as api
    s = api._start_evolution_scheduler()
    assert s is None
    assert api._true_rsi_engine is None


# ════════════════════════════════════════════════════════════
# 2. LAAP_TRSI_ENABLED=1 → 启动且驱动 TrueRSIEngine
# ════════════════════════════════════════════════════════════

def test_trsi_enabled_starts_with_true_rsi(monkeypatch):
    """TRSI=1 → 调度器启动, 驱动真实 TrueRSIEngine, 守卫已注入。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_TRSI_ENABLED", "1")
    s = api._start_evolution_scheduler()
    assert s is not None
    assert isinstance(s.engine, TrueRSIEngine)
    assert api._true_rsi_engine is s.engine
    # M4 守卫已注入底层 CodeEvolutionEngine
    inner = s.engine.engine
    assert inner.scope_guard is not None
    assert s.stats()["interval_seconds"] == 3600  # 默认 1 小时


def test_trsi_does_not_require_evo_flag(monkeypatch):
    """TRSI=1 且 EVO 未开 → 调度器仍启动 (M4 隐含调度)。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_TRSI_ENABLED", "1")
    assert api._start_evolution_scheduler() is not None


# ════════════════════════════════════════════════════════════
# 3. M3 API 单例仍指向原始 CodeEvolutionEngine
# ════════════════════════════════════════════════════════════

def test_m3_api_engine_is_raw_code_evolution(monkeypatch):
    """TRSI 挂载后 _get_code_evolution_engine 返回原始引擎 (M3 /v1/evo/* 语义不变)。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_TRSI_ENABLED", "1")
    api._start_evolution_scheduler()
    api_engine = api._get_code_evolution_engine()
    assert api_engine is not None
    assert not isinstance(api_engine, TrueRSIEngine)
    # 与 TrueRSIEngine 包装的是同一实例
    assert api._true_rsi_engine.engine is api_engine


# ════════════════════════════════════════════════════════════
# 4. 仅 EVO → 回归 M2 (驱动 CodeEvolutionEngine)
# ════════════════════════════════════════════════════════════

def test_evo_only_keeps_code_evolution(monkeypatch):
    """仅 EVO=1 → 调度器驱动 CodeEvolutionEngine (M2 原行为), M4 不挂载。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_EVO_ENABLED", "1")
    s = api._start_evolution_scheduler()
    assert s is not None
    assert isinstance(s.engine, _FakeEngine)
    assert api._true_rsi_engine is None


def test_both_flags_m4_wins(monkeypatch):
    """EVO 与 TRSI 同时开 → M4 优先 (调度器驱动 TrueRSIEngine)。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_EVO_ENABLED", "1")
    monkeypatch.setenv("LAAP_TRSI_ENABLED", "1")
    s = api._start_evolution_scheduler()
    assert isinstance(s.engine, TrueRSIEngine)


# ════════════════════════════════════════════════════════════
# 5. 幂等 + interval
# ════════════════════════════════════════════════════════════

def test_idempotent_singleton(monkeypatch):
    """重复调用返回同一实例 (服务进程只应有一个调度器)。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_TRSI_ENABLED", "1")
    s1 = api._start_evolution_scheduler()
    s2 = api._start_evolution_scheduler()
    assert s1 is s2


def test_interval_control(monkeypatch):
    """LAAP_EVO_INTERVAL 控制周期 (TRSI 下同样生效)。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_TRSI_ENABLED", "1")
    monkeypatch.setenv("LAAP_EVO_INTERVAL", "45")
    s = api._start_evolution_scheduler()
    assert s.stats()["interval_seconds"] == 45


# ════════════════════════════════════════════════════════════
# 6. TrueRSIEngine.auto_improve 与调度器兼容
# ════════════════════════════════════════════════════════════

def test_auto_improve_scheduler_compatible(monkeypatch):
    """scheduler 调用 true_rsi.auto_improve: 参数透传 + auto_deploy 恒强制 False。"""
    captured = {}

    def _fake_auto_improve(directory="", max_mutations=5,
                           auto_deploy=False, **kw):
        captured["directory"] = directory
        captured["max_mutations"] = max_mutations
        captured["auto_deploy"] = auto_deploy
        return [{"status": "test_passed", "target": "laap/agi/foo.py"}]

    fe = _FakeEngine(repo_root="/tmp")
    fe.auto_improve = _fake_auto_improve
    trsi = TrueRSIEngine(engine=fe)

    results = trsi.auto_improve(
        directory="laap/agi/", max_mutations=2, auto_deploy=True)
    assert captured["directory"] == "laap/agi/"
    assert captured["max_mutations"] == 2
    # M4 约束 4: 即使调用方 auto_deploy=True, 底层也收到 False
    assert captured["auto_deploy"] is False
    assert len(results) == 1
    assert results[0]["status"] == "test_passed"
