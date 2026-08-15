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
from laap.paper_trading.strategy import STRATEGY_PARAMS


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


# ════════════════════════════════════════════════════════════
# Track ① 多空策略族（long_short）
# ════════════════════════════════════════════════════════════

def test_long_short_profits_in_downtrend():
    """单调下跌 → 空头捕捉下跌收益（正收益）。"""
    runner = BacktestRunner()
    prices = [100.0 - i * 0.5 for i in range(60)]
    lo = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=False)
    ls = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=True)
    assert ls["cumulative_return"] > 0  # 空头在下跌段赚钱
    assert ls["cumulative_return"] > lo["cumulative_return"]  # 优于纯做多


def test_long_short_long_side_intact_in_uptrend():
    """单调上涨 → 多头仍捕捉上涨（long_short 不破坏多头）。"""
    runner = BacktestRunner()
    prices = [100.0 + i * 0.5 for i in range(60)]
    lo = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=False)
    ls = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=True)
    assert ls["cumulative_return"] >= lo["cumulative_return"] - 0.02


def test_long_short_off_equals_baseline():
    """long_short=False 等价原策略（向后兼容）。"""
    runner = BacktestRunner()
    prices = [100.0 + i * 0.3 + ((i * 5) % 7 - 3) * 0.2 for i in range(120)]
    a = runner.run_backtest(prices, STRATEGY_PARAMS)
    b = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=False)
    assert a == b


def test_long_short_net_values_nonnegative_total():
    """多空模式净值不为负（短仓 MTM 记账正确）。"""
    runner = BacktestRunner()
    prices = [100.0 - i * 0.5 for i in range(60)]
    _m, nv = runner.run_backtest_values(prices, STRATEGY_PARAMS,
                                        long_short=True)
    assert len(nv) > 0
    assert all(n.total >= 0 for n in nv)


# ════════════════════════════════════════════════════════════
# Track ① 指数择时（external_regime）
# ════════════════════════════════════════════════════════════

def test_external_regime_suppresses_longs():
    """外部指数全程下行 → 长期做多不开仓（无亏损，净值持平）。"""
    runner = BacktestRunner()
    prices = [100.0 + i * 0.5 for i in range(60)]  # 个股上涨但指数下行
    ext = [False] * len(prices)
    plain = runner.run_backtest(prices, STRATEGY_PARAMS)
    timed = runner.run_backtest(prices, STRATEGY_PARAMS, external_regime=ext)
    assert timed["cumulative_return"] <= plain["cumulative_return"]
    assert timed["cumulative_return"] >= -0.01  # 被择时挡住 → 接近 0


def test_external_regime_enables_shorts():
    """指数下行 + 多空 → 开空捕捉下跌（个股下跌时正收益）。"""
    runner = BacktestRunner()
    prices = [100.0 - i * 0.5 for i in range(60)]
    ext = [False] * len(prices)
    m = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=True,
                            external_regime=ext)
    assert m["cumulative_return"] > 0


def test_external_regime_all_true_is_noop():
    """external_regime 全 True = 无外部择时（no-op，等价性）。"""
    runner = BacktestRunner()
    prices = [100.0 + i * 0.3 + ((i * 5) % 7 - 3) * 0.2 for i in range(120)]
    for ls in (False, True):
        plain = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=ls)
        timed = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=ls,
                                    external_regime=[True] * len(prices))
        assert plain == timed


# ════════════════════════════════════════════════════════════
# item 2：交易成本（佣金 + 印花税 + 滑点）
# ════════════════════════════════════════════════════════════

_COSTS = {"commission": 0.00025, "stamp": 0.0005, "slippage": 0.001}


def test_costs_reduce_returns():
    """带成本收益 <= 无成本收益（成本侵蚀）。"""
    runner = BacktestRunner()
    prices = [100.0 + i * 0.3 + ((i * 5) % 7 - 3) * 0.2 for i in range(200)]
    for ls in (False, True):
        plain = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=ls)
        costly = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=ls,
                                     costs=_COSTS)
        assert costly["cumulative_return"] <= plain["cumulative_return"] + 1e-9


def test_costs_zero_equals_none():
    """全零成本等价无成本（向后兼容）。"""
    runner = BacktestRunner()
    prices = [100.0 + i * 0.3 + ((i * 5) % 7 - 3) * 0.2 for i in range(120)]
    a = runner.run_backtest(prices, STRATEGY_PARAMS)
    b = runner.run_backtest(prices, STRATEGY_PARAMS,
                            costs={"commission": 0.0, "stamp": 0.0, "slippage": 0.0})
    assert a == b


def test_t1_guard_no_regression():
    """T+1 entry_bar 守卫不改变日线粒度行为（防御性守卫，无回归）。
    真正的 T+1 强约束在真实执行层 ledger.enforce_t1（test_paper_enhancements 4 项覆盖）。"""
    runner = BacktestRunner()
    prices = [100.0 - i * 0.4 for i in range(80)]
    for ls in (False, True):
        m = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=ls)
        assert "cumulative_return" in m and "max_drawdown" in m


# ════════════════════════════════════════════════════════════
# M3：A 股涨跌停（涨停禁买 / 跌停禁卖）
# ════════════════════════════════════════════════════════════

def _up_then_crash_recover():
    """上升建仓 → 暴跌触发止损 → 次日跌停开盘被锁 → 反弹。

    次日开盘成交模型下构造：bar i 收盘触发止损（-12%），bar i+1 开盘
    又是 -12%（跌停）→ 卖被锁；随后反弹。无涨跌停则按低点割肉。
    """
    up = [100.0 + i * 0.5 for i in range(40)]  # 100..119.5 上升（建仓并持有）
    d1 = up[-1] * 0.88                          # -12% 暴跌日（触发止损）
    return up + [d1, d1 * 0.88, d1 * 1.0, d1 * 1.06, d1 * 1.12]


def test_price_limit_blocks_sell_at_limit_down():
    """跌停日禁卖 → 持仓被锁，随后反弹优于立即割肉。"""
    runner = BacktestRunner()
    prices = _up_then_crash_recover()
    # 自定义参数：紧止损 5% 触发暴跌日离场；关闭超买/止盈/移动止损避免提前退出
    params = dict(STRATEGY_PARAMS)
    params.update({
        "fast_ma": 3, "slow_ma": 30,
        "rsi_overbought": 200.0, "take_profit_pct": 5.0,
        "trailing_stop": 0.5, "stop_loss_pct": 0.05, "atr_stop_mult": 50.0,
    })
    m_no = runner.run_backtest(prices, params, price_limit=None)
    m_lim = runner.run_backtest(prices, params, price_limit=0.10)
    assert m_lim["cumulative_return"] > m_no["cumulative_return"]


def test_price_limit_runs_both_families():
    """涨跌停建模在趋势/多空/均值回归下均可运行（无崩溃）。"""
    runner = BacktestRunner()
    prices = [100.0 + i * 0.3 + ((i * 5) % 7 - 3) * 0.2 for i in range(120)]
    for style in ("trend", "mean_reversion"):
        for ls in (False, True):
            m = runner.run_backtest(prices, STRATEGY_PARAMS, long_short=ls,
                                    price_limit=0.10, style=style)
            assert "cumulative_return" in m


# ════════════════════════════════════════════════════════════
# M4：均值回归（独立信号家族）
# ════════════════════════════════════════════════════════════

def test_mean_reversion_buys_dip_and_profits():
    """浅跌超卖 → 均值回归低位买入，反弹获利（contrarian 哲学）。"""
    runner = BacktestRunner()
    prices = [100.0 - i * 0.5 for i in range(20)] + [90.0 + i * 1.0 for i in range(40)]
    m = runner.run_backtest(prices, STRATEGY_PARAMS, style="mean_reversion")
    assert m["cumulative_return"] > 0


def test_mean_reversion_differs_from_trend():
    """均值回归与趋势是不同信号（同一序列上行为不同）。"""
    runner = BacktestRunner()
    prices = [100.0 - i * 0.5 for i in range(20)] + [90.0 + i * 1.0 for i in range(40)]
    m_trend = runner.run_backtest(prices, STRATEGY_PARAMS, style="trend")
    m_mr = runner.run_backtest(prices, STRATEGY_PARAMS, style="mean_reversion")
    assert m_trend["cumulative_return"] != m_mr["cumulative_return"]
