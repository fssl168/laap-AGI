"""T-A 多策略融合测试：列表多数决 / 单策略透传 / sell 风控优先。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.paper_service import PaperClosedLoop
from laap.paper_trading.market_source import StubMarketSource


class _VoteRunner:
    """记录 strategy 调用、按序返回预设信号的 stub runner。"""
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def evaluate_signal(self, price_series, params=None, ohlcv=None,
                        position_held=False, entry_price=None, peak=None,
                        regime_ma=None, strategy="multi_factor"):
        self.calls.append(strategy)
        if self.results:
            return self.results.pop(0)
        return "hold", f"stub:{strategy}"


@pytest.fixture()
def loop(tmp_path):
    from laap.agi.unified_memory import UnifiedMemory
    db = PaperDB(db_path=str(tmp_path / "pt.db"))
    return PaperClosedLoop(db, StubMarketSource(base_prices={"600519": 100.0}),
                           UnifiedMemory(), initial_cash=1_000_000.0,
                           enforce_t1=False)


def test_fused_signal_none_defaults_multi_factor(loop):
    """strategy=None → 默认 multi_factor（行为不变）。"""
    runner = _VoteRunner()
    action, reason = loop._fused_signal(runner, None, [100.0]*30, {},
                                        None, False, None)
    assert runner.calls == ["multi_factor"]
    assert action == "hold"


def test_fused_signal_single_strategy(loop):
    """strategy=str → 单策略路由。"""
    runner = _VoteRunner()
    loop._fused_signal(runner, "golden_cross", [100.0]*30, {}, None, False, None)
    assert runner.calls == ["golden_cross"]


def test_fused_signal_sell_priority(loop):
    """任一策略 sell → sell（风控优先）。"""
    runner = _VoteRunner([("buy", "a"), ("sell", "b"), ("hold", "c")])
    action, reason = loop._fused_signal(
        runner, ["golden_cross", "macd_momentum", "volume_breakout"],
        [100.0]*30, {}, None, False, None)
    assert action == "sell"
    assert "fused[3]" in reason


def test_fused_signal_buy_majority_2of3(loop):
    """2/3 投 buy → buy。"""
    runner = _VoteRunner([("buy", "a"), ("buy", "b"), ("hold", "c")])
    action, _ = loop._fused_signal(
        runner, ["golden_cross", "macd_momentum", "volume_breakout"],
        [100.0]*30, {}, None, False, None)
    assert action == "buy"


def test_fused_signal_insufficient_buy_holds(loop):
    """< 2/3 投 buy → hold。"""
    runner = _VoteRunner([("buy", "a"), ("hold", "b"), ("hold", "c")])
    action, _ = loop._fused_signal(
        runner, ["golden_cross", "macd_momentum", "volume_breakout"],
        [100.0]*30, {}, None, False, None)
    assert action == "hold"


def test_fused_signal_empty_list_holds(loop):
    runner = _VoteRunner()
    action, reason = loop._fused_signal(runner, [], [100.0]*30, {},
                                        None, False, None)
    assert action == "hold"
    assert "empty" in reason


def test_run_daily_cycle_with_strategy_list(tmp_path):
    """run_daily_cycle(strategy=[...]) 不抛错、返回信号结构。"""
    from laap.agi.unified_memory import UnifiedMemory
    from laap.paper_trading import strategy
    db = PaperDB(db_path=str(tmp_path / "pt.db"))
    loop = PaperClosedLoop(db, StubMarketSource(base_prices={"600519": 100.0}),
                           UnifiedMemory(), initial_cash=1_000_000.0,
                           enforce_t1=False)
    # 上涨行情 → 多策略至少不崩溃
    closes = [100.0 + i * 1.0 for i in range(20)] + \
             [120.0 - i * 1.5 for i in range(8)] + \
             [108.0 + i * 0.55 for i in range(15)]
    ohlcv = [(c - 0.1, c, c + 0.2, c - 0.2, 100_000.0) for c in closes]
    result = loop.run_daily_cycle(
        ["600519"], dict(strategy.STRATEGY_PARAMS),
        ohlcv_map={"600519": ohlcv},
        strategy=["golden_cross", "macd_momentum"])
    assert "signals" in result
    assert result["signals"][0]["action"] in ("buy", "sell", "hold")
    assert "net_value" in result


# ════════════════════════════════════════════════════════════
# T-B: 模板策略 E2E + 进化隔离契约
# ════════════════════════════════════════════════════════════

def test_list_templates_complete():
    """模板注册表完整（multi_factor + 7 模板）。"""
    from laap.paper_trading.strategy_templates import list_templates
    assert set(list_templates()) == {
        "multi_factor", "mean_reversion", "golden_cross", "rsi_reversal",
        "boll_breakout", "macd_momentum", "volume_breakout", "shrink_pullback"}


def test_templates_isolated_from_param_space():
    """进化契约：模板参数（STRATEGY_* 前缀）不进 PARAM_SPACE，进化不碰模板。"""
    from laap.paper_trading.param_evolver import PARAM_SPACE
    from laap.paper_trading.strategy import STRATEGY_PARAMS
    # PARAM_SPACE 键 == STRATEGY_PARAMS 键（模板参数在 quant_config，不在进化空间）
    assert set(PARAM_SPACE) == set(STRATEGY_PARAMS)
    # 模板专属参数（boll/breakout/pullback）不在 PARAM_SPACE
    tmpl_keys = {"boll_window", "boll_k", "breakout_window", "pullback_vol_ratio"}
    assert not (tmpl_keys & set(PARAM_SPACE))


def test_run_daily_cycle_single_template_real_kline(tmp_path):
    """单模板策略在真实 kline.db 路径走通（golden_cross）。"""
    from laap.agi.unified_memory import UnifiedMemory
    from laap.paper_trading import strategy
    db = PaperDB(db_path=str(tmp_path / "pt.db"))
    loop = PaperClosedLoop(db, StubMarketSource(base_prices={"600519": 100.0}),
                           UnifiedMemory(), initial_cash=1_000_000.0,
                           enforce_t1=False)
    # 不注入 ohlcv_map → 走真实 kline.db（沙箱有 600519 真实数据）
    result = loop.run_daily_cycle(
        ["600519"], dict(strategy.STRATEGY_PARAMS), ohlcv_map=None,
        strategy="golden_cross")
    assert result["signals"][0]["action"] in ("buy", "sell", "hold")
    q = result["data_quality"]["600519"]
    assert q["source"] in ("real", "synthetic")
    assert "net_value" in result
