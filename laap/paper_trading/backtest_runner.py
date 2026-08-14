"""LAAP Paper Trading — 样本外回测 runner（闭环 B 前半）。

提供:
  split_series  时间序切分（train/valid/oos，非随机，防未来函数）
  run_backtest  内置均线交叉策略在价格序列上回放，产出交易适应度
  oos_gate      OOS 不劣化门禁（fail-closed）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from laap.paper_trading.models import PaperNetValue
from laap.paper_trading.trade_fitness import compute_trade_fitness

logger = logging.getLogger("laap.paper_trading.backtest_runner")


def split_series(dates: List[Any], train: float = 0.6, valid: float = 0.2,
                 oos: float = 0.2) -> Tuple[List[Any], List[Any], List[Any]]:
    """按时间序切分（非随机，防未来函数泄漏）。

    Args:
        dates: 有序时间序列（升序）
        train/valid/oos: 三段比例（应相加 ≈ 1）
    Returns: (train_dates, valid_dates, oos_dates)
    """
    n = len(dates)
    train_end = int(n * train)
    valid_end = train_end + int(n * valid)
    return dates[:train_end], dates[train_end:valid_end], dates[valid_end:]


def _moving_average(prices: List[float], window: int) -> List[Optional[float]]:
    """简单移动平均（前 window-1 个为 None）。"""
    ma: List[Optional[float]] = []
    for i in range(len(prices)):
        if i + 1 < window:
            ma.append(None)
        else:
            ma.append(sum(prices[i - window + 1:i + 1]) / window)
    return ma


def _run_ma_cross(price_series: List[float], short: int, long: int,
                  initial_cash: float = 1_000_000.0) -> List[PaperNetValue]:
    """均线交叉策略回放：金叉全仓买入、死叉清仓。

    Returns: net_values 序列（逐点 MTM）。
    """
    if short >= long:
        raise ValueError(f"short({short}) must be < long({long})")
    short_ma = _moving_average(price_series, short)
    long_ma = _moving_average(price_series, long)

    cash = initial_cash
    position = 0.0
    prev_diff = 0.0
    net_values: List[PaperNetValue] = []
    for i, price in enumerate(price_series):
        sma = short_ma[i]
        lma = long_ma[i]
        if sma is None or lma is None:
            continue
        diff = sma - lma
        # 金叉买入 / 死叉卖出（一次一仓）
        if prev_diff <= 0 < diff and position == 0.0:
            position = cash / price
            cash = 0.0
        elif prev_diff >= 0 > diff and position > 0.0:
            cash = position * price
            position = 0.0
        prev_diff = diff
        equity = position * price
        net_values.append(PaperNetValue(
            ts=float(i), cash=cash, equity=equity, total=cash + equity))
    return net_values


class BacktestRunner:
    """样本外回测 runner。"""

    def run_backtest(self, price_series: List[float], params: Optional[Dict[str, Any]] = None,
                     split: Optional[Tuple[int, int]] = None) -> Dict[str, float]:
        """在价格序列（或切片）上回放策略，返回交易适应度 metrics。

        Args:
            price_series: 价格序列
            params: 策略参数 {short, long}
            split: (start, end) 切片；None 用整段
        """
        p = params or {}
        short = int(p.get("short", 5))
        long = int(p.get("long", 20))
        start, end = split if split else (0, len(price_series))
        seg = price_series[start:end]
        net_values = _run_ma_cross(seg, short, long)
        return compute_trade_fitness(net_values)

    def oos_gate(self, train_metrics: Dict[str, float],
                 oos_metrics: Dict[str, float]) -> Tuple[bool, str]:
        """OOS 不劣化门禁（fail-closed）。

        通过条件：oos 累计收益 >= 0 且 oos 夏普 >= train 夏普 * 0.8。
        Returns: (ok, reason)。
        """
        oos_cumret = oos_metrics.get("cumulative_return", 0.0)
        oos_sharpe = oos_metrics.get("sharpe_ratio", 0.0)
        train_sharpe = train_metrics.get("sharpe_ratio", 0.0)

        if oos_cumret < 0:
            return False, f"oos cumulative_return {oos_cumret:.2%} < 0"
        threshold = train_sharpe * 0.8
        if oos_sharpe < threshold:
            return False, (f"oos sharpe {oos_sharpe:.3f} < "
                           f"{threshold:.3f} (train*0.8)")
        return True, "oos not degraded"
