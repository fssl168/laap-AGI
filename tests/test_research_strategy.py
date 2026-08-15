# -*- coding: utf-8 -*-
"""research_strategy.py 研报策略层测试。"""
import pytest

from laap.paper_trading.research_strategy import (
    TradePlan, rating_bullish_ratio, target_price_mean, build_trade_plan,
    _round_lot, _rating_is_bullish)
from laap.paper_trading.news_intel import ResearchReport
from laap.paper_trading.news_verifier import TechState
from laap.paper_trading.fees import FeeModel


def _reports(ratings, tps=None):
    tps = tps or [None] * len(ratings)
    return [ResearchReport("600519", title=f"r{i}", rating=ratings[i],
                           target_price=tps[i]) for i in range(len(ratings))]


def _tech(close=100.0, rsi=45.0, atr=2.0, limit_up=False):
    return TechState("600519", rsi=rsi, close=close, atr=atr,
                     ma20=95.0, prev_close=99.0, change_pct=0.01,
                     limit_up=limit_up)


def test_rating_is_bullish():
    assert _rating_is_bullish("买入")
    assert _rating_is_bullish("增持")
    assert not _rating_is_bullish("卖出")
    assert not _rating_is_bullish("")


def test_rating_bullish_ratio():
    assert rating_bullish_ratio(_reports(["买入", "增持", "中性"])) == pytest.approx(2/3)
    assert rating_bullish_ratio([]) == 0.0


def test_target_price_mean():
    assert target_price_mean(_reports(["买入", "买入"], [100.0, 120.0])) == pytest.approx(110.0)
    assert target_price_mean(_reports(["买入", "买入"])) is None
    assert target_price_mean([]) is None


def test_round_lot():
    assert _round_lot(100) == 100
    assert _round_lot(150) == 100
    assert _round_lot(99) == 0
    assert _round_lot(1050) == 1000


def test_build_trade_plan_now_buy():
    reports = _reports(["买入", "增持", "中性"], [110.0, 115.0, None])
    plan = build_trade_plan("600519", None, reports, _tech(close=100.0, rsi=45.0),
                            cash=1_000_000, position_scale=0.5)
    assert plan.action == "buy"
    assert plan.buy_time == "now"
    assert plan.quantity >= 100
    assert plan.quantity % 100 == 0
    assert plan.stop_loss is not None
    assert plan.take_profit is not None


def test_build_trade_plan_overbought_pullback():
    reports = _reports(["买入", "买入"])
    plan = build_trade_plan("600519", None, reports, _tech(rsi=65.0),
                            cash=1_000_000, position_scale=0.5)
    assert plan.buy_time == "pullback"
    assert plan.action == "hold"


def test_build_trade_plan_no_reports_wait():
    plan = build_trade_plan("600519", None, [], _tech(),
                            cash=1_000_000, position_scale=0.5)
    assert plan.buy_time == "wait"
    assert plan.action == "hold"


def test_build_trade_plan_quantity_capped():
    reports = _reports(["买入", "买入"])
    # 总资产 1M，单票上限 10% → 100k；价格 1000 → 最多 100 股
    plan = build_trade_plan("600519", None, reports, _tech(close=1000.0, rsi=40.0),
                            cash=1_000_000, position_scale=0.5)
    assert plan.quantity <= 100
    assert plan.quantity % 100 == 0


def test_build_trade_plan_position_scale_max_from_self():
    reports = _reports(["买入", "买入"])
    class _Self:
        position_scale_max = 0.05  # 5% 上限
    # 价格 1000，5% → 50k → 50 股 → 取整到 0（<100）→ wait
    plan = build_trade_plan("600519", None, reports, _tech(close=1000.0, rsi=40.0),
                            cash=1_000_000, position_scale=0.5, trading_self=_Self())
    assert plan.quantity < 100


def test_build_trade_plan_limit_up_wait():
    reports = _reports(["买入", "买入"])
    plan = build_trade_plan("600519", None, reports, _tech(rsi=40.0, limit_up=True),
                            cash=1_000_000, position_scale=0.5)
    assert plan.buy_time != "now"
