# -*- coding: utf-8 -*-
"""PaperLedger 交易成本扣费测试（B2 落地）。

fee_model=None → 零成本（与旧行为完全一致，向后兼容）；
注入 FeeModel → 买入扣佣金+过户费（滑点上调）、卖出扣佣金+印花税+过户费（滑点下调），
pnl 为净额（已扣买卖费用）。
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.fees import FeeModel
from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.paper_service import PaperClosedLoop
from laap.paper_trading.market_source import StubMarketSource
from laap.paper_trading.models import (
    DecisionAction, OrderStatus, PaperSignal,
)


@pytest.fixture()
def ledger(tmp_path):
    db = PaperDB(db_path=str(tmp_path / "pt.db"))
    return PaperLedger(db, initial_cash=100_000.0, enforce_t1=False)


def _buy_signal(qty=100, price=100.0, symbol="600519") -> PaperSignal:
    return PaperSignal(symbol=symbol, action=DecisionAction.BUY,
                       quantity=qty, trigger_price=price, rationale="t")


def _fee() -> FeeModel:
    return FeeModel()  # 默认 = costs.DEFAULT_COSTS（佣0.025%/印花0.05%卖/滑0.1%）


def test_zero_fee_backward_compat(ledger):
    """fee_model=None → 与旧行为一致：现金精确、pnl=(exit-entry)*qty。"""
    order = ledger.submit_signal(_buy_signal(qty=100, price=100.0))
    trade = ledger.fill_order(order.id, fill_price=100.0)
    assert ledger.cash == pytest.approx(100_000 - 100 * 100.0)
    assert trade.entry_price == 100.0
    closed = ledger.close_trade(trade.id, exit_price=110.0)
    assert closed.pnl == pytest.approx((110 - 100) * 100.0)
    assert ledger.cash == pytest.approx(100_000 - 10000 + 11000)


def test_buy_with_fee_deducts_cost(ledger):
    """买入含费：滑点上调 + 佣金扣费。"""
    ledger.fee_model = _fee()
    order = ledger.submit_signal(_buy_signal(qty=100, price=100.0))
    trade = ledger.fill_order(order.id, fill_price=100.0)
    # slip_price = 100*1.001 = 100.1；amount=10010；buy_fee=10010*0.00025=2.5025
    assert trade.entry_price == pytest.approx(100.1)
    assert ledger.cash == pytest.approx(100_000 - 10010 - 2.5025)


def test_close_with_fee_net_pnl(ledger):
    """卖出含费：滑点下调 + 佣金+印花税，pnl 为净额。"""
    ledger.fee_model = _fee()
    order = ledger.submit_signal(_buy_signal(qty=100, price=100.0))
    trade = ledger.fill_order(order.id, fill_price=100.0)
    closed = ledger.close_trade(trade.id, exit_price=110.0)
    # exit_slip=110*0.999=109.89；amount=10989；sell_fee=10989*0.00075=8.24175
    # proceeds=10989-8.24175=10980.75825；entry_cost=10010+2.5025=10012.5025
    # pnl=10980.75825-10012.5025=968.25575
    assert closed.exit_price == pytest.approx(109.89)
    assert closed.pnl == pytest.approx(968.25575)
    assert ledger.cash == pytest.approx(100_000 - 10012.5025 + 10980.75825)


def test_fee_reduces_pnl_versus_zero(ledger, tmp_path):
    """同一笔交易，含费 pnl 应严格小于零成本 pnl。"""
    db0 = PaperDB(db_path=str(tmp_path / "zero.db"))
    l0 = PaperLedger(db0, initial_cash=100_000.0, enforce_t1=False)  # 零成本
    o0 = l0.submit_signal(_buy_signal(qty=100, price=100.0))
    t0 = l0.fill_order(o0.id, fill_price=100.0)
    p0 = l0.close_trade(t0.id, exit_price=110.0).pnl

    ledger.fee_model = _fee()
    o1 = ledger.submit_signal(_buy_signal(qty=100, price=100.0))
    t1 = ledger.fill_order(o1.id, fill_price=100.0)
    p1 = ledger.close_trade(t1.id, exit_price=110.0).pnl
    assert p1 < p0
    assert p0 == pytest.approx(1000.0)


def test_decide_and_trade_with_fee(tmp_path, monkeypatch):
    """PaperClosedLoop.decide_and_trade 注入 FeeModel → 成交扣费（fill_price 为滑点后价）。"""
    # 交易时段桩：decide_and_trade 有时间门（2026-08-17 起），非交易时段拒单
    import laap.paper_trading.paper_service as ps
    import datetime as _dt
    class _N:
        hour = 14
        minute = 0
        def strftime(self, f):
            return "14:00"
    class _FakeDT:
        @staticmethod
        def now():
            return _N()
    monkeypatch.setattr(ps, "datetime", _FakeDT)
    db = PaperDB(db_path=str(tmp_path / "loop.db"))
    market = StubMarketSource(base_prices={"600519": 100.0}, seed=1)
    loop = PaperClosedLoop(db=db, market=market, memory=None,
                           initial_cash=100_000.0, enforce_t1=False,
                           fee_model=_fee())
    res = loop.decide_and_trade("600519", DecisionAction.BUY, quantity=100,
                                trigger_price=100.0, rationale="news",
                                allow_fallback=True)  # stub 测试行情显式允许
    # fill_price = 成交价（滑点后）；现金 = 初始 − 成交额 − 佣金
    assert res["trade_id"]
    fill = res["fill_price"]
    assert loop.ledger.cash == pytest.approx(
        100_000 - fill * 100 - fill * 100 * 0.00025, rel=1e-3)
    # 含费后现金应小于"同价格零成本"情形（佣金+滑点扣掉）
    assert loop.ledger.cash < 100_000 - fill * 100
