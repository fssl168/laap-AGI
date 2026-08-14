"""LAAP Paper Trading — 交易适应度（闭环 B 前半）。

把"交易业绩"量化成 [0,1] 分数，作为代码级自进化的部署门禁。
组合: score = 0.4*收益_norm + 0.35*夏普_norm + 0.25*(1-回撤)

与 M2 FitnessEvaluator（软件健康度）构成双门槛：
  代码变更只接受"软件健康度不降 + 交易适应度 OOS 不劣化"。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from laap.paper_trading.models import PaperNetValue


def _cumulative_return(net_values: List[PaperNetValue]) -> float:
    """累计收益率 (total 序列首末)。无/单点 → 0.0。"""
    if len(net_values) < 2:
        return 0.0
    start = net_values[0].total
    end = net_values[-1].total
    if start <= 0:
        return 0.0
    return (end - start) / start


def _daily_returns(net_values: List[PaperNetValue]) -> List[float]:
    """日收益率序列（相邻 total 变化率）。"""
    rets = []
    for i in range(1, len(net_values)):
        prev = net_values[i - 1].total
        cur = net_values[i].total
        if prev > 0:
            rets.append((cur - prev) / prev)
    return rets


def _sharpe_ratio(net_values: List[PaperNetValue], rf: float = 0.0,
                  periods_per_year: int = 252) -> float:
    """夏普比率（日收益 mean/std * sqrt(252)）。样本 <2 → 0.0。"""
    rets = _daily_returns(net_values)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean - rf / periods_per_year) / std * math.sqrt(periods_per_year)


def _max_drawdown(net_values: List[PaperNetValue]) -> float:
    """最大回撤（峰值→谷底最大跌幅，[0,1]）。"""
    peak = -float("inf")
    max_dd = 0.0
    for nv in net_values:
        peak = max(peak, nv.total)
        if peak > 0:
            dd = (peak - nv.total) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def _normalize_return(cumret: float) -> float:
    """累计收益归一化到 [0,1]：<0 → 0；>50% → 1；线性。"""
    return max(0.0, min(1.0, cumret / 0.5))


def _normalize_sharpe(sharpe: float) -> float:
    """夏普归一化到 [0,1]：<0 → 0；>2 → 1；线性。"""
    return max(0.0, min(1.0, sharpe / 2.0))


def compute_trade_fitness(net_values: List[PaperNetValue],
                          weights: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """组合交易适应度分数。

    Args:
        net_values: 净值序列
        weights: 可选权重覆盖（默认 收益0.4/夏普0.35/回撤0.25）
    Returns: {score, cumulative_return, sharpe_ratio, max_drawdown, components...}
    """
    w = weights or {"return": 0.4, "sharpe": 0.35, "drawdown": 0.25}
    cumret = _cumulative_return(net_values)
    sharpe = _sharpe_ratio(net_values)
    max_dd = _max_drawdown(net_values)

    score = (
        w["return"] * _normalize_return(cumret)
        + w["sharpe"] * _normalize_sharpe(sharpe)
        + w["drawdown"] * (1.0 - max_dd)
    )
    return {
        "score": round(score, 4),
        "cumulative_return": round(cumret, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
    }
