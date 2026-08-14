"""LAAP Paper Trading — 行情源（真实源优先 + Stub fallback）。

决策 #2：首版用真实源。LiveMarketSource（akshare / Longbridge WS 可选）优先，
失败/无 token 时降级 StubMarketSource（合成行情），降级显式 used_fallback 标记。

沙箱注意：联网受限，测试一律用 Stub；Live 源在用户环境真实验证。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("laap.paper_trading.market_source")


class MarketSource:
    """行情源抽象基类。"""

    def get_price(self, symbol: str, ts: Optional[float] = None) -> Tuple[float, Dict[str, Any]]:
        """返回 (价格, 元信息)。元信息含 used_fallback 标记。"""
        raise NotImplementedError


class LiveMarketSource(MarketSource):
    """真实源：akshare 轮询（A 股）+ Longbridge WS（可选，后续接）。

    运行时降级：取价失败（网络/无数据）时回落到 Stub 合成价，显式 used_fallback。
    """

    def __init__(self, stub_fallback: Optional["StubMarketSource"] = None):
        self._stub = stub_fallback or StubMarketSource()

    def get_price(self, symbol: str, ts: Optional[float] = None) -> Tuple[float, Dict[str, Any]]:
        try:
            price, meta = self._live_price(symbol)
            return price, meta
        except Exception as e:
            logger.warning(f"LiveMarketSource failed for {symbol}, fallback to stub: {e}")
            price, meta = self._stub.get_price(symbol, ts)
            meta["used_fallback"] = True
            meta["fallback_reason"] = str(e)[:120]
            return price, meta

    def _live_price(self, symbol: str) -> Tuple[float, Dict[str, Any]]:
        """akshare 单只实时买卖五档，取买一/卖一中间价（简化估值，非严格最新成交价）。"""
        try:
            import akshare as ak
        except ImportError as e:
            raise RuntimeError(f"akshare not installed: {e}") from e

        df = ak.stock_bid_ask_em(symbol=symbol)
        if df is not None and not df.empty:
            bid = float(df.iloc[0].get("bid", 0) or 0)
            ask = float(df.iloc[0].get("ask", 0) or 0)
            if bid and ask:
                return (bid + ask) / 2, {"source": "akshare", "used_fallback": False}
        raise RuntimeError(f"no live price for {symbol}")


class StubMarketSource(MarketSource):
    """合成行情：确定性随机游走，用于沙箱测试 / 真实源降级。"""

    def __init__(self, base_prices: Optional[Dict[str, float]] = None, seed: int = 42):
        import random
        self._rng = random.Random(seed)
        self._base = base_prices or {}
        self._cache: Dict[str, float] = {}

    def get_price(self, symbol: str, ts: Optional[float] = None) -> Tuple[float, Dict[str, Any]]:
        if symbol not in self._cache:
            base = self._base.get(symbol, 100.0)
            self._cache[symbol] = base
        # 小幅随机游走（确定性，seed 固定）
        drift = self._rng.uniform(-0.005, 0.005)
        self._cache[symbol] *= (1 + drift)
        return round(self._cache[symbol], 4), {"source": "stub", "used_fallback": True}


def resolve_source(prefer_live: bool = True) -> MarketSource:
    """工厂：优先真实源，失败降级 Stub（显式 used_fallback）。

    探测 akshare 可用性（真实源依赖），沙箱/离线环境自动降级。
    """
    if prefer_live:
        try:
            import akshare  # noqa: F401  探测真实源依赖
            logger.info("MarketSource: LiveMarketSource selected")
            return LiveMarketSource()
        except Exception as e:
            logger.warning(f"LiveMarketSource unavailable, falling back to stub: {e}")
    logger.info("MarketSource: StubMarketSource selected (used_fallback=True)")
    return StubMarketSource()
