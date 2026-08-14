"""LAAP Paper Trading — 日终结算（MTM 净值）。

参考 DSA Settlement.daily_settle：持仓按市值估值 + 现金 → 净值快照。
（最小闭环先不做 T+1 可用量锁仓，后续按需补。）
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.market_source import MarketSource
from laap.paper_trading.models import PaperNetValue

logger = logging.getLogger("laap.paper_trading.settle")


class Settlement:
    """日终结算器。"""

    def daily_settle(self, ledger: PaperLedger, market: MarketSource,
                     date: Optional[float] = None) -> PaperNetValue:
        """持仓 MTM 结算，落净值快照。

        Args:
            ledger: PaperLedger（含现金 + 持仓）
            market: MarketSource（估值价）
            date: 结算时点（默认 now）
        """
        return ledger.snapshot_net_value(market)
