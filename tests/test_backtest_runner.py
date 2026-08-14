"""P2 样本外回测 runner 测试（时间切分 / 回放 / OOS 门禁）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.backtest_runner import (
    BacktestRunner,
    split_series,
    _run_ma_cross,
)


def test_split_series_time_ordered():
    dates = list(range(10))
    train, valid, oos = split_series(dates, train=0.6, valid=0.2, oos=0.2)
    assert train == [0, 1, 2, 3, 4, 5]
    assert valid == [6, 7]
    assert oos == [8, 9]
    # 三段无重叠、按时间序
    assert train[-1] < valid[0]
    assert valid[-1] < oos[0]


def test_ma_cross_produces_net_values():
    # 趋势价格序列（先涨后跌）
    prices = [100 + i for i in range(10)] + [110 - i for i in range(10)]
    nv = _run_ma_cross(prices, short=2, long=5)
    assert len(nv) > 0
    # 净值非负
    assert all(n.total >= 0 for n in nv)


def test_ma_cross_rejects_bad_params():
    with pytest.raises(ValueError):
        _run_ma_cross([100.0] * 20, short=10, long=5)


def test_run_backtest_returns_metrics():
    runner = BacktestRunner()
    prices = [100 + i * 0.5 for i in range(60)]
    metrics = runner.run_backtest(prices, params={"short": 5, "long": 20})
    assert "score" in metrics
    assert "cumulative_return" in metrics


def test_oos_gate_fails_on_negative_oos():
    runner = BacktestRunner()
    train = {"sharpe_ratio": 1.0, "cumulative_return": 0.1}
    oos_bad = {"sharpe_ratio": 0.5, "cumulative_return": -0.05}
    ok, reason = runner.oos_gate(train, oos_bad)
    assert ok is False
    assert "cumulative_return" in reason


def test_oos_gate_fails_on_sharpe_degraded():
    runner = BacktestRunner()
    train = {"sharpe_ratio": 1.0, "cumulative_return": 0.1}
    oos = {"sharpe_ratio": 0.5, "cumulative_return": 0.05}  # 0.5 < 0.8
    ok, _ = runner.oos_gate(train, oos)
    assert ok is False


def test_oos_gate_passes_when_not_degraded():
    runner = BacktestRunner()
    train = {"sharpe_ratio": 1.0, "cumulative_return": 0.1}
    oos = {"sharpe_ratio": 0.9, "cumulative_return": 0.05}  # 0.9 >= 0.8
    ok, _ = runner.oos_gate(train, oos)
    assert ok is True
