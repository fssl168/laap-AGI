"""
API 服务链路进化调度器启动测试 (M2 修复)
========================================
验证 LAAP_EVO_ENABLED 在服务链路 (python -m laap_brain.api) 真正生效:
  1. 默认 (未设置环境变量) 不启动调度器
  2. LAAP_EVO_ENABLED=1 时启动调度器并执行 tick
  3. 幂等: 重复调用返回同一实例
  4. LAAP_EVO_INTERVAL 控制周期

背景: 原调度器挂在 AGIAgent.__init__, 但服务链路从不实例化 agent,
导致环境变量被读取却从未生效。本测试守护 api._start_evolution_scheduler 入口。

运行:
    python -m pytest tests/test_evo_scheduler_api.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def _reset_scheduler(monkeypatch):
    """每个测试前重置 api 模块的调度器单例与环境变量。"""
    import laap_brain.api as api
    # 重置单例
    monkeypatch.setattr(api, "_evolution_scheduler", None)
    # 清空环境变量
    monkeypatch.delenv("LAAP_EVO_ENABLED", raising=False)
    monkeypatch.delenv("LAAP_EVO_INTERVAL", raising=False)
    yield
    # 清理: 若测试启动过调度器, 确保停止
    s = api._evolution_scheduler
    if s is not None and s.is_running:
        s.stop()


def test_default_disabled():
    """未设置 LAAP_EVO_ENABLED 时不得启动。"""
    import laap_brain.api as api
    s = api._start_evolution_scheduler()
    assert s is None


def test_enabled_starts(monkeypatch):
    """LAAP_EVO_ENABLED=1 时启动调度器。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_EVO_ENABLED", "1")
    s = api._start_evolution_scheduler()
    assert s is not None
    assert s.is_running is True
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


def test_tick_executes_when_enabled(monkeypatch):
    """开启后 tick 真实执行 (不部署, 产出 proposal 记录)。"""
    import laap_brain.api as api
    monkeypatch.setenv("LAAP_EVO_ENABLED", "1")
    monkeypatch.setenv("LAAP_EVO_INTERVAL", "1")

    # 隔离: 启动函数内部会构造 CodeEvolutionEngine (git 探测/扫描), 单测中
    # 直接注入轻量 engine 到调度器, 避免真实 auto_improve 卡线程
    from laap.agi.evolution_scheduler import EvolutionScheduler

    class _FakeEngine:
        def auto_improve(self, **kw):
            return [{"status": "test_passed", "target": "x.py"}]

    # 直接构造调度器 (绕过 api._start_evolution_scheduler 的真实引擎创建)
    fake = _FakeEngine()
    scheduler = EvolutionScheduler(engine=fake, interval_seconds=1,
                                   fitness_fn=lambda: 0.5)
    monkeypatch.setattr(api, "_evolution_scheduler", scheduler)
    scheduler.start()
    try:
        tick = scheduler._tick()
        assert "tick" in tick
        assert "results" in tick
        for r in tick["results"]:
            assert r.get("status") != "deployed"
    finally:
        scheduler.stop()
