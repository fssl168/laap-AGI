# -*- coding: utf-8 -*-
"""limit_utils.py 涨停/跌停/停牌检测测试（B3）。"""
from laap.paper_trading.limit_utils import (
    is_limit_up, is_limit_down, is_suspended_or_invalid)


def _k(**kw):
    base = {"close": 10.0, "high": 10.2, "low": 9.8, "open": 10.0,
            "volume": 1_000_000, "up_limit": 11.0, "down_limit": 9.0}
    base.update(kw)
    return base


def test_limit_up_explicit_flag():
    assert is_limit_up(_k(is_limit_up=True))
    assert not is_limit_up(_k(is_limit_up=False))


def test_limit_up_by_up_limit_boundary():
    # close 贴近 up_limit（≥ 0.9999）
    assert is_limit_up(_k(close=10.999))
    assert not is_limit_up(_k(close=10.5))


def test_limit_up_by_ohlc_form():
    # 一字涨停：high==low==close，close>=open
    assert is_limit_up(_k(close=11.0, high=11.0, low=11.0, open=10.5))
    # 非一字（high>low）且 close 未贴涨停价 → 不判涨停
    assert not is_limit_up(_k(close=10.8, high=11.2, low=10.8))


def test_limit_up_by_limit_status():
    assert is_limit_up(_k(limit_status="up_limit"))
    assert is_limit_up(_k(limit_status="涨停"))
    assert not is_limit_up(_k(limit_status="down_limit"))


def test_limit_down():
    assert is_limit_down(_k(is_limit_down=True))
    assert is_limit_down(_k(close=9.0005))  # ≤ down_limit×1.0001
    assert is_limit_down(_k(close=9.0, high=9.0, low=9.0, open=9.5))
    assert not is_limit_down(_k(close=9.5))


def test_suspended_or_invalid():
    assert is_suspended_or_invalid(_k(close=0.0))
    assert is_suspended_or_invalid(_k(high=9.0, low=10.0))  # high<low
    assert is_suspended_or_invalid(_k(volume=0.0))
    assert is_suspended_or_invalid(_k(is_suspended=True))
    assert not is_suspended_or_invalid(_k())
    # None/空 → 视为无效（fail-closed）
    assert is_suspended_or_invalid(None)
    assert is_suspended_or_invalid({})


def test_limit_utils_no_crash_partial():
    # 缺字段不应崩溃
    assert not is_limit_up({"close": 10.0})
    assert not is_limit_down({"close": 10.0})
    assert not is_suspended_or_invalid({"close": 10.0})
