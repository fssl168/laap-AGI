"""PositionMonitor 单测: 止盈/止损/移动止损/T+1 跳过。

覆盖 laap/paper_trading/daily_pipeline.py::QuantDailyPipeline._monitor_positions:
  - stop_loss:   浮亏 ≥ 8% 自动平仓
  - take_profit: 浮盈 ≥ 20% 自动平仓
  - trailing_stop: 从持仓期最高价回撤 ≥ 5% 自动平仓
  - T+1 锁仓: 当日买入持仓跳过, 不平仓
  - hold: 未达阈值持仓不动
"""
import sys, os, time
sys.path.insert(0, r"D:\laap-AGI")

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.paper_service import PaperClosedLoop
from laap.paper_trading.daily_pipeline import QuantDailyPipeline


class FakeMarket:
    def __init__(self, prices):
        self.prices = prices
    def get_price(self, symbol, ts=None):
        return self.prices.get(symbol, 10.0), {"source": "test", "used_fallback": False}


class FakeMemory:
    def add(self, *a, **kw): pass
    def recall(self, *a, **kw): return []
    def encode_experience(self, *a, **kw): return "ep_test"


class FakeEngine:
    def evolve_params(self, **kw):
        return {"best_params": None, "best_train": {}, "gate": None}
    def apply_params_to_code(self, *a, **kw):
        return None


def _tmp_db(name):
    path = os.path.join(os.environ.get("TEMP", "/tmp"), name)
    if os.path.exists(path):
        os.remove(path)
    return path


def _make_loop(db_path, market):
    db = PaperDB(db_path)
    loop = PaperClosedLoop(db=db, market=market, memory=FakeMemory())
    shared = PaperLedger(db, initial_cash=1_000_000.0, enforce_t1=True)
    loop.ledger = shared
    return loop


def _make_position(ledger, symbol, qty, price, ts):
    """预置持仓, 直接控制 entry_ts (fill_order 会用 now, 忽略 signal ts)。"""
    conn = ledger.db.conn()
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        (f"trd_{symbol}_{int(ts)}", f"ord_{symbol}_{int(ts)}", symbol, "buy", qty, price, ts),
    )
    conn.commit()


def _closed_symbols(result):
    return [c["symbol"] for c in result.get("closed", [])]


def _closed_reasons(result):
    return {c["symbol"]: c.get("reason") for c in result.get("closed", [])}


def test_stop_loss():
    """昨日持仓 + 现价 -11% → stop_loss 平仓。"""
    loop = _make_loop(_tmp_db("pm_t1.db"), FakeMarket({"600519": 89.0}))
    _make_position(loop.ledger, "600519", 100, 100.0, time.time() - 90000)
    pipe = QuantDailyPipeline(FakeEngine(), loop, symbols=["600519"])
    r = pipe._monitor_positions()
    assert _closed_reasons(r).get("600519") == "stop_loss"


def test_take_profit():
    """昨日持仓 + 现价 +25% → take_profit 平仓。"""
    loop = _make_loop(_tmp_db("pm_t2.db"), FakeMarket({"600519": 125.0}))
    _make_position(loop.ledger, "600519", 100, 100.0, time.time() - 90000)
    pipe = QuantDailyPipeline(FakeEngine(), loop, symbols=["600519"])
    r = pipe._monitor_positions()
    assert _closed_reasons(r).get("600519") == "take_profit"


def test_t1_lock_skips_today_position():
    """今日持仓 → T+1 锁仓, 即使大跌也不平仓。"""
    loop = _make_loop(_tmp_db("pm_t3.db"), FakeMarket({"600519": 70.0}))
    _make_position(loop.ledger, "600519", 100, 100.0, time.time())
    pipe = QuantDailyPipeline(FakeEngine(), loop, symbols=["600519"])
    r = pipe._monitor_positions()
    skipped = [c for c in r["skipped_t1"] if c["symbol"] == "600519"]
    assert skipped and not r["closed"]


def test_hold_within_thresholds():
    """昨日持仓 + 现价 +5% (未达阈值) → hold。"""
    loop = _make_loop(_tmp_db("pm_t4.db"), FakeMarket({"600519": 105.0}))
    _make_position(loop.ledger, "600519", 100, 100.0, time.time() - 90000)
    pipe = QuantDailyPipeline(FakeEngine(), loop, symbols=["600519"])
    r = pipe._monitor_positions()
    held = [c for c in r["checked"] if c["symbol"] == "600519"]
    assert held and held[0]["status"] == "hold"
    assert not r["closed"]


def test_trailing_stop():
    """tick1 高 115 (+15%), tick2 跌到 108 (回撤 6.1% ≥ 5%) → trailing_stop。"""
    loop = _make_loop(_tmp_db("pm_t5.db"), FakeMarket({"600519": 115.0}))
    _make_position(loop.ledger, "600519", 100, 100.0, time.time() - 90000)
    pipe = QuantDailyPipeline(FakeEngine(), loop, symbols=["600519"])
    pipe._monitor_positions()  # tick1: high=115, +15% < 20% → hold
    loop.market = FakeMarket({"600519": 108.0})
    r2 = pipe._monitor_positions()  # tick2: 回撤 6.1% ≥ 5% → trailing
    assert _closed_reasons(r2).get("600519") == "trailing_stop"
