"""验证 2026-08-19 修复：盘中止损平仓后即时 snapshot_net_value 落库。

新增逻辑 laap/paper_trading/daily_pipeline.py::monitor_positions —
平仓成功 (result["closed"] 非空) 后调用 ledger.snapshot_net_value(market)，
让 net_values 立即反映回笼现金 + 持仓清零，避免净值曲线停更在平仓前。

本测试隔离在临时 SQLite 库内，不触碰生产 PostgreSQL。
"""
import sys, os, time, tempfile
sys.path.insert(0, r"/vol1/@appdata/trim.hermes/workspace/laap-AGI")

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
    path = os.path.join(tempfile.gettempdir(), name)
    if os.path.exists(path):
        os.remove(path)
    return path


def _preplace_position(ledger, symbol, qty, price, ts):
    conn = ledger.db.conn()
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        (f"trd_{symbol}_{int(ts)}", f"ord_{symbol}_{int(ts)}",
         symbol, "buy", qty, price, ts),
    )
    conn.commit()


def test_stop_loss_writes_net_value_snapshot():
    """昨日持仓日内大跌触发止损平仓 → 平仓后 net_values 出现新快照, equity=0。"""
    loop = PaperClosedLoop(
        db=PaperDB(_tmp_db("pm_snap1.db")),
        market=FakeMarket({"600519": 89.0}),
        memory=FakeMemory(),
    )
    shared = PaperLedger(loop.db, initial_cash=1_000_000.0, enforce_t1=True)
    loop.ledger = shared
    _preplace_position(shared, "600519", 100, 100.0, time.time() - 90000)

    before = shared.db.conn().execute("SELECT COUNT(*) FROM net_values").fetchone()[0]
    pipe = QuantDailyPipeline(FakeEngine(), loop, symbols=["600519"])
    r = pipe._monitor_positions()

    closed = [c["symbol"] for c in r.get("closed", [])]
    assert "600519" in closed, "止损平仓应发生"
    after = shared.db.conn().execute("SELECT COUNT(*) FROM net_values").fetchone()[0]
    assert after > before, "平仓后应新增一条净值快照"

    cash, equity = shared.db.conn().execute(
        "SELECT cash, equity FROM net_values ORDER BY ts DESC LIMIT 1").fetchone()
    assert round(float(equity), 2) == 0.0, "快照 equity 应为 0（持仓已清）"
    assert float(cash) > 0, "现金应回笼为正"


def test_hold_does_not_write_snapshot():
    """持仓未触发平仓 → 不应落新增快照（只在平仓时写）。"""
    loop = PaperClosedLoop(
        db=PaperDB(_tmp_db("pm_snap2.db")),
        market=FakeMarket({"600519": 102.0}),
        memory=FakeMemory(),
    )
    shared = PaperLedger(loop.db, initial_cash=1_000_000.0, enforce_t1=True)
    loop.ledger = shared
    _preplace_position(shared, "600519", 100, 100.0, time.time() - 90000)

    before = shared.db.conn().execute("SELECT COUNT(*) FROM net_values").fetchone()[0]
    pipe = QuantDailyPipeline(FakeEngine(), loop, symbols=["600519"])
    r = pipe._monitor_positions()

    assert not r.get("closed"), "涨幅 <20% 不应平仓"
    after = shared.db.conn().execute("SELECT COUNT(*) FROM net_values").fetchone()[0]
    assert after == before, "未平仓时不应新增净快照"
