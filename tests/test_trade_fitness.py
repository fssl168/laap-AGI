"""P2 交易适应度测试（收益/夏普/回撤/组合）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.models import PaperNetValue
from laap.paper_trading.trade_fitness import (
    _cumulative_return,
    _sharpe_ratio,
    _max_drawdown,
    compute_trade_fitness,
)


def _nv(totals):
    return [PaperNetValue(ts=float(i), total=t) for i, t in enumerate(totals)]


def test_cumulative_return_rising():
    nv = _nv([100.0, 110.0, 121.0])
    assert _cumulative_return(nv) == pytest.approx(0.21)


def test_cumulative_return_empty_or_single():
    assert _cumulative_return([]) == 0.0
    assert _cumulative_return(_nv([100.0])) == 0.0


def test_sharpe_positive_trend():
    # 稳定上涨 → 夏普 > 0
    nv = _nv([100.0 + i * 1.0 for i in range(20)])
    assert _sharpe_ratio(nv) > 0


def test_sharpe_flat_zero():
    nv = _nv([100.0] * 10)
    assert _sharpe_ratio(nv) == 0.0


def test_max_drawdown():
    nv = _nv([100.0, 120.0, 90.0, 110.0])
    # 峰值 120 → 谷底 90 → dd = 30/120 = 0.25
    assert _max_drawdown(nv) == pytest.approx(0.25)


def test_compute_trade_fitness_bounds():
    nv = _nv([100.0 + i * 0.5 for i in range(30)])
    r = compute_trade_fitness(nv)
    assert 0.0 <= r["score"] <= 1.0
    assert "cumulative_return" in r
    assert "sharpe_ratio" in r
    assert "max_drawdown" in r


def test_compute_trade_fitness_losing():
    nv = _nv([100.0 - i * 1.0 for i in range(20)])
    r = compute_trade_fitness(nv)
    assert r["cumulative_return"] < 0
    assert r["score"] < 0.5
