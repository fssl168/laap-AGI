"""P0 MarketSource 行情源测试。

验证: Stub 确定性 / resolve_source 降级 / used_fallback 标记。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from laap.paper_trading.market_source import (
    MarketSource,
    StubMarketSource,
    resolve_source,
)


def test_stub_source_deterministic():
    """同 seed 的 Stub 源产出相同序列。"""
    s1 = StubMarketSource(seed=42, base_prices={"600519": 100.0})
    s2 = StubMarketSource(seed=42, base_prices={"600519": 100.0})
    seq1 = [s1.get_price("600519")[0] for _ in range(5)]
    seq2 = [s2.get_price("600519")[0] for _ in range(5)]
    assert seq1 == seq2


def test_stub_source_marks_fallback():
    s = StubMarketSource()
    _price, meta = s.get_price("600519")
    assert meta["used_fallback"] is True
    assert meta["source"] == "stub"


def test_stub_source_uses_base_price():
    s = StubMarketSource(base_prices={"000001": 10.0})
    # 首价接近 base（随机游走 ±0.5%）
    price, _ = s.get_price("000001")
    assert 9.0 <= price <= 11.0


def test_resolve_source_stub_when_no_live():
    """prefer_live=False → 直接 Stub。"""
    src = resolve_source(prefer_live=False)
    assert isinstance(src, StubMarketSource)


def test_resolve_source_live_falls_back_to_stub():
    """沙箱无 akshare → resolve_source 降级 Stub（不抛异常）。"""
    src = resolve_source(prefer_live=True)
    # 无论 Live 是否可用，resolve_source 必须返回可用源
    assert isinstance(src, MarketSource)
    _price, meta = src.get_price("600519")
    assert meta["used_fallback"] is True  # 沙箱必降级
