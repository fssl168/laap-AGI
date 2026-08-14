"""增强1/2/4 测试：K 线加载 + T+1 锁仓 + 参数提取。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.models import DecisionAction, PaperSignal


# ════════════════════════════════════════════════════════════
# 增强1: kline_source
# ════════════════════════════════════════════════════════════

def test_load_price_series_fallback_synthetic():
    """沙箱无真实 kline → 降级合成序列（fallback 默认 True）。"""
    from laap.paper_trading.kline_source import load_price_series
    series = load_price_series(symbol="600519", days=60)
    assert len(series) == 60
    assert all(p > 0 for p in series)


def test_load_price_series_no_fallback_empty():
    from laap.paper_trading.kline_source import load_price_series
    series = load_price_series(symbol="600519", days=60, fallback=False)
    # 沙箱可能无数据 → 空列表（不抛异常）
    assert isinstance(series, list)


# ════════════════════════════════════════════════════════════
# 增强4: T+1 锁仓
# ════════════════════════════════════════════════════════════

@pytest.fixture()
def db(tmp_path):
    return PaperDB(db_path=str(tmp_path / "pt.db"))


def _buy(db, initial_cash=100_000.0, enforce_t1=True) -> tuple:
    ledger = PaperLedger(db, initial_cash=initial_cash, enforce_t1=enforce_t1)
    sig = PaperSignal(symbol="600519", action=DecisionAction.BUY,
                      quantity=100, trigger_price=100.0)
    order = ledger.submit_signal(sig)
    trade = ledger.fill_order(order.id, fill_price=100.0)
    return ledger, trade


def test_t1_lock_rejects_same_day_close(db):
    """enforce_t1=True：当日买入不可平仓。"""
    ledger, trade = _buy(db, enforce_t1=True)
    with pytest.raises(ValueError, match="锁仓"):
        ledger.close_trade(trade.id, exit_price=105.0)


def test_t1_lock_bypass(db):
    """bypass_t1=True 跳过锁仓。"""
    ledger, trade = _buy(db, enforce_t1=True)
    closed = ledger.close_trade(trade.id, exit_price=105.0, bypass_t1=True)
    assert closed.pnl == pytest.approx(500.0)


def test_t1_lock_allows_next_day_close(db):
    """跨自然日可平仓（手动把 entry_ts 改到昨天）。"""
    ledger, trade = _buy(db, enforce_t1=True)
    conn = db.conn()
    conn.execute("UPDATE trades SET entry_ts=? WHERE id=?",
                 (time.time() - 86400 * 2, trade.id))
    conn.commit()
    conn.close()
    closed = ledger.close_trade(trade.id, exit_price=105.0)
    assert closed.pnl == pytest.approx(500.0)


def test_t1_locked_positions(db):
    ledger, trade = _buy(db, enforce_t1=True)
    locked = ledger.t1_locked_positions()
    assert len(locked) == 1
    assert locked[0].id == trade.id


# ════════════════════════════════════════════════════════════
# 增强3: 策略参数提取器
# ════════════════════════════════════════════════════════════

def test_extract_strategy_params_ok():
    from laap.paper_trading.param_extractor import extract_strategy_params
    code = 'STRATEGY_PARAMS = {\n    "short": 3,\n    "long": 15,\n}\n'
    params = extract_strategy_params(code)
    assert params == {"short": 3, "long": 15}


def test_extract_strategy_params_none_when_missing():
    from laap.paper_trading.param_extractor import extract_strategy_params
    assert extract_strategy_params("def f():\n    return 1\n") is None
    assert extract_strategy_params("") is None


def test_extract_strategy_params_syntax_error():
    from laap.paper_trading.param_extractor import extract_strategy_params
    assert extract_strategy_params("def (invalid:") is None


def test_load_baseline_params():
    from laap.paper_trading.param_extractor import load_baseline_params
    p = load_baseline_params()
    assert "short" in p and "long" in p
    assert p["short"] < p["long"]
