# -*- coding: utf-8 -*-
"""Step 1 滚动 Walk-Forward 验证器测试。

覆盖:
  - build_folds：800/400/80 → 5 段、test 互不重叠、数据不足返回空
  - run_fold：返回完整结构化结果（ok/reason/test_metrics/z/excess）
  - run：汇总字段齐全，verdict ∈ {STABLE_PASS, WEAK_PASS, FAIL}
  - 可复现性：同 seed 两次 run_fold 的 best_params 一致
  - _verdict 判定逻辑（决策 2）

全部用合成数据（确定性），不依赖真实 K 线/网络/DB。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.walkforward import (
    WalkForwardValidator, Z_THRESHOLD,
    _two_sided_p, _mtc_pass_flags, _regime_class,
)


@pytest.fixture
def synth_prices():
    return [100.0 + i * 0.5 + ((i * 7) % 11 - 5) * 0.3 for i in range(800)]


# ════════════════════════════════════════════════════════════
# 滚动段构造（决策 1：800 天 × 5 段）
# ════════════════════════════════════════════════════════════

def test_build_folds_5_folds_800_400_80():
    folds = WalkForwardValidator.build_folds(800, 400, 80)
    assert len(folds) == 5
    # 各段 train/test 边界正确
    assert folds[0] == (0, 400, 400, 480)
    assert folds[1] == (80, 480, 480, 560)
    assert folds[4] == (320, 720, 720, 800)
    # test 互不重叠
    tests = [f[2:] for f in folds]
    seen = set()
    for t in tests:
        assert not (set(range(t[0], t[1])) & seen)
        seen |= set(range(t[0], t[1]))


def test_build_folds_insufficient_data():
    assert WalkForwardValidator.build_folds(300, 400, 80) == []


def test_build_folds_custom_step():
    # step=40 → 更多段（rolling origin 更密）
    folds = WalkForwardValidator.build_folds(800, 400, 80, step=40)
    assert len(folds) == 9


# ════════════════════════════════════════════════════════════
# 单段
# ════════════════════════════════════════════════════════════

def test_run_fold_structure(synth_prices):
    v = WalkForwardValidator()
    fold = v.build_folds(len(synth_prices), 400, 80)[0]
    r = v.run_fold(synth_prices, fold, n_samples=20, seed=42,
                   baseline_samples=20)
    for key in ("ok", "reason", "best_params", "train_metrics",
                "test_metrics", "z", "buy_hold", "excess"):
        assert key in r, f"缺少 {key}"
    assert r["ok"] in (True, False)
    assert set(r["best_params"].keys()) >= {"fast_ma", "slow_ma", "rsi_period"}
    assert r["test_metrics"]["cumulative_return"] is not None
    assert r["z"] is not None  # significance=True 时应有 z


def test_run_fold_reproducible(synth_prices):
    v = WalkForwardValidator()
    fold = v.build_folds(len(synth_prices), 400, 80)[0]
    r1 = v.run_fold(synth_prices, fold, n_samples=20, seed=7, baseline_samples=20)
    r2 = v.run_fold(synth_prices, fold, n_samples=20, seed=7, baseline_samples=20)
    assert r1["best_params"] == r2["best_params"]
    assert r1["test_metrics"] == r2["test_metrics"]


def test_run_fold_no_significance_z_none(synth_prices):
    v = WalkForwardValidator()
    fold = v.build_folds(len(synth_prices), 400, 80)[0]
    r = v.run_fold(synth_prices, fold, n_samples=20, seed=1,
                   significance=False)
    assert r["z"] is None


# ════════════════════════════════════════════════════════════
# 全量汇总
# ════════════════════════════════════════════════════════════

def test_run_summary_fields(synth_prices):
    v = WalkForwardValidator()
    data = {"T1": {"closes": synth_prices}}
    report = v.run(data, train_size=400, test_size=80,
                   n_samples=20, seed=42, baseline_samples=20)
    s = report["summary"]
    for key in ("n_symbols", "n_folds_total", "pass_count", "pass_rate",
                "mean_oos_return", "median_oos_return", "positive_folds",
                "median_z", "mean_excess", "verdict", "verdict_reason"):
        assert key in s, f"缺少 {key}"
    assert s["n_folds_total"] == 5
    assert s["verdict"] in ("STABLE_PASS", "WEAK_PASS", "FAIL")
    assert len(report["symbols"]) == 1


def test_run_skips_short_symbols(synth_prices):
    v = WalkForwardValidator()
    data = {"SHORT": {"closes": synth_prices[:200]}}  # 不足 train+test
    report = v.run(data, train_size=400, test_size=80,
                   n_samples=10, seed=1, baseline_samples=10)
    assert report["summary"]["verdict"] == "NO_DATA"
    assert report["symbols"] == []


# ════════════════════════════════════════════════════════════
# 判定逻辑（决策 2）
# ════════════════════════════════════════════════════════════

def test_verdict_stable_pass():
    s = {"pass_rate": 0.8, "median_oos_return": 0.02,
         "median_z": 2.5, "pass_threshold": 0.6}
    v, r = WalkForwardValidator._verdict(s, 0.6)
    assert v == "STABLE_PASS"


def test_verdict_weak_pass_when_z_low():
    s = {"pass_rate": 0.5, "median_oos_return": 0.01,
         "median_z": 1.2, "pass_threshold": 0.6}
    v, r = WalkForwardValidator._verdict(s, 0.6)
    assert v == "WEAK_PASS"


def test_verdict_fail_when_return_nonpositive():
    s = {"pass_rate": 0.5, "median_oos_return": -0.01,
         "median_z": 2.5, "pass_threshold": 0.6}
    v, r = WalkForwardValidator._verdict(s, 0.6)
    assert v == "FAIL"


def test_z_threshold_constant():
    assert Z_THRESHOLD == 1.96


# ════════════════════════════════════════════════════════════
# item 1：多重检验控制 + item 3：跨周期分段
# ════════════════════════════════════════════════════════════

def test_two_sided_p():
    assert _two_sided_p(0.0) == 1.0
    assert _two_sided_p(1.96) == pytest.approx(0.05, abs=0.002)


def test_mtc_bonferroni_stricter():
    # 15 段 Bonferroni：z=1.96（p≈0.05）不通过；z=3.2 通过（p≈0.0014 < 0.05/15）
    flags = _mtc_pass_flags([1.96] * 15, "bonferroni")
    assert not any(flags)
    flags2 = _mtc_pass_flags([3.2] * 15, "bonferroni")
    assert all(flags2)


def test_mtc_fdr_flags():
    # 4 段 FDR(BH q=0.05)：仅强信号 z=4.0 通过，其余 z=1.5~1.7 不达阈值
    zs = [1.5, 1.6, 1.7, 4.0]
    flags = _mtc_pass_flags(zs, "fdr")
    assert flags[-1] is True
    assert not any(flags[:-1])


def test_regime_class():
    assert _regime_class(0.06) == "bull"
    assert _regime_class(-0.06) == "bear"
    assert _regime_class(0.0) == "range"


def test_run_mtc_fdr_summary(synth_prices):
    v = WalkForwardValidator()
    data = {"T1": {"closes": synth_prices}}
    report = v.run(data, train_size=400, test_size=80,
                   n_samples=20, seed=42, baseline_samples=20, mtc="fdr")
    s = report["summary"]
    assert s["mtc"] == "fdr"
    assert "regime_stats" in s
    # 跨周期分段字段完整
    for cls, rs in s["regime_stats"].items():
        assert {"n", "pass", "pass_rate", "mean_oos", "mean_excess"} <= set(rs)


def test_valid_ratio_gate_stricter(synth_prices):
    """M1 验证段门禁：开启后通过数 <= 关闭（选参须在未参与选参的 verify 段正收益）。"""
    v = WalkForwardValidator()
    data = {"T1": {"closes": synth_prices}}
    r_no = v.run(data, train_size=400, test_size=80,
                 n_samples=20, seed=42, baseline_samples=20)
    r_yes = v.run(data, train_size=400, test_size=80,
                  n_samples=20, seed=42, baseline_samples=20, valid_ratio=0.8)
    assert r_yes["summary"]["pass_count"] <= r_no["summary"]["pass_count"]
    # 每个 fold 带 valid_ok 字段（verify 段结果可审计）
    for s in r_yes["symbols"]:
        for f in s["folds"]:
            assert "valid_ok" in f and "valid_reason" in f
