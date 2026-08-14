"""P0 PaperLedger 最小 OMS 测试。

验证: 下单幂等 / 成交 / 撤单 / 平仓 pnl / 净值快照 / 统计。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.market_source import StubMarketSource
from laap.paper_trading.models import (
    DecisionAction,
    OrderStatus,
    PaperSignal,
)


@pytest.fixture()
def ledger(tmp_path):
    db = PaperDB(db_path=str(tmp_path / "paper_trading.db"))
    return PaperLedger(db, initial_cash=100_000.0, enforce_t1=False)


def _buy_signal(symbol="600519", qty=100, price=1355.0) -> PaperSignal:
    return PaperSignal(
        symbol=symbol, action=DecisionAction.BUY, quantity=qty,
        trigger_price=price, rationale="test buy",
    )


def test_submit_signal_creates_pending_order(ledger):
    sig = _buy_signal()
    order = ledger.submit_signal(sig)
    assert order.status == OrderStatus.PENDING
    assert order.signal_id.startswith("600519")


def test_submit_signal_idempotent(ledger):
    """client_request_id 幂等：重复提交返回同一订单（参考 DSA T-13）。"""
    sig = _buy_signal()
    o1 = ledger.submit_signal(sig, client_request_id="req-1")
    o2 = ledger.submit_signal(sig, client_request_id="req-1")
    assert o1.id == o2.id
    # 只产生一个订单
    assert ledger.stats()["orders"] == 1


def test_fill_order_creates_trade_and_deducts_cash(ledger):
    sig = _buy_signal(qty=100, price=1355.0)
    order = ledger.submit_signal(sig)
    cash_before = ledger.cash
    trade = ledger.fill_order(order.id, fill_price=1355.0)
    assert trade.entry_price == 1355.0
    assert trade.quantity == 100
    assert ledger.cash == cash_before - 1355.0 * 100
    assert ledger.stats()["trades"] == 1
    assert ledger.stats()["open_positions"] == 1


def test_cancel_order_only_pending(ledger):
    order = ledger.submit_signal(_buy_signal())
    ledger.cancel_order(order.id)
    # 已取消的订单再取消 → 报错
    with pytest.raises(ValueError):
        ledger.cancel_order(order.id)


def test_close_trade_pnl(ledger):
    order = ledger.submit_signal(_buy_signal(qty=100, price=100.0))
    trade = ledger.fill_order(order.id, fill_price=100.0)
    cash_after_buy = ledger.cash
    closed = ledger.close_trade(trade.id, exit_price=110.0)
    assert closed.pnl == pytest.approx(1000.0)
    assert closed.pnl_pct == pytest.approx(0.10)
    # 卖出回笼现金
    assert ledger.cash == cash_after_buy + 110.0 * 100
    assert ledger.stats()["open_positions"] == 0


def test_close_trade_twice_raises(ledger):
    order = ledger.submit_signal(_buy_signal(qty=10, price=100.0))
    trade = ledger.fill_order(order.id, fill_price=100.0)
    ledger.close_trade(trade.id, exit_price=105.0)
    with pytest.raises(ValueError):
        ledger.close_trade(trade.id, exit_price=106.0)


def test_snapshot_net_value(ledger):
    market = StubMarketSource(base_prices={"600519": 1355.0})
    order = ledger.submit_signal(_buy_signal(qty=100, price=1355.0))
    ledger.fill_order(order.id, fill_price=1355.0)
    nv = ledger.snapshot_net_value(market)
    # equity = 100 * ~1355（stub 随机游走 ±0.5%）
    assert nv.total > 0
    assert len(ledger.net_values()) == 1


def test_restore_cash_from_net_value(tmp_path):
    """重启后 cash 从最新净值恢复。"""
    db = PaperDB(db_path=str(tmp_path / "paper_trading.db"))
    l1 = PaperLedger(db, initial_cash=100_000.0, enforce_t1=False)
    order = l1.submit_signal(_buy_signal(qty=100, price=100.0))
    l1.fill_order(order.id, fill_price=100.0)
    l1.snapshot_net_value(StubMarketSource(base_prices={"600519": 100.0}))
    cash_after = l1.cash

    # 重新打开同一 db
    l2 = PaperLedger(db, initial_cash=100_000.0, enforce_t1=False)
    assert l2.cash == cash_after
