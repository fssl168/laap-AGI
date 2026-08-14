"""LAAP Paper Trading — 日终结算（MTM 净值 + T+1 锁仓）。

参考 DSA Settlement.daily_settle：持仓按市值估值 + 现金 → 净值快照。
T+1 锁仓（A股）：当日买入持仓不可卖，锁仓摘要由 locked_summary 提供；
强制约束在 ledger.close_trade（当日买入拒绝平仓）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.market_source import MarketSource
from laap.paper_trading.models import PaperNetValue

logger = logging.getLogger("laap.paper_trading.settle")


class Settlement:
    """日终结算器。"""

    def daily_settle(self, ledger: PaperLedger, market: MarketSource,
                     date: Optional[float] = None) -> PaperNetValue:
        """持仓 MTM 结算，落净值快照（含 locked 持仓市值）。

        Args:
            ledger: PaperLedger（含现金 + 持仓）
            market: MarketSource（估值价）
            date: 结算时点（默认 now）
        """
        return ledger.snapshot_net_value(market)

    def locked_summary(self, ledger: PaperLedger) -> Dict[str, Any]:
        """T+1 锁仓摘要：当日买入的持仓（不可卖）。"""
        locked = ledger.t1_locked_positions()
        return {
            "locked_count": len(locked),
            "locked_positions": [p.to_dict() for p in locked],
        }
