"""P0 Settlement 日终结算测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.market_source import StubMarketSource
from laap.paper_trading.settle import Settlement
from laap.paper_trading.models import DecisionAction, PaperSignal


@pytest.fixture()
def ledger(tmp_path):
    db = PaperDB(db_path=str(tmp_path / "paper_trading.db"))
    return PaperLedger(db, initial_cash=100_000.0)


def test_daily_settle_records_net_value(ledger):
    market = StubMarketSource(base_prices={"600519": 1355.0})
    sig = PaperSignal(symbol="600519", action=DecisionAction.BUY,
                      quantity=100, trigger_price=1355.0)
    order = ledger.submit_signal(sig)
    ledger.fill_order(order.id, fill_price=1355.0)

    settlement = Settlement()
    nv = settlement.daily_settle(ledger, market)

    assert nv.total > 0
    assert len(ledger.net_values()) == 1


def test_daily_settle_no_positions(ledger):
    market = StubMarketSource()
    settlement = Settlement()
    nv = settlement.daily_settle(ledger, market)
    # 无持仓 → equity=0, total=cash
    assert nv.equity == 0.0
    assert nv.total == ledger.cash
