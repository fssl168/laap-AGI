"""
API 服务链路进化调度器启动测试 (M2 修复)
========================================
验证 LAAP_EVO_ENABLED 在服务链路 (python -m laap_brain.api) 真正生效:
  1. 默认 (未设置环境变量) 不启动调度器
  2. LAAP_EVO_ENABLED=1 时启动调度器 (fake 组件, 不启动真实线程)
  3. 幂等: 重复调用返回同一实例
  4. LAAP_EVO_INTERVAL 控制周期

背景: 原调度器挂在 AGIAgent.__init__, 但服务链路从不实例化 agent,
导致环境变量被读取却从未生效。本测试守护 api._start_evolution_scheduler 入口。

重要: 测试注入 fake CodeEvolutionEngine / EvolutionScheduler, 绝不启动真实
daemon 线程 (全量回归时残留线程会导致 pytest 退出崩溃, exit 0xC0000142)。
线程生命周期由 tests/test_evolution_scheduler.py 单独覆盖。

运行:
    python -m pytest tests/test_evo_scheduler_api.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


class _FakeEngine:
    """fake CodeEvolutionEngine — 无 git 探测/扫描副作用。"""

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
    """重置单例与环境变量, 注入 fake 组件 (函数级 from-import 可被模块 monkeypatch 覆盖)。"""
    import laap_brain.api as api
    import laap.agi.code_evolution as ce_mod
    import laap.agi.evolution_scheduler as es_mod

    monkeypatch.setattr(api, "_evolution_scheduler", None)
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


def test_default_disabled():
    """未设置 LAAP_EVO_ENABLED 时不得启动。"""
    import laap_brain.api as api
    s = api._start_evolution_scheduler()
    assert s is None


def test_enabled_starts(monkeypatch):
    """LAAP_EVO_ENABLED=1 时启动调度器 (fake 实例已创建)。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_EVO_ENABLED", "1")
    s = api._start_evolution_scheduler()
    assert s is not None
    assert isinstance(s, _FakeScheduler)
    assert s.stats()["interval_seconds"] == 3600  # 默认 1 小时


def test_enabled_interval(monkeypatch):
    """LAAP_EVO_INTERVAL 控制周期。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_EVO_ENABLED", "1")
    monkeypatch.setenv("LAAP_EVO_INTERVAL", "30")
    s = api._start_evolution_scheduler()
    assert s.stats()["interval_seconds"] == 30


def test_idempotent_singleton(monkeypatch):
    """重复调用返回同一实例 (服务进程只应有一个调度器)。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_EVO_ENABLED", "1")
    s1 = api._start_evolution_scheduler()
    s2 = api._start_evolution_scheduler()
    assert s1 is s2


def test_scheduler_constructed_with_engine(monkeypatch):
    """调度器应收到 CodeEvolutionEngine 实例 (引擎装配正确)。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_EVO_ENABLED", "1")
    s = api._start_evolution_scheduler()
    assert s is not None
    assert s.engine is not None
    assert s.engine.repo_root  # 传入的 repo_root 非空
