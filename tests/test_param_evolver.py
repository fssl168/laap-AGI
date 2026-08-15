# -*- coding: utf-8 -*-
"""阶段 2 多因子策略 + 参数进化器测试。

覆盖:
  - backtest_runner 多因子引擎（指标长度对齐、止损止盈、仓位）
  - ParamSpace（采样/变异/交叉、网格爆炸防护）
  - ParamEvolver（随机/遗传搜索的可复现性）
  - QuantEvolutionEngine.evolve_params / evolve_with_llm

全部用合成数据（确定性），不依赖真实 K 线/网络/DB。
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from laap.paper_trading.backtest_runner import (
    BacktestRunner, _rsi, _sma, _atr, _momentum, split_series)
from laap.paper_trading.param_evolver import (
    ParamEvolver, ParamSpace, PARAM_SPACE, _is_int_param)
from laap.paper_trading.strategy import STRATEGY_PARAMS


@pytest.fixture
def synth_prices():
    """合成趋势+噪声序列（确定性）。"""
    return [100.0 + i * 0.5 + ((i * 7) % 11 - 5) * 0.3 for i in range(200)]


@pytest.fixture
def runner():
    return BacktestRunner()


# ════════════════════════════════════════════════════════════
# 指标长度对齐
# ════════════════════════════════════════════════════════════

def test_indicator_lengths_match(synth_prices):
    n = len(synth_prices)
    assert len(_sma(synth_prices, 5)) == n
    assert len(_rsi(synth_prices, 14)) == n
    assert len(_momentum(synth_prices, 10)) == n
    assert len(_atr(None, synth_prices, 14)) == n


def test_rsi_bounds(synth_prices):
    rsi = _rsi(synth_prices, 14)
    vals = [v for v in rsi if v is not None]
    assert vals, "RSI 应有有效值"
    assert all(0 <= v <= 100 for v in vals)


def test_atr_positive(synth_prices):
    atr = _atr(None, synth_prices, 14)
    vals = [v for v in atr if v is not None]
    assert vals and all(v >= 0 for v in vals)


# ════════════════════════════════════════════════════════════
# 多因子回测
# ════════════════════════════════════════════════════════════

def test_multi_factor_backtest_runs(runner, synth_prices):
    m = runner.run_backtest(synth_prices, STRATEGY_PARAMS)
    assert "cumulative_return" in m
    assert "sharpe_ratio" in m
    assert "score" in m
    assert 0.0 <= m["score"] <= 1.0


def test_multi_factor_split(runner, synth_prices):
    m_full = runner.run_backtest(synth_prices, STRATEGY_PARAMS)
    m_seg = runner.run_backtest(synth_prices, STRATEGY_PARAMS, split=(0, 100))
    assert m_seg["score"] >= 0


def test_oos_gate_fail_closed(runner, synth_prices):
    # 训练段正收益，OOS 段负收益 → 门禁应 fail-closed
    train = {"cumulative_return": 0.1, "sharpe_ratio": 0.5}
    oos = {"cumulative_return": -0.05, "sharpe_ratio": 0.1}
    ok, reason = runner.oos_gate(train, oos)
    assert not ok
    assert "cumulative_return" in reason


def test_defensive_fast_slower_than_slow(runner, synth_prices):
    # fast_ma >= slow_ma 时防御降级，不抛异常
    bad = dict(STRATEGY_PARAMS, fast_ma=30, slow_ma=10)
    m = runner.run_backtest(synth_prices, bad)
    assert m["score"] >= 0


# ════════════════════════════════════════════════════════════
# ParamSpace
# ════════════════════════════════════════════════════════════

def test_param_space_sample_in_bounds():
    ps = ParamSpace()
    rng = random.Random(42)
    for _ in range(50):
        p = ps.sample(rng)
        assert set(p.keys()) == set(PARAM_SPACE.keys())
        for k, (lo, hi, _) in PARAM_SPACE.items():
            assert lo <= p[k] <= hi


def test_param_space_int_params_stay_int():
    from laap.paper_trading.param_evolver import _INT_PARAMS
    ps = ParamSpace()
    rng = random.Random(7)
    for _ in range(50):
        p = ps.sample(rng)
        for k in _INT_PARAMS:
            assert float(p[k]).is_integer(), f"{k}={p[k]} 应为整数"


def test_param_space_mutate_in_bounds():
    ps = ParamSpace()
    rng = random.Random(1)
    base = ps.sample(rng)
    mutated = ps.mutate(base, rng, rate=1.0)
    for k, (lo, hi, _) in PARAM_SPACE.items():
        assert lo <= mutated[k] <= hi


def test_param_space_grid_explosion_guard():
    ps = ParamSpace()
    with pytest.raises(ValueError, match="改用 method='random' 或 'genetic'"):
        ps.grid(max_combos=100)


# ════════════════════════════════════════════════════════════
# ParamEvolver 可复现性
# ════════════════════════════════════════════════════════════

def test_random_search_reproducible(synth_prices):
    ev = ParamEvolver()
    r1 = ev.evolve(synth_prices, method="random", n_samples=50, seed=42)
    r2 = ev.evolve(synth_prices, method="random", n_samples=50, seed=42)
    assert r1["best_params"] == r2["best_params"]
    assert r1["best_train"]["score"] == r2["best_train"]["score"]
    assert r1["gate"]["ok"] == r2["gate"]["ok"]


def test_genetic_search_reproducible(synth_prices):
    ev = ParamEvolver()
    g1 = ev.evolve(synth_prices, method="genetic", population=8,
                   generations=5, seed=42)
    g2 = ev.evolve(synth_prices, method="genetic", population=8,
                   generations=5, seed=42)
    assert g1["best_params"] == g2["best_params"]


def test_evolve_returns_full_structure(synth_prices):
    ev = ParamEvolver()
    r = ev.evolve(synth_prices, method="random", n_samples=30, seed=1)
    for key in ("method", "best_params", "best_train", "best_oos",
                "gate", "candidates", "n_candidates"):
        assert key in r, f"缺少 {key}"
    assert r["n_candidates"] == 30
    assert r["gate"]["ok"] in (True, False)


def test_param_evolver_score_uses_compute_trade_fitness(synth_prices):
    """阶段 3.1 契约：param_evolver 的 score == compute_trade_fitness 组合分。"""
    from laap.paper_trading.backtest_runner import (
        _run_multi_factor, DEFAULT_COSTS)
    from laap.paper_trading.trade_fitness import compute_trade_fitness
    ev = ParamEvolver()
    scored = ev._score(synth_prices, STRATEGY_PARAMS)
    # _score 走 run_backtest 默认 A 股成本，此处显式对齐成本口径
    nv = _run_multi_factor(synth_prices, STRATEGY_PARAMS, costs=DEFAULT_COSTS)
    tf = compute_trade_fitness(nv)
    assert scored["score"] == tf["score"]
    assert scored["cumulative_return"] == tf["cumulative_return"]


def test_genetic_beats_or_matches_random(synth_prices):
    """遗传搜索 score 应不低于随机搜索（收敛性弱断言）。"""
    ev = ParamEvolver()
    r = ev.evolve(synth_prices, method="random", n_samples=100, seed=42)
    g = ev.evolve(synth_prices, method="genetic", population=12,
                  generations=8, seed=42)
    assert g["best_train"]["score"] >= r["best_train"]["score"] - 0.05


# ════════════════════════════════════════════════════════════
# QuantEvolutionEngine 参数进化
# ════════════════════════════════════════════════════════════

def _make_qe(synth_prices, db=None):
    from laap.paper_trading.quant_evolution import QuantEvolutionEngine
    # 用空壳 code_evo_engine（只测参数进化路径，不测代码级进化）
    class _Fake:
        def __init__(self):
            self.audit = None
        def stats(self):
            return {}
    return QuantEvolutionEngine(_Fake(), BacktestRunner(), synth_prices, db=db)


def test_evolve_params_returns_gate(synth_prices):
    qe = _make_qe(synth_prices)
    r = qe.evolve_params(method="random", n_samples=30, seed=42)
    assert "best_params" in r
    assert "gate" in r
    assert r["gate"]["ok"] in (True, False)


def test_evolve_with_llm_none_equals_params(synth_prices):
    qe = _make_qe(synth_prices)
    r = qe.evolve_with_llm(llm_fn=None, method="random", n_samples=30, seed=42)
    assert r["llm_refined"] is None


def test_evolve_with_llm_refines(synth_prices):
    qe = _make_qe(synth_prices)
    # 简单 LLM 桩：返回相同参数（确保不崩 + 返回结构完整）
    def fake_llm(params, train, ctx):
        return dict(params)
    r = qe.evolve_with_llm(llm_fn=fake_llm, method="random",
                           n_samples=30, seed=42)
    assert r["llm_refined"] is not None
    assert "gate" in r["llm_refined"]
    assert r["llm_refined"]["params"] == r["best_params"]


def test_evolve_with_llm_bad_output(synth_prices):
    qe = _make_qe(synth_prices)
    r = qe.evolve_with_llm(llm_fn=lambda *a, **k: None,
                           method="random", n_samples=20, seed=1)
    assert "error" in r["llm_refined"] or "error" in (r.get("llm_refined") or {})


def test_evolve_with_llm_uses_injected_llm_fn(synth_prices):
    """阶段 3.3：构造时注入 llm_fn，evolve_with_llm(llm_fn=None) 回退使用。"""
    from laap.paper_trading.llm_refine import build_llm_refine_fn
    from laap.paper_trading.quant_evolution import QuantEvolutionEngine

    class _Fake:
        def __init__(self):
            self.audit = None
        def stats(self):
            return {}

    def fake_llm_call(prompt, system="", max_tokens=500):
        return {"text": '{"fast_ma": 6, "slow_ma": 30}'}

    qe = QuantEvolutionEngine(_Fake(), BacktestRunner(), synth_prices,
                              llm_fn=build_llm_refine_fn(fake_llm_call))
    r = qe.evolve_with_llm(method="random", n_samples=20, seed=42)
    assert r["llm_refined"] is not None
    assert r["llm_refined"]["params"]["fast_ma"] == 6.0
    assert qe.stats()["llm_refine_available"] is True
