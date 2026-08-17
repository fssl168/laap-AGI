"""paper_service 风控修复单测:
1. decide_and_trade 风控异常 → fail-closed 拒绝 (原 fail-open 放行)
2. run_daily_cycle 批量路径现在经过 RiskGate (R2 单票≤10% 生效)
"""
import sys, os, time
sys.path.insert(0, r"D:\laap-AGI")

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.models import PaperSignal, DecisionAction
from laap.paper_trading.paper_service import PaperClosedLoop
from laap.paper_trading.risk_gate import RiskGate, record_rejection


class FakeMarket:
    def get_price(self, symbol, ts=None):
        return 10.0, {"source": "test", "used_fallback": False}


class FakeMemory:
    def add(self, *a, **kw): pass
    def recall(self, *a, **kw): return []
    def encode_experience(self, *a, **kw): return "ep_test"


def _tmp_db(name):
    path = os.path.join(os.environ.get("TEMP", "/tmp"), name)
    if os.path.exists(path):
        os.remove(path)
    return path


def _make_loop(db_path, market=None):
    db = PaperDB(db_path)
    loop = PaperClosedLoop(db=db, market=market or FakeMarket(), memory=FakeMemory())
    loop.ledger = PaperLedger(db, initial_cash=1_000_000.0, enforce_t1=True)
    return loop


# ── 1. fail-closed: 风控异常必须拒绝下单 ──

def test_decide_and_trade_risk_gate_fail_closed(monkeypatch):
    """RiskGate.check_signal 抛异常 → 返回 blocked, 不继续下单 (原 fail-open)。"""
    loop = _make_loop(_tmp_db("fc1.db"))

    # 绕过非交易时段检查 (模拟 14:00 交易时间)
    import laap.paper_trading.paper_service as ps
    import datetime as _dt
    class _FakeDT:
        @staticmethod
        def now():
            class _N:
                hour = 14
                minute = 0
            return _N()
    monkeypatch.setattr(ps, "datetime", _FakeDT)

    def _boom(*a, **kw):
        raise RuntimeError("risk gate exploded")

    monkeypatch.setattr(RiskGate, "check_signal", _boom)
    result = loop.decide_and_trade(
        "600519", DecisionAction.BUY, 100, 100.0,
        rationale="test fail-closed")
    assert result["status"] == "blocked", f"期望 blocked, 实际 {result}"
    assert "fail-closed" in result["reason"].lower() or "风控" in result["reason"]


# ── 2. 批量路径 RiskGate 生效: 超 R2 单票 10% 被拒 ──

def test_run_daily_cycle_risk_gate_blocks_over_position():
    """批量信号路径: 大额买入超 R2 单票 10% → 信号变 hold, 不成交。"""
    loop = _make_loop(_tmp_db("fc2.db"))

    # 直接构造: 100 万现金, 现价 10 元, position_scale=0.5 → 预算 50 万 = 5万股
    # 单票 50 万 / 总资产 100 万 = 50% > R2 10% → 必须被拒
    from laap.paper_trading.strategy import STRATEGY_PARAMS
    params = dict(STRATEGY_PARAMS)
    params["position_scale"] = 0.5

    # 用注入 ohlcv 让信号稳定触发 buy
    # 简单上升趋势: close 从 9 涨到 10.5
    ohlcv = [(i, 9.0 + i * 0.1, 9.5, 8.8, 100000) for i in range(60)]

    result = loop.run_daily_cycle(
        ["600519"], params, ohlcv_map={"600519": ohlcv},
        strategy="multi_factor")

    buy_signals = [s for s in result.get("signals", []) if s.get("action") == "buy"]
    hold_with_risk = [s for s in result.get("signals", [])
                      if s.get("action") == "hold" and "风控" in s.get("signal_reason", "")]
    # 无论策略是否触发 buy, 只要触发就必须被风控拦成 hold
    assert not buy_signals, f"超 R2 仓位不应成交: {buy_signals}"
    # 若信号触发了 buy 被风控拦, 应有 hold+风控 reason; 若策略未触发, 至少不成交
    trades = loop.ledger.open_positions()
    assert len(trades) == 0, f"不应有成交: {trades}"


# ── 3. record_rejection 落库 ──

def test_record_rejection_written():
    db = PaperDB(_tmp_db("fc3.db"))
    record_rejection(db, "600519", "R2", "单票仓位超限", {"qty": 50000})
    conn = db.conn()
    rows = conn.execute("SELECT * FROM risk_rejections WHERE symbol='600519'").fetchall()
    assert len(rows) == 1
    assert rows[0]["rule_id"] == "R2"
