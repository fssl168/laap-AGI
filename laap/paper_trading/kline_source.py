"""LAAP Paper Trading — 历史 K 线加载（增强 1：接真实历史 K 线）。

从 watchlist_kline_store（本地 kline.db）加载历史 close 序列，作为
OOS 门禁的真实基线数据源；失败降级合成序列（显式 fallback 标记）。
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger("laap.paper_trading.kline_source")


def _synthetic_series(days: int) -> List[float]:
    """合成趋势+噪声序列（仅作降级兜底）。"""
    return [100.0 + i * 0.5 + ((i * 7) % 11 - 5) * 0.3 for i in range(days)]


def load_price_series(symbol: str = "600519", days: int = 120,
                      fallback: bool = True) -> List[float]:
    """加载历史 close 序列（真实历史 K 线优先）。

    Args:
        symbol: 股票代码（如 600519 / 000001）
        days: 取最近 N 天
        fallback: 失败时是否降级合成序列（默认 True，保证门禁可用）
    Returns: close 序列（升序）；无数据且 fallback=False 时返回 []。
    """
    try:
        # 项目根目录模块（延迟导入，失败降级）
        from watchlist_kline_store import get_kline
        rows = get_kline(symbol, days=days)
        if rows and len(rows) >= 10:
            closes = [float(r[2]) for r in rows]  # (date, open, close, high, low, volume)
            logger.info(f"loaded {len(closes)} real kline closes for {symbol}")
            return closes
    except Exception as e:
        logger.warning(f"load_price_series real kline failed for {symbol}: {e}")

    if not fallback:
        return []
    logger.warning(f"kline fallback to synthetic series for {symbol} "
                   f"(used_fallback=True)")
    return _synthetic_series(days)
