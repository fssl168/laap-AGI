"""策略模板注册表测试（laap.paper_trading.strategy_templates）。

覆盖：
  1. 注册表枚举 / 未知策略 fail-closed
  2. 每个模板的确定性信号（构造已知序列断言 buy/sell/hold）
  3. 与 BacktestRunner.evaluate_signal 路由集成
  4. 契约回归：multi_factor 默认行为不变
"""

from __future__ import annotations

import pytest

from laap.paper_trading.strategy_templates import (
    STRATEGY_TEMPLATES, list_templates, get_template, evaluate_strategy,
)
from laap.paper_trading.backtest_runner import BacktestRunner

# 上穿样本：末段 MA5 上穿 MA10（快速拉升）
GOLDEN_UP = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.8, 11.0, 11.4, 11.9, 12.5, 13.2]
# 下穿样本：末段 MA5 下穿 MA10（快速下跌）
GOLDEN_DOWN = [13.0, 12.9, 12.8, 12.7, 12.6, 12.5, 12.4, 12.2, 12.0, 11.6, 11.1, 10.5, 9.8]


def _ohlcv(closes, vol_mult=1.0, tail_vol=None):
    """构造对齐的 ohlcv：(open, close, high, low, volume)。

    vol_mult: 基础量能倍数；tail_vol: 末根成交量（None=按 vol_mult 递推）。
    """
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        high = max(o, c) * 1.01
        low = min(o, c) * 0.99
        vol = 1000.0 * vol_mult * (1.0 + i * 0.01)
        out.append((o, c, high, low, vol))
    if tail_vol is not None and out:
        o, c, h, l, _v = out[-1]
        out[-1] = (o, c, h, l, float(tail_vol))
    return out


class TestRegistry:
    def test_registry_contains_core_templates(self):
        names = list_templates()
        assert names[0] == "multi_factor"
        for n in ("golden_cross", "rsi_reversal", "boll_breakout",
                  "macd_momentum", "volume_breakout", "shrink_pullback",
                  "mean_reversion"):
            assert n in names

    def test_unknown_strategy_fail_closed(self):
        action, reason = evaluate_strategy("no_such_strategy", GOLDEN_UP)
        assert action == "hold"
        assert "unknown strategy" in reason

    def test_get_template_roundtrip(self):
        t = get_template("golden_cross")
        assert t is not None and t.name == "golden_cross"
        assert get_template("multi_factor") is None  # 内建，不走注册表


class TestGoldenCross:
    def test_buy_on_cross_up(self):
        action, reason = evaluate_strategy("golden_cross", GOLDEN_UP,
                                           {"fast_ma": 5, "slow_ma": 10})
        assert action == "buy"
        assert "cross" in reason

    def test_sell_on_cross_down(self):
        action, reason = evaluate_strategy("golden_cross", GOLDEN_DOWN,
                                           {"fast_ma": 5, "slow_ma": 10},
                                           position_held=True)
        assert action == "sell"

    def test_hold_without_signal(self):
        flat = [10.0] * 15
        action, _r = evaluate_strategy("golden_cross", flat, {"fast_ma": 5, "slow_ma": 10})
        assert action == "hold"


class TestRsiReversal:
    def test_buy_oversold(self):
        # 持续下跌 → RSI 进入超卖
        down = [10.0 - i * 0.15 for i in range(30)]
        action, reason = evaluate_strategy("rsi_reversal", down)
        assert action == "buy"
        assert "rsi_reversal" in reason

    def test_sell_overbought(self):
        # 持续上涨 → RSI 超买，持仓中卖出
        up = [10.0 + i * 0.15 for i in range(30)]
        action, reason = evaluate_strategy("rsi_reversal", up, position_held=True)
        assert action == "sell"
        assert "rsi_reversal exit" in reason

    def test_hold_neutral(self):
        flat = [10.0 + (0.05 if i % 2 else -0.05) for i in range(30)]
        action, _r = evaluate_strategy("rsi_reversal", flat)
        assert action == "hold"


class TestBollBreakout:
    def test_buy_cross_up_upper(self):
        # 平稳后暴力拉升 → 收盘上穿上轨
        series = [10.0] * 20 + [10.2, 10.6, 11.2]
        action, reason = evaluate_strategy("boll_breakout", series)
        assert action == "buy"
        assert "upper" in reason

    def test_sell_cross_down_lower(self):
        series = [10.0] * 20 + [9.8, 9.4, 8.8]
        action, _r = evaluate_strategy("boll_breakout", series, position_held=True)
        assert action == "sell"


class TestMacdMomentum:
    def test_buy_hist_cross_zero(self):
        # 深跌后单根暴涨 → MACD 柱在最后两根之间由负转正（需 ≥40 根预热）
        series = [10.0 - i * 0.1 for i in range(45)] + [13.0]
        action, reason = evaluate_strategy("macd_momentum", series)
        assert action == "buy"
        assert "0" in reason

    def test_sell_hist_cross_zero(self):
        # 暴涨后单根暴跌 → MACD 柱在最后两根之间由正转负
        series = [10.0 + i * 0.1 for i in range(45)] + [7.0]
        action, _r = evaluate_strategy("macd_momentum", series, position_held=True)
        assert action == "sell"


class TestVolumeBreakout:
    def test_buy_on_high_volume_breakout(self):
        closes = [10.0 + i * 0.02 for i in range(22)]
        closes[-1] = 12.0  # 严格突破 20 日高点（原 11.5 与高点相等不触发）
        # 末根放量 3 倍于前 20 日均量（突破确认）
        avg_vol = sum(1000.0 * (1.0 + i * 0.01) for i in range(20)) / 20
        ohlcv = _ohlcv(closes, vol_mult=1.0, tail_vol=avg_vol * 3.0)
        action, reason = evaluate_strategy("volume_breakout", closes,
                                           {"breakout_window": 20,
                                            "volume_ma_window": 20,
                                            "volume_ratio_min": 2.0},
                                           ohlcv=ohlcv)
        assert action == "buy"
        assert "vol_confirm" in reason

    def test_hold_without_volume(self):
        closes = [10.0 + i * 0.02 for i in range(22)]
        closes[-1] = 11.5
        ohlcv = _ohlcv(closes, vol_mult=1.0)  # 未放量
        action, _r = evaluate_strategy("volume_breakout", closes,
                                       {"breakout_window": 20,
                                        "volume_ma_window": 20,
                                        "volume_ratio_min": 2.0},
                                       ohlcv=ohlcv)
        assert action == "hold"


class TestShrinkPullback:
    def test_buy_on_pullback(self):
        # 上升趋势后小幅回踩 MA5
        base = [10.0 + i * 0.05 for i in range(25)]
        series = base + [base[-1] + 0.02]  # 最后一 bar 贴近均线
        # 末根缩量至前 20 日均量 40%（缩量回踩确认）
        avg_vol = sum(1000.0 * (1.0 + i * 0.01) for i in range(20)) / 20
        ohlcv = _ohlcv(series, vol_mult=1.0, tail_vol=avg_vol * 0.4)
        action, reason = evaluate_strategy("shrink_pullback", series,
                                           {"fast_ma": 5, "mid_ma": 10, "slow_ma": 20,
                                            "volume_ma_window": 20,
                                            "pullback_vol_ratio": 0.7},
                                           ohlcv=ohlcv)
        assert action == "buy"
        assert "pullback" in reason

    def test_hold_without_bullish_alignment(self):
        # 下跌趋势 → 不满足多头排列
        down = [10.0 - i * 0.1 for i in range(30)]
        action, _r = evaluate_strategy("shrink_pullback", down)
        assert action == "hold"


class TestMeanReversion:
    def test_buy_oversold_below_ma(self):
        # 深跌后 RSI 超卖且价低于慢均线
        down = [10.0 - i * 0.15 for i in range(30)]
        action, reason = evaluate_strategy("mean_reversion", down)
        assert action == "buy"
        assert "mean_reversion" in reason

    def test_sell_when_neutral(self):
        down = [10.0 - i * 0.15 for i in range(30)]
        up_tail = down + [d + 2.0 for d in down[-5:]]  # 反弹回中性
        action, _r = evaluate_strategy("mean_reversion", up_tail, position_held=True)
        assert action == "sell"


class TestIntegration:
    """与 BacktestRunner.evaluate_signal 的路由集成。"""

    def test_default_is_multi_factor(self):
        runner = BacktestRunner()
        # 默认 multi_factor：行为与旧版一致（不抛错，返回合法 action）
        action, reason = runner.evaluate_signal(GOLDEN_UP, {"fast_ma": 5, "slow_ma": 20})
        assert action in ("buy", "sell", "hold")
        assert isinstance(reason, str)

    def test_strategy_route(self):
        runner = BacktestRunner()
        action, reason = runner.evaluate_signal(
            GOLDEN_UP, {"fast_ma": 5, "slow_ma": 10}, strategy="golden_cross")
        assert action == "buy"
        assert "cross" in reason

    def test_unknown_strategy_via_runner(self):
        runner = BacktestRunner()
        action, reason = runner.evaluate_signal(GOLDEN_UP, strategy="nope")
        assert action == "hold"
        assert "unknown strategy" in reason
