# -*- coding: utf-8 -*-
"""fees.py 交易成本模型测试（B2）。"""
import pytest

from laap.paper_trading.costs import DEFAULT_COSTS
from laap.paper_trading.fees import FeeModel, calculate_cost, apply_slippage


def test_fee_model_defaults_align_costs_single_source():
    f = FeeModel()
    assert f.commission_rate == DEFAULT_COSTS["commission"]
    assert f.stamp_duty == DEFAULT_COSTS["stamp"]
    assert f.slippage == DEFAULT_COSTS["slippage"]
    assert f.min_commission == 0.0
    assert f.transfer_fee == 0.0


def test_fee_model_override():
    f = FeeModel(commission_rate=0.001, min_commission=5.0)
    assert f.commission_rate == 0.001
    assert f.min_commission == 5.0
    # 未覆盖字段仍取单源
    assert f.stamp_duty == DEFAULT_COSTS["stamp"]


def test_calculate_cost_buy():
    total, comm, stamp, transfer = calculate_cost(100_000, "buy")
    # 佣金 = 100000*0.00025 = 25；印花税 0；过户费 0
    assert comm == pytest.approx(25.0)
    assert stamp == 0.0
    assert transfer == 0.0
    assert total == pytest.approx(25.0)


def test_calculate_cost_sell_includes_stamp():
    total, comm, stamp, transfer = calculate_cost(100_000, "sell")
    assert comm == pytest.approx(25.0)
    assert stamp == pytest.approx(50.0)  # 100000*0.0005
    assert transfer == 0.0
    assert total == pytest.approx(75.0)


def test_calculate_cost_min_commission():
    f = FeeModel(min_commission=5.0)
    total, comm, _, _ = calculate_cost(1_000, "buy", f)  # 1000*0.00025=0.25 < 5
    assert comm == 5.0
    assert total == pytest.approx(5.0)


def test_calculate_cost_transfer_fee():
    f = FeeModel(transfer_fee=0.00001)
    _, _, _, transfer = calculate_cost(100_000, "buy", f)
    assert transfer == pytest.approx(1.0)


def test_apply_slippage_direction():
    assert apply_slippage(100.0, "buy") == pytest.approx(100.1)   # 买贵
    assert apply_slippage(100.0, "sell") == pytest.approx(99.9)   # 卖贱


def test_calculate_cost_custom_fee():
    f = FeeModel(commission_rate=0.001, stamp_duty=0.001, slippage=0.002)
    total, comm, stamp, _ = calculate_cost(50_000, "sell", f)
    assert comm == pytest.approx(50.0)
    assert stamp == pytest.approx(50.0)
    assert total == pytest.approx(100.0)
