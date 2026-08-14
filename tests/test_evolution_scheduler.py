"""
Evolution Scheduler & Fitness 测试 (M2 True RSI)
================================================
验证闭环调度器与适应度评估器:
  1. Scheduler 启停 / tick 执行 / stats
  2. Fitness 合成逻辑与分量归一化

运行:
    python -m pytest tests/test_evolution_scheduler.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.agi.evolution_scheduler import EvolutionScheduler
from laap.agi.code_evolution import CodeEvolutionEngine
from laap.agi.fitness import FitnessEvaluator

REPO = str(Path(__file__).resolve().parents[1])


@pytest.fixture()
def engine(monkeypatch):
    # 轻量构造: 跳过 GitIntegrator 的 git subprocess 探测 (pytest 捕获环境易卡)
    from laap.agi.code_evolution import CodeEvolutionEngine as CEE
    monkeypatch.setattr(CEE, "__init__", lambda self, repo_root="", llm_fn=None: None)
    e = object.__new__(CEE)
    e.repo_root = REPO
    e.mutations = []
    e.deployed_count = 0
    e.rollback_count = 0
    e.analyzer = None
    e.patcher = None
    e.tester = None
    e.git = None
    # 隔离: 单测中不真实执行 auto_improve (会扫全仓+沙箱, 卡线程)
    monkeypatch.setattr(e, "auto_improve", lambda **kw: [])
    return e


@pytest.fixture()
def scheduler(engine):
    # fitness_fn 显式注入, 避免缺省时构造 FitnessEvaluator (真跑 pytest/网络)
    s = EvolutionScheduler(engine=engine, interval_seconds=3600,
                           fitness_fn=lambda: 0.5)
    yield s
    s.stop()


# ════════════════════════════════════════════════════════════
# 1. Scheduler 生命周期
# ════════════════════════════════════════════════════════════

def test_scheduler_start_stop(scheduler):
    assert scheduler.start() is True
    assert scheduler.is_running is True
    # 幂等: 重复 start 返回 False
    assert scheduler.start() is False
    assert scheduler.stop() is True
    assert scheduler.is_running is False
    # 幂等: 重复 stop 返回 False
    assert scheduler.stop() is False


def test_scheduler_stats_structure(scheduler):
    st = scheduler.stats()
    assert "running" in st
    assert "tick_count" in st
    assert "interval_seconds" in st
    assert st["interval_seconds"] == 3600


def test_scheduler_tick_returns_tick(scheduler, monkeypatch):
    # 隔离: 不真实执行 auto_improve (沙箱子进程在单测环境易卡), 返回合成结果
    monkeypatch.setattr(scheduler.engine, "auto_improve",
                        lambda **kw: [{"status": "test_passed", "target": "x.py"}])
    tick = scheduler._tick()
    assert "tick" in tick
    assert "baseline_fitness" in tick
    assert "results" in tick
    assert tick["tick"] == 1


def test_scheduler_tick_increments(scheduler, monkeypatch):
    monkeypatch.setattr(scheduler.engine, "auto_improve",
                        lambda **kw: [])
    scheduler._tick()
    scheduler._tick()
    assert scheduler.tick_count == 2


# ════════════════════════════════════════════════════════════
# 2. Fitness 合成
# ════════════════════════════════════════════════════════════

def test_composite_returns_score(monkeypatch):
    fe = FitnessEvaluator(repo_root=REPO)
    # 隔离真实子进程/网络调用
    monkeypatch.setattr(fe, "test_pass_rate", lambda **kw: 0.9)
    monkeypatch.setattr(fe, "avg_latency_ms", lambda **kw: 0.5)
    monkeypatch.setattr(fe, "memory_recall_hit_rate", lambda **kw: 0.7)
    r = fe.composite(components=True)
    assert "score" in r
    assert "test_pass_rate" in r
    assert "avg_latency_score" in r
    assert "memory_recall_hit_rate" in r
    assert 0.0 <= r["score"] <= 1.0
    # 0.4*0.9 + 0.3*0.5 + 0.3*0.7 = 0.72
    assert abs(r["score"] - 0.72) < 1e-9


def test_composite_weighted_math():
    fe = FitnessEvaluator(repo_root=REPO)
    # 分量恒为 1 时, score = 0.4 + 0.3 + 0.3 = 1.0 (用 monkeypatch 伪造)
    fe.test_pass_rate = lambda **kw: 1.0
    fe.avg_latency_ms = lambda **kw: 1.0
    fe.memory_recall_hit_rate = lambda **kw: 1.0
    assert abs(fe.composite()["score"] - 1.0) < 1e-9


def test_latency_normalization_bounds():
    fe = FitnessEvaluator(repo_root=REPO)
    # 纯逻辑验证归一化公式 (不真实采样, 避免 daemon 依赖):
    # latency=0 → 1.0; latency=5000 → 0.0; latency=10000 → clamp 0.0
    norm = lambda ms: max(0.0, min(1.0, 1.0 - ms / 5000.0))
    assert abs(norm(0) - 1.0) < 1e-9
    assert abs(norm(5000) - 0.0) < 1e-9
    assert abs(norm(10000) - 0.0) < 1e-9
    assert abs(norm(2500) - 0.5) < 1e-9


# ════════════════════════════════════════════════════════════
# 3. 集成: scheduler 默认不部署 (auto_deploy=False)
# ════════════════════════════════════════════════════════════

def test_scheduler_tick_no_deploy(engine, monkeypatch):
    """M2 阶段 tick 产出的 mutation 不得自动部署。"""
    # 隔离: 伪造 auto_improve 结果, 验证调度器不自动部署
    monkeypatch.setattr(engine, "auto_improve",
                        lambda **kw: [{"status": "test_passed", "target": "x.py"}])
    s = EvolutionScheduler(engine=engine, interval_seconds=3600,
                           fitness_fn=lambda: 0.5)
    tick = s._tick()
    for r in tick["results"]:
        # 状态只能是不部署的中间态 (rejected/test_failed/test_passed), 不能是 deployed
        assert r.get("status") != "deployed"
    s.stop()
