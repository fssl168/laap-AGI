# -*- coding: utf-8 -*-
"""2026-08-17 风控漂移整改专项测试：
  1. R4 熔断接线（compute_daily_pnl 真实计算 + 注入 decide_and_trade/run_daily_cycle/news_pipeline）
  2. 批量信号路径补 R5 连亏 / R1 止损距离
  3. decide_and_trade 拒绝落 risk_rejections 审计
  4. 持仓监控 daemon 化（monitor_positions 模块级 + NewsSignalWorker 盘中接入）
"""
import os, sys, time
sys.path.insert(0, r"D:\laap-AGI")

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.ledger import PaperLedger
from laap.paper_trading.models import PaperSignal, DecisionAction
from laap.paper_trading.paper_service import PaperClosedLoop
from laap.paper_trading.risk_gate import (
    RiskGate, record_rejection, compute_loss_streak, compute_daily_pnl)
from laap.paper_trading.daily_pipeline import (
    monitor_positions, QuantDailyPipeline)


class FakeMarket:
    def __init__(self, prices=None, fallback=False):
        self.prices = prices or {}
        self.fallback = fallback
    def get_price(self, symbol, ts=None):
        if self.fallback:
            return self.prices.get(symbol, 10.0), {"source": "stub", "used_fallback": True}
        return self.prices.get(symbol, 10.0), {"source": "test", "used_fallback": False}


class FakeMemory:
    def add(self, *a, **kw): pass
    def recall(self, *a, **kw): return []
    def encode_experience(self, *a, **kw): return "ep_test"


def _tmp_db(name):
    path = os.path.join(os.environ.get("TEMP", "/tmp"), name)
    if os.path.exists(path):
        os.remove(path)
    return path


def _make_loop(db_path, market=None, fee=None):
    db = PaperDB(db_path)
    loop = PaperClosedLoop(db=db, market=market or FakeMarket(),
                           memory=FakeMemory(), fee_model=fee)
    loop.ledger = PaperLedger(db, initial_cash=1_000_000.0, enforce_t1=False)
    return loop


def _fake_trading_time(monkeypatch):
    """把 paper_service.datetime 打成交易时段 14:00（绕过时间门）。"""
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


def _insert_closed_today_loss(db, symbol, qty, entry, exit_, pnl):
    conn = db.conn()
    now = time.time()
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price,"
        " exit_price, pnl, pnl_pct, entry_ts, exit_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (f"t_{symbol}_{int(now)}", f"o_{symbol}_{int(now)}", symbol, "buy",
         qty, entry, exit_, pnl, pnl / (entry * qty), now, now))
    conn.commit()
    conn.close()


def _insert_open_today(db, symbol, qty, entry):
    conn = db.conn()
    now = time.time()
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        (f"to_{symbol}_{int(now)}", f"oo_{symbol}_{int(now)}", symbol, "buy",
         qty, entry, now))
    conn.commit()
    conn.close()


def _insert_consecutive_losses(db, symbol, n):
    """昨日已平仓的连亏（不产生今日未实现，也不计入今日已实现）→ 只触发 R5。"""
    conn = db.conn()
    now = time.time()
    yesterday = now - 86400
    for i in range(n):
        tid = f"cl_{symbol}_{i}_{int(now)}"
        conn.execute(
            "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price,"
            " exit_price, pnl, pnl_pct, entry_ts, exit_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tid, f"o_{tid}", symbol, "buy", 100, 100.0, 95.0,
             -500.0, -0.05, yesterday, yesterday))
        conn.execute(
            "INSERT INTO outcomes (trade_id, pnl_pct, hold_days) VALUES (?,?,?)",
            (tid, -0.01, 1))
    conn.commit()
    conn.close()


def _risk_rows(db, symbol, rule):
    conn = db.conn()
    rows = conn.execute(
        "SELECT * FROM risk_rejections WHERE symbol=? AND rule_id=?",
        (symbol, rule)).fetchall()
    conn.close()
    return rows


# ════════════════════════════════════════════════════════════
# 1. compute_daily_pnl（R4 真实输入）
# ════════════════════════════════════════════════════════════

def test_daily_pnl_realized_today_only():
    db = PaperDB(_tmp_db("dp1.db"))
    _insert_closed_today_loss(db, "600519", 1000, 100.0, 95.0, -5000.0)
    pnl = compute_daily_pnl(db, FakeMarket())
    assert pnl == pytest.approx(-5000.0)


def test_daily_pnl_unrealized_today_new_position():
    db = PaperDB(_tmp_db("dp2.db"))
    _insert_open_today(db, "600519", 1000, 100.0)  # 今日新建仓
    mkt = FakeMarket({"600519": 95.0})             # 现价 -5%
    pnl = compute_daily_pnl(db, mkt)
    assert pnl == pytest.approx(-5000.0)


def test_daily_pnl_ignores_prior_day_position():
    db = PaperDB(_tmp_db("dp3.db"))
    conn = db.conn()
    now = time.time()
    yesterday = now - 86400
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        ("old1", "oo1", "600519", "buy", 1000, 100.0, yesterday))
    conn.commit()
    conn.close()
    mkt = FakeMarket({"600519": 90.0})  # 昨日持仓 -10%，不应计入当日
    assert compute_daily_pnl(db, mkt) == pytest.approx(0.0)


def test_daily_pnl_market_fallback_counts_realized_only():
    db = PaperDB(_tmp_db("dp4.db"))
    _insert_open_today(db, "600519", 1000, 100.0)
    _insert_closed_today_loss(db, "600519", 100, 100.0, 95.0, -500.0)
    pnl = compute_daily_pnl(db, FakeMarket({"600519": 90.0}, fallback=True))
    assert pnl == pytest.approx(-500.0)  # 未实现因行情降级不计


def test_daily_pnl_none_db():
    assert compute_daily_pnl(None, FakeMarket()) == 0.0


# ════════════════════════════════════════════════════════════
# 2. decide_and_trade：R4/R5/R1 接线 + 审计
# ════════════════════════════════════════════════════════════

def test_decide_and_trade_r4_blocks_and_audits(monkeypatch):
    _fake_trading_time(monkeypatch)
    db_path = _tmp_db("da_r4.db")
    loop = _make_loop(db_path)
    # 今日已实现亏损 -30k（总资产 1M 的 3% > 2%）→ R4 熔断
    _insert_closed_today_loss(loop.db, "600519", 3000, 100.0, 90.0, -30000.0)
    res = loop.decide_and_trade("600519", DecisionAction.BUY, 100,
                                trigger_price=100.0, rationale="r4",
                                allow_fallback=True)
    assert res["status"] == "blocked"
    assert "R4" in res["reason"]
    assert len(_risk_rows(loop.db, "600519", "R4")) == 1  # 审计落库


def test_decide_and_trade_r5_blocks_and_audits(monkeypatch):
    _fake_trading_time(monkeypatch)
    db_path = _tmp_db("da_r5.db")
    loop = _make_loop(db_path)
    _insert_consecutive_losses(loop.db, "600519", 3)  # 连亏 3 → R5
    res = loop.decide_and_trade("600519", DecisionAction.BUY, 100,
                                trigger_price=100.0, rationale="r5",
                                allow_fallback=True)
    assert res["status"] == "blocked"
    assert "R5" in res["reason"]
    assert len(_risk_rows(loop.db, "600519", "R5")) == 1


def test_decide_and_trade_r1_blocks_wide_stop(monkeypatch):
    _fake_trading_time(monkeypatch)
    import laap.paper_trading.strategy as strat
    old = strat.STRATEGY_PARAMS.get("stop_loss_pct")
    strat.STRATEGY_PARAMS["stop_loss_pct"] = 0.10  # 10% 止损 > R1 上限 5%
    try:
        db_path = _tmp_db("da_r1.db")
        loop = _make_loop(db_path)
        res = loop.decide_and_trade("600519", DecisionAction.BUY, 100,
                                    trigger_price=100.0, rationale="r1",
                                    allow_fallback=True)
        assert res["status"] == "blocked"
        assert "R1" in res["reason"]
        assert len(_risk_rows(loop.db, "600519", "R1")) == 1
    finally:
        if old is None:
            strat.STRATEGY_PARAMS.pop("stop_loss_pct", None)
        else:
            strat.STRATEGY_PARAMS["stop_loss_pct"] = old


def test_decide_and_trade_normal_buy_passes_with_r1(monkeypatch):
    """默认 stop_loss_pct=0.05 + 小仓位 → 通过全部 R1-R5，正常成交。"""
    _fake_trading_time(monkeypatch)
    db_path = _tmp_db("da_ok.db")
    loop = _make_loop(db_path)
    res = loop.decide_and_trade("600519", DecisionAction.BUY, 100,
                                trigger_price=100.0, rationale="ok",
                                allow_fallback=True)
    assert res.get("trade_id"), f"应成交: {res}"
    assert loop.ledger.open_positions()


# ════════════════════════════════════════════════════════════
# 3. 批量信号路径（run_daily_cycle）R1/R4/R5 接线
# ════════════════════════════════════════════════════════════

_BUY_SERIES = ([12.0 - 0.05 * i for i in range(60)] +   # 长跌 12→9
               [9.0 + 0.02 + 0.06 * i for i in range(12)])  # 短反弹 →9.68


def _buy_ohlcv():
    """能稳定触发 multi_factor buy 的 OHLCV（趋势多+RSI 61<70+末根放量）。"""
    vols = [100000] * len(_BUY_SERIES)
    vols[-1] = 500000
    return [(i, c, c * 1.01, c * 0.99, vols[i]) for i, c in enumerate(_BUY_SERIES)]


def _run_cycle(loop, symbol="600519", position_scale=0.05):
    from laap.paper_trading.strategy import STRATEGY_PARAMS
    params = dict(STRATEGY_PARAMS)
    params["position_scale"] = position_scale
    return loop.run_daily_cycle(
        [symbol], params, ohlcv_map={symbol: _buy_ohlcv()},
        strategy="multi_factor")


def test_run_daily_cycle_r4_blocks_batch(monkeypatch):
    _fake_trading_time(monkeypatch)
    db_path = _tmp_db("dc_r4.db")
    loop = _make_loop(db_path)
    _insert_closed_today_loss(loop.db, "600519", 3000, 100.0, 90.0, -30000.0)
    res = _run_cycle(loop)
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    assert not buys, f"R4 熔断下不应成交: {buys}"
    assert len(_risk_rows(loop.db, "600519", "R4")) >= 1


def test_run_daily_cycle_r5_blocks_batch(monkeypatch):
    _fake_trading_time(monkeypatch)
    db_path = _tmp_db("dc_r5.db")
    loop = _make_loop(db_path)
    _insert_consecutive_losses(loop.db, "600519", 3)
    res = _run_cycle(loop)
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    assert not buys, f"R5 连亏下不应成交: {buys}"
    assert len(_risk_rows(loop.db, "600519", "R5")) >= 1


def test_run_daily_cycle_r1_blocks_wide_stop_batch(monkeypatch):
    _fake_trading_time(monkeypatch)
    db_path = _tmp_db("dc_r1.db")
    loop = _make_loop(db_path)
    from laap.paper_trading.strategy import STRATEGY_PARAMS
    params = dict(STRATEGY_PARAMS)
    params["position_scale"] = 0.05
    params["stop_loss_pct"] = 0.10  # 宽止损 → R1 拒绝
    res = loop.run_daily_cycle(
        ["600519"], params, ohlcv_map={"600519": _buy_ohlcv()},
        strategy="multi_factor")
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    assert not buys, f"R1 宽止损不应成交: {buys}"
    assert len(_risk_rows(loop.db, "600519", "R1")) >= 1


def test_run_daily_cycle_normal_buy_passes(monkeypatch):
    """默认参数（stop_loss_pct=0.05, position_scale=0.10）→ R1-R5 全通过成交。"""
    _fake_trading_time(monkeypatch)
    db_path = _tmp_db("dc_ok.db")
    loop = _make_loop(db_path, market=FakeMarket({"600519": 9.68}))  # 与 buy 收盘一致
    res = _run_cycle(loop, position_scale=0.10)
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    assert buys, f"正常参数应触发买入: {res['signals']}"
    assert loop.ledger.open_positions()


def test_run_daily_cycle_r2_precap_caps_oversized_intent(monkeypatch):
    """R2 预裁剪（2026-08-17）：position_scale=0.5（意图 50%）→ 实际下单被裁到 ≤10%。

    回归：此前批量路径不预裁剪，默认 position_scale 会恒被 R2 拒（系统瘫痪）；
    现对齐 news 路径 build_trade_plan cap_qty，实际下单 = min(意图, R2 上限)。
    """
    _fake_trading_time(monkeypatch)
    db_path = _tmp_db("dc_precap.db")
    loop = _make_loop(db_path)
    loop.market = FakeMarket({"600519": 9.68})  # 与 buy 序列收盘价一致，避免 R2 边界误判
    res = _run_cycle(loop, position_scale=0.5)  # 意图 50% 现金
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    assert buys, f"预裁剪后应仍成交: {res['signals']}"
    positions = loop.ledger.open_positions()
    assert positions, "应产生持仓"
    pos = positions[0]
    pos_value = pos.quantity * pos.entry_price
    total_assets = loop.ledger.cash + sum(
        p.quantity * p.entry_price for p in loop.ledger.open_positions())
    # 单票 ≤ 10% 账户（R2 上限）
    assert pos_value <= 0.10 * total_assets + 1e-6, \
        f"单票 {pos_value} > 10% 账户 {0.10*total_assets}"


def test_decide_and_trade_r2_still_blocks_oversized_direct(monkeypatch):
    """R2 门（防御兜底）：绕过预裁剪直接传超限 qty → 仍拒绝 + 审计。"""
    _fake_trading_time(monkeypatch)
    db_path = _tmp_db("da_r2.db")
    loop = _make_loop(db_path)
    # 1M 账户，买 20 万股 @10 = 200% 账户 → R2 必拒
    res = loop.decide_and_trade("600519", DecisionAction.BUY, 200000,
                                trigger_price=100.0, rationale="r2",
                                allow_fallback=True)
    assert res["status"] == "blocked"
    assert "R2" in res["reason"]
    assert len(_risk_rows(loop.db, "600519", "R2")) >= 1


def test_run_daily_cycle_r3_total_portfolio_blocks_batch(monkeypatch):
    """R3 总仓位：持有其他标的 48% 时再买 5% → 总 53% > 50% 拒绝（2026-08-17 修复）。

    回归：此前 existing_pos_value 只算同标的 → R3 忽略其他标的持仓。
    """
    _fake_trading_time(monkeypatch)
    db_path = _tmp_db("dc_r3.db")
    loop = _make_loop(db_path)
    conn = loop.db.conn()
    ts = time.time() - 86400
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        ("r3_exist", "or3_exist", "000001", "buy", 48000, 10.0, ts))  # 48 万 = 48%
    conn.commit()
    conn.close()
    loop.ledger.cash -= 480000  # 已持仓占用现金
    res = _run_cycle(loop)  # 新增 ~5% → 总 ~53%
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    assert not buys, f"R3 总仓超限不应成交: {buys}"
    assert len(_risk_rows(loop.db, "600519", "R3")) >= 1


# ════════════════════════════════════════════════════════════
# 4. 持仓监控 daemon 化：monitor_positions 模块级 + NewsSignalWorker 接入
# ════════════════════════════════════════════════════════════

def test_monitor_positions_module_stop_loss():
    db_path = _tmp_db("mon_mod.db")
    loop = _make_loop(db_path)
    # 昨日持仓（非 T+1 锁仓）
    conn = loop.db.conn()
    ts = time.time() - 90000
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        ("mod1", "omo1", "600519", "buy", 100, 100.0, ts))
    conn.commit()
    conn.close()
    state = {}
    r = monitor_positions(loop, state)
    assert [c["symbol"] for c in r["closed"]] == ["600519"]
    assert r["closed"][0]["reason"] == "stop_loss"  # 现价 89（-11% ≤ -5%）


def test_news_worker_monitor_wiring(monkeypatch):
    """NewsSignalWorker._monitor_open_positions 盘中自动平仓 + last_monitor 记录。"""
    from laap.paper_trading.news_pipeline import NewsSignalWorker, NewsSignalPipeline
    db_path = _tmp_db("worker_mon.db")
    loop = _make_loop(db_path)
    conn = loop.db.conn()
    ts = time.time() - 90000
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        ("wmon1", "owm1", "600519", "buy", 100, 100.0, ts))
    conn.commit()
    conn.close()

    pipe = NewsSignalPipeline(loop=loop, db=loop.db)
    worker = NewsSignalWorker(pipe, symbols=["600519"], enabled=True)
    m = worker._monitor_open_positions()
    assert m is not None
    assert [c["symbol"] for c in m["closed"]] == ["600519"]
    assert worker.last_monitor is m  # 跨 tick 状态保存


def test_news_worker_monitor_state_persists_high(monkeypatch):
    """移动止损高水位跨 tick 保存在 worker._monitor_state。"""
    from laap.paper_trading.news_pipeline import NewsSignalWorker, NewsSignalPipeline
    db_path = _tmp_db("worker_high.db")
    loop = _make_loop(db_path)
    conn = loop.db.conn()
    ts = time.time() - 90000
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        ("wh1", "owh1", "600519", "buy", 100, 100.0, ts))
    conn.commit()
    conn.close()
    pipe = NewsSignalPipeline(loop=loop, db=loop.db)
    worker = NewsSignalWorker(pipe, symbols=["600519"], enabled=True)
    loop.market = FakeMarket({"600519": 115.0})  # tick1 高 115
    worker._monitor_open_positions()
    key = f"pos_high_wh1"
    assert worker._monitor_state.get(key) == pytest.approx(115.0)
    loop.market = FakeMarket({"600519": 108.0})  # tick2 回撤 6.1% ≥ 5%
    m = worker._monitor_open_positions()
    assert any(c["symbol"] == "600519" and c.get("reason") == "trailing_stop"
               for c in m["closed"])
