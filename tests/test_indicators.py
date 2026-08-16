"""指标库正确性测试（laap.paper_trading.indicators）。

用构造序列验证数值正确性 + 契约（预热期 None / 长度对齐 / 不足数据降级）。
"""

from __future__ import annotations

import pytest

from laap.paper_trading.indicators import (
    ema, macd, boll, stochastic, williams_r, cci, obv, vwap, fib,
)

PRICES = [10.0, 10.5, 10.2, 10.8, 11.0, 11.5, 11.2, 11.8, 12.0, 12.5]
VOL = [1000.0, 1200.0, 900.0, 1500.0, 1100.0, 1300.0, 1000.0, 1600.0, 1400.0, 1700.0]


class TestContract:
    """通用契约：长度对齐 + 预热 None + 空输入安全。"""

    def test_len_aligned(self):
        assert len(ema(PRICES, 5)) == len(PRICES)
        line, sig, hist = macd(PRICES)
        assert len(line) == len(sig) == len(hist) == len(PRICES)
        mid, up, lo = boll(PRICES, 5)
        assert len(mid) == len(up) == len(lo) == len(PRICES)
        k, d = stochastic(PRICES, PRICES, PRICES)
        assert len(k) == len(d) == len(PRICES)
        assert len(williams_r(PRICES, PRICES, PRICES)) == len(PRICES)
        assert len(cci(PRICES, PRICES, PRICES)) == len(PRICES)
        assert len(obv(PRICES, VOL)) == len(PRICES)
        assert len(vwap(PRICES, PRICES, PRICES, VOL)) == len(PRICES)
        f = fib(PRICES, PRICES, PRICES, lookback=5)
        assert set(f.keys()) == {0.236, 0.382, 0.5, 0.618, 0.786}
        assert all(len(s) == len(PRICES) for s in f.values())

    def test_empty_series_safe(self):
        line, sig, hist = macd([])
        assert line == [] and sig == [] and hist == []
        assert boll([], 5) == ([], [], [])
        assert williams_r([], [], []) == []
        assert obv([], []) == []

    def test_short_series_returns_none(self):
        assert ema([1.0], 5) == [1.0]  # ema 从首值起算
        mid, up, lo = boll([1.0], 5)
        assert mid == [None] and up == [None] and lo == [None]
        assert williams_r([1.0], [1.0], [1.0]) == [None]
        f = fib([1.0], [1.0], [1.0], lookback=5)
        assert f[0.5] == [None]


class TestEma:
    def test_ema_matches_manual(self):
        # alpha = 2/(5+1) = 1/3
        out = ema(PRICES, 5)
        assert out[0] == pytest.approx(10.0)
        assert out[1] == pytest.approx(10.0 + (10.5 - 10.0) / 3)
        expected2 = out[1] + (10.2 - out[1]) / 3
        assert out[2] == pytest.approx(expected2)

    def test_span1_is_identity(self):
        out = ema(PRICES, 1)  # alpha=1 → 原值
        assert out == PRICES


class TestBoll:
    def test_mid_is_sma(self):
        mid, _up, _lo = boll(PRICES, 5)
        assert mid[4] == pytest.approx(sum(PRICES[:5]) / 5)
        assert mid[9] == pytest.approx(sum(PRICES[5:]) / 5)

    def test_band_symmetry(self):
        mid, up, lo = boll(PRICES, 5, k=2.0)
        for i in range(4, len(PRICES)):
            # upper = mid + k*std, lower = mid - k*std → upper-mid = mid-lower
            assert up[i] - mid[i] == pytest.approx(mid[i] - lo[i])


class TestMacd:
    def test_hist_is_line_minus_signal(self):
        line, sig, hist = macd(PRICES)
        for i in range(len(PRICES)):
            if line[i] is not None and sig[i] is not None:
                assert hist[i] == pytest.approx(line[i] - sig[i])


class TestObv:
    def test_obv_accumulates_sign(self):
        out = obv([10.0, 11.0, 10.5, 12.0], [100.0, 200.0, 50.0, 300.0])
        assert out == [0.0, 200.0, 150.0, 450.0]  # +200 -50 +300


class TestVwap:
    def test_vwap_positive(self):
        out = vwap([10.0, 11.0], [9.0, 10.0], [10.0, 11.0], [100.0, 100.0])
        # TP0 = (10+9+10)/3 = 9.667; TP1 = (11+10+11)/3 = 10.667
        assert out[0] == pytest.approx((10.0 + 9.0 + 10.0) / 3)
        assert out[1] == pytest.approx(
            ((10.0 + 9.0 + 10.0) / 3 * 100 + (11.0 + 10.0 + 11.0) / 3 * 100) / 200)


class TestFib:
    def test_uptrend_levels(self):
        high = [10.0, 11.0, 12.0, 13.0, 14.0]
        low = [9.0, 10.0, 11.0, 12.0, 13.0]
        close = [10.0, 11.0, 12.0, 13.0, 14.0]
        f = fib(high, low, close, lookback=5)
        # 上升趋势: level = swing_high - ratio*diff = 14 - ratio*5
        assert f[0.5][4] == pytest.approx(14.0 - 0.5 * 5.0)
        assert f[0.382][4] == pytest.approx(14.0 - 0.382 * 5.0)
