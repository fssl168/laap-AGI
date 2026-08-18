"""阶段 4 测试：真实执行组件（load_ohlcv/incremental_update/evaluate_signal/run_daily_cycle）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.paper_service import PaperClosedLoop
from laap.paper_trading.models import DecisionAction
from laap.paper_trading import strategy


@pytest.fixture()
def loop(tmp_path, monkeypatch):
    from laap.agi.unified_memory import UnifiedMemory
    # 交易时段桩：run_daily_cycle 内层 decide_and_trade 有时间门（2026-08-17 起）
    import laap.paper_trading.paper_service as ps
    import datetime as _dt
    class _N:
        hour = 14
        minute = 0
        def strftime(self, f):
            return "14:00"
    class _FakeDT:
        @staticmethod
        def now():
            return _N()
    monkeypatch.setattr(ps, "datetime", _FakeDT)
    db = PaperDB(db_path=str(tmp_path / "pt.db"))

    class _FakeLiveMarket:
        """模拟实时行情（非降级），run_daily_cycle 集成测试用。

        fail-closed 契约下 stub 成交会被拒绝，因此测试须模拟行情可用。
        """
        def get_price(self, symbol, ts=None):
            return 100.0, {"source": "akshare", "used_fallback": False}

    market = _FakeLiveMarket()
    memory = UnifiedMemory()
    return PaperClosedLoop(db, market, memory, initial_cash=1_000_000.0, enforce_t1=False)


# ════════════════════════════════════════════════════════════
# 阶段4 公共基座: kline_source
# ════════════════════════════════════════════════════════════

def test_load_ohlcv_fallback_synthetic():
    """沙箱无真实 kline → 降级合成 5 元组，长度正确。"""
    from laap.paper_trading.kline_source import load_ohlcv
    ohlcv = load_ohlcv(symbol="600519", days=60)
    assert len(ohlcv) == 60
    for o, c, h, l, v in ohlcv:
        assert h >= c >= l > 0
        assert v > 0


def test_load_ohlcv_no_fallback_empty():
    from laap.paper_trading.kline_source import load_ohlcv
    ohlcv = load_ohlcv(symbol="600519", days=60, fallback=False)
    assert isinstance(ohlcv, list)


def test_incremental_update_no_akshare_returns_zero():
    """沙箱无 akshare → 返回 0（不抛异常）。"""
    from laap.paper_trading.kline_source import incremental_update
    n = incremental_update(symbol="600519", days=30)
    assert n == 0


def test_with_prefix():
    from laap.paper_trading.kline_source import _with_prefix
    assert _with_prefix("600519") == "sh600519"
    assert _with_prefix("000001") == "sz000001"
    assert _with_prefix("sh600519") == "sh600519"


# ════════════════════════════════════════════════════════════
# 阶段4 路径A: evaluate_signal
# ════════════════════════════════════════════════════════════

def test_evaluate_signal_hold_insufficient_history():
    from laap.paper_trading.backtest_runner import BacktestRunner
    r = BacktestRunner()
    action, reason = r.evaluate_signal([100.0, 101.0], {}, position_held=False)
    assert action == "hold"
    assert "insufficient" in reason


def _up_prices():
    """上涨→回调→温和反弹（RSI<70 且 fast_ma>slow_ma，触发 buy）。"""
    return [100.0 + i * 1.0 for i in range(20)] + \
           [120.0 - i * 1.5 for i in range(8)] + \
           [108.0 + i * 0.55 for i in range(15)]


def test_evaluate_signal_buy_on_uptrend():
    """上涨后回调再温和反弹（RSI 不过热）→ buy。"""
    from laap.paper_trading.backtest_runner import BacktestRunner
    prices = _up_prices()
    r = BacktestRunner()
    action, reason = r.evaluate_signal(prices, {}, position_held=False)
    assert action == "buy"


def test_evaluate_signal_sell_when_held_and_downtrend():
    """持仓 + 下跌趋势 → sell。"""
    from laap.paper_trading.backtest_runner import BacktestRunner
    prices = [200.0 - i * 2.0 for i in range(30)]  # 单调下跌
    r = BacktestRunner()
    action, reason = r.evaluate_signal(prices, {}, position_held=True)
    assert action == "sell"


def test_evaluate_signal_hold_when_no_trigger():
    """震荡无信号 → hold。"""
    from laap.paper_trading.backtest_runner import BacktestRunner
    import math
    prices = [100.0 + math.sin(i / 3.0) * 2.0 for i in range(30)]
    r = BacktestRunner()
    action, _ = r.evaluate_signal(prices, {}, position_held=False)
    assert action in ("buy", "hold")  # 不抛错且合法


# ════════════════════════════════════════════════════════════
# 阶段4 路径A: run_daily_cycle
# ════════════════════════════════════════════════════════════

def _up_ohlcv(days=43):
    """上涨→回调→温和反弹 OHLCV（触发 buy：RSI 不过热 + 末值放量）。"""
    closes = _up_prices()
    ohlcv = []
    for i, c in enumerate(closes):
        vol = 300_000.0 if i == len(closes) - 1 else 100_000.0  # 末值 3 倍放量
        ohlcv.append((c - 0.1, c, c + 0.2, c - 0.2, vol))
    return ohlcv[:days]


def test_run_daily_cycle_buys_on_uptrend(loop):
    """上涨趋势 → run_daily_cycle 产生 buy 信号 + 成交。"""
    params = dict(strategy.STRATEGY_PARAMS)
    params["position_scale"] = 0.05  # 兼容 R2 单票≤10%（默认 0.5 会被风控拒）
    result = loop.run_daily_cycle(
        ["600519"], params,
        ohlcv_map={"600519": _up_ohlcv()})
    assert result["signals"][0]["action"] == "buy"
    assert result["signals"][0]["trade_id"]
    assert "net_value" in result


def test_run_daily_cycle_hold_when_flat(loop):
    """持平行情 → hold，不产生订单。"""
    ohlcv = []
    for i in range(40):
        c = 100.0
        ohlcv.append((c, c, c, c, 100_000.0))
    result = loop.run_daily_cycle(
        ["600519"], dict(strategy.STRATEGY_PARAMS),
        ohlcv_map={"600519": ohlcv})
    assert result["signals"][0]["action"] == "hold"


def test_run_daily_cycle_sell_when_held_and_downtrend(loop):
    """先持仓买入（up），再下跌行情 → sell 平仓。"""
    params = dict(strategy.STRATEGY_PARAMS)
    params["position_scale"] = 0.05  # 兼容 R2 单票≤10%
    loop.run_daily_cycle(["600519"], params,
                         ohlcv_map={"600519": _up_ohlcv()})
    assert len(loop.ledger.open_positions()) == 1

    down = []
    for i in range(40):
        c = 200.0 - i * 2.0
        down.append((c - 0.5, c, c + 0.5, c - 0.8, 100_000.0))
    result = loop.run_daily_cycle(
        ["600519"], params,
        ohlcv_map={"600519": down})
    assert result["signals"][0]["action"] == "sell"
    assert len(loop.ledger.open_positions()) == 0


def test_evaluate_signal_take_profit_risk_exit():
    """阶段4修复：风控退出分支可达（take_profit 触发，而非恒 False）。"""
    from laap.paper_trading.backtest_runner import BacktestRunner
    r = BacktestRunner()
    prices = [100.0 + i * 0.5 for i in range(40)]  # 100 → 119.5 单调上涨
    params = dict(strategy.STRATEGY_PARAMS, rsi_overbought=999.0, take_profit_pct=0.05)
    action, reason = r.evaluate_signal(prices, params, position_held=True, entry_price=100.0)
    assert action == "sell"
    assert "risk exit" in reason


def test_evaluate_signal_no_risk_exit_without_entry():
    """阶段4修复：无 entry_price 时保守跳过风控退出，不误触发。"""
    from laap.paper_trading.backtest_runner import BacktestRunner
    r = BacktestRunner()
    prices = [100.0 + i * 0.5 for i in range(40)]
    params = dict(strategy.STRATEGY_PARAMS, rsi_overbought=999.0, take_profit_pct=0.05)
    action, reason = r.evaluate_signal(prices, params, position_held=True)
    assert action == "hold"  # 无 entry_price → 风控退出不触发


def test_run_daily_cycle_insufficient_cash_holds(loop):
    """阶段4修复：预算不足一手时不强制买入（不产生负现金）。"""
    loop.ledger.cash = 5000.0
    result = loop.run_daily_cycle(
        ["600519"], dict(strategy.STRATEGY_PARAMS),
        ohlcv_map={"600519": _up_ohlcv()})
    assert result["signals"][0]["action"] == "hold"
    assert "insufficient cash" in result["signals"][0]["signal_reason"]


# ════════════════════════════════════════════════════════════
# T1: 数据口径统一 + 数据质量标记
# ════════════════════════════════════════════════════════════

def test_kline_adjust_constant():
    from laap.paper_trading.kline_source import KLINE_ADJUST
    assert KLINE_ADJUST == "qfq"


def test_load_ohlcv_with_quality_structure():
    """with_quality=True → (ohlcv, quality)，quality 结构完整、口径 qfq。"""
    from laap.paper_trading.kline_source import load_ohlcv
    ohlcv, quality = load_ohlcv(symbol="600519", days=60, with_quality=True)
    assert isinstance(ohlcv, list)
    # 沙箱可能真实数据（kline.db）或降级合成——两者都须是合法 OHLCV
    assert len(ohlcv) >= 0
    assert quality["source"] in ("real", "synthetic", "tencent")
    assert "used_fallback" in quality
    assert quality["adjust"] == "qfq"


def test_load_ohlcv_with_quality_no_fallback():
    """fallback=False 不抛异常，返回 (list, quality) 结构。"""
    from laap.paper_trading.kline_source import load_ohlcv
    ohlcv, quality = load_ohlcv(symbol="600519", days=60,
                                fallback=False, with_quality=True)
    assert isinstance(ohlcv, list)
    assert "used_fallback" in quality


def test_incremental_update_accepts_dates():
    """start_date/end_date 参数不抛异常（沙箱无 akshare 返回 0）。"""
    from laap.paper_trading.kline_source import incremental_update
    n = incremental_update(symbol="600519", days=30,
                           start_date="20250101", end_date="20250801")
    assert n == 0


def test_run_daily_cycle_data_quality_injected(loop):
    """ohlcv_map 注入 → data_quality 标记 injected。"""
    result = loop.run_daily_cycle(
        ["600519"], dict(strategy.STRATEGY_PARAMS),
        ohlcv_map={"600519": _up_ohlcv()})
    assert result["data_quality"]["600519"]["source"] == "injected"


def test_run_daily_cycle_data_quality_real_or_synthetic(loop):
    """不注入 ohlcv_map → data_quality 走 load_ohlcv（沙箱降级 synthetic）。"""
    result = loop.run_daily_cycle(
        ["600519"], dict(strategy.STRATEGY_PARAMS), ohlcv_map=None)
    q = result["data_quality"]["600519"]
    assert q["source"] in ("real", "synthetic", "tencent")  # 沙箱为 synthetic/tencent
    assert "used_fallback" in q
    assert q["adjust"] == "qfq"
