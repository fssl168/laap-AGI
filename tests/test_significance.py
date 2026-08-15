# -*- coding: utf-8 -*-
"""阶段 3.2 门禁升级（统计显著性）测试。

覆盖:
  - significance.mean_std / z_statistic / beats_baseline 数学
  - ParamEvolver.random_baseline（随机基线统计有效）
  - ParamEvolver.evolve(significance=True) 返回显著性字段
  - BacktestRunner.oos_gate 显著性层（显著优于/不显著 分别放行/拒绝）
  - oos_gate 两参向后兼容

全部用合成数据（确定性），不依赖真实 K 线/网络/DB。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from laap.paper_trading.backtest_runner import BacktestRunner
from laap.paper_trading.param_evolver import ParamEvolver
from laap.paper_trading.significance import (
    beats_baseline,
    mean_std,
    z_statistic,
)


@pytest.fixture
def synth_prices():
    return [100.0 + i * 0.5 + ((i * 7) % 11 - 5) * 0.3 for i in range(200)]


# ════════════════════════════════════════════════════════════
# 数学基础
# ════════════════════════════════════════════════════════════

def test_mean_std_basic():
    m, s = mean_std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert m == pytest.approx(5.0)
    assert s == pytest.approx(2.1381, rel=1e-3)  # 样本标准差（n-1）


def test_mean_std_empty():
    m, s = mean_std([])
    assert m == 0.0 and s == 0.0


def test_z_statistic_sign():
    strat = {"mean": 0.01, "std": 0.02, "n": 100}
    base = {"mean": 0.0, "std": 0.02, "n": 100}
    z = z_statistic(strat, base)
    assert z > 0  # 策略优于基线 → 正 z


def test_beats_baseline_significantly_better():
    strat = {"mean": 0.01, "std": 0.02, "n": 100}
    base = {"mean": 0.0, "std": 0.02, "n": 100}
    ok, reason = beats_baseline(strat, base, z_threshold=1.96)
    assert ok is True
    assert "significantly better" in reason


def test_beats_baseline_not_significant():
    strat = {"mean": 0.001, "std": 0.02, "n": 100}
    base = {"mean": 0.0, "std": 0.02, "n": 100}
    ok, reason = beats_baseline(strat, base, z_threshold=1.96)
    assert ok is False
    assert "not significantly better" in reason


def test_beats_baseline_insufficient_samples():
    ok, reason = beats_baseline(
        {"mean": 0.0, "std": 0.0, "n": 1},
        {"mean": 0.0, "std": 0.0, "n": 1},
    )
    assert ok is False
    assert "insufficient samples" in reason


# ════════════════════════════════════════════════════════════
# 随机基线 + 显著性进化
# ════════════════════════════════════════════════════════════

def test_random_baseline_stats_valid(synth_prices):
    ev = ParamEvolver()
    b = ev.random_baseline(synth_prices, split=(100, 200), n_samples=10, seed=42)
    assert b["n"] > 0
    assert math.isfinite(b["mean"])
    assert b["std"] >= 0


def test_evolve_significance_returns_fields(synth_prices):
    ev = ParamEvolver()
    r = ev.evolve(synth_prices, method="random", n_samples=20, seed=1,
                  significance=True, baseline_samples=10)
    assert "significance" in r
    assert r["significance"]["random_baseline"]["n"] > 0
    assert r["significance"]["strategy_stats"]["n"] >= 0
    assert r["gate"]["ok"] in (True, False)


# ════════════════════════════════════════════════════════════
# oos_gate 显著性层 + 向后兼容
# ════════════════════════════════════════════════════════════

def _good_metrics():
    train = {"cumulative_return": 0.1, "sharpe_ratio": 1.0}
    oos = {"cumulative_return": 0.05, "sharpe_ratio": 0.9}
    return train, oos


def test_oos_gate_significance_blocks_when_not_better():
    runner = BacktestRunner()
    train, oos = _good_metrics()
    strat = {"mean": 0.001, "std": 0.02, "n": 100}
    base = {"mean": 0.0, "std": 0.02, "n": 100}
    ok, reason = runner.oos_gate(train, oos, strategy_stats=strat,
                                 random_baseline=base)
    assert ok is False
    assert "not significantly better" in reason


def test_oos_gate_significance_passes_when_better():
    runner = BacktestRunner()
    train, oos = _good_metrics()
    strat = {"mean": 0.01, "std": 0.02, "n": 100}
    base = {"mean": 0.0, "std": 0.02, "n": 100}
    ok, _ = runner.oos_gate(train, oos, strategy_stats=strat,
                            random_baseline=base)
    assert ok is True


def test_oos_gate_two_arg_backward_compat():
    """两参调用等价旧语义（无显著性层）。"""
    runner = BacktestRunner()
    train, oos = _good_metrics()
    ok, _ = runner.oos_gate(train, oos)
    assert ok is True


def test_oos_gate_strict_positive_return():
    """阶段 3.2：OOS 累计收益 严格 > 0（==0 拒绝）。"""
    runner = BacktestRunner()
    train = {"cumulative_return": 0.1, "sharpe_ratio": 1.0}
    oos = {"cumulative_return": 0.0, "sharpe_ratio": 0.9}
    ok, reason = runner.oos_gate(train, oos)
    assert ok is False
    assert "cumulative_return" in reason
