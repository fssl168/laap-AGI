# -*- coding: utf-8 -*-
"""risk_gate.py 风控门测试（R1-R5）。"""
import sqlite3
import pytest

from laap.paper_trading.risk_gate import (
    RiskGate, record_rejection, compute_loss_streak)


class _Plan:
    def __init__(self, action="buy", quantity=1000, buy_price=10.0,
                 stop_loss=9.5, buy_time="now"):
        self.action = action
        self.quantity = quantity
        self.buy_price = buy_price
        self.stop_loss = stop_loss
        self.buy_time = buy_time


def _gate():
    return RiskGate()


def test_pass_path():
    # 总资产 1M，买 1000 股 @10 = 10k（1%），止损 5% 内
    plan = _Plan(quantity=1000, buy_price=10.0, stop_loss=9.5)
    ok, rule, reason = _gate().check_signal(
        plan, cash=500_000, total_assets=1_000_000)
    assert ok is True
    assert rule == ""


def test_r1_stop_loss_too_far():
    plan = _Plan(quantity=1000, buy_price=10.0, stop_loss=8.0)  # 距离 20%
    ok, rule, reason = _gate().check_signal(
        plan, cash=500_000, total_assets=1_000_000)
    assert ok is False
    assert rule == "R1"


def test_r1_stop_loss_above_entry_rejected():
    """止损位高于成交价（长仓非法）→ R1 拒绝（计划价位与成交价不一致防护）。"""
    plan = _Plan(quantity=1000, buy_price=10.0, stop_loss=12.0)  # 止损 > 成交价
    ok, rule, reason = _gate().check_signal(
        plan, cash=500_000, total_assets=1_000_000)
    assert ok is False
    assert rule == "R1"


def test_r2_single_pos_over_cap():
    # 买 20000 股 @10 = 200k = 20% > 10%
    plan = _Plan(quantity=20000, buy_price=10.0, stop_loss=9.5)
    ok, rule, _ = _gate().check_signal(
        plan, cash=500_000, total_assets=1_000_000)
    assert ok is False
    assert rule == "R2"


def test_r2_position_scale_max_tighter():
    # 1000 股 @10 = 10k = 1%，但 position_scale_max=0.005 → 5k 上限 → 拒
    plan = _Plan(quantity=1000, buy_price=10.0, stop_loss=9.5)
    ok, rule, _ = _gate().check_signal(
        plan, cash=500_000, total_assets=1_000_000, position_scale_max=0.005)
    assert ok is False
    assert rule == "R2"


def test_r3_total_pos_over_cap():
    # 单票 5000 股@10=50k(5%≤10%) 通过 R2；已有 48% + 新增 5% → 53% > 50% → R3
    plan = _Plan(quantity=5000, buy_price=10.0, stop_loss=9.5)
    ok, rule, _ = _gate().check_signal(
        plan, cash=300_000, total_assets=1_000_000,
        existing_pos_value=480_000)
    assert ok is False
    assert rule == "R3"


def test_r4_daily_loss_circuit_breaker():
    plan = _Plan(quantity=1000, buy_price=10.0, stop_loss=9.5)
    ok, rule, _ = _gate().check_signal(
        plan, cash=500_000, total_assets=1_000_000,
        daily_pnl=-30_000)  # -3% ≥ 2% 熔断
    assert ok is False
    assert rule == "R4"


def test_r5_consecutive_losses():
    plan = _Plan(quantity=1000, buy_price=10.0, stop_loss=9.5)
    ok, rule, _ = _gate().check_signal(
        plan, cash=500_000, total_assets=1_000_000, consecutive_losses=3)
    assert ok is False
    assert rule == "R5"


def test_not_buy_plan():
    plan = _Plan(action="hold")
    ok, rule, _ = _gate().check_signal(
        plan, cash=500_000, total_assets=1_000_000)
    assert ok is False
    assert rule == "NOT_BUY"


class _FileDB:
    def __init__(self, path):
        self._path = path
        c = sqlite3.connect(str(path))
        c.execute("CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, symbol TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS outcomes ("
                  "trade_id TEXT PRIMARY KEY, pnl_pct REAL)")
        c.commit()
        c.close()

    def conn(self):
        c = sqlite3.connect(str(self._path))
        c.row_factory = sqlite3.Row
        return c


def test_record_rejection(tmp_path):
    db = _FileDB(tmp_path / "rg.db")
    record_rejection(db, "600519", "R2", "单票超仓", {"qty": 20000})
    conn = db.conn()
    rows = conn.execute("SELECT * FROM risk_rejections").fetchall()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "600519"
    assert rows[0]["rule_id"] == "R2"
    conn.close()


def test_compute_loss_streak(tmp_path):
    db = _FileDB(tmp_path / "rg2.db")
    c = db.conn()
    c.execute("INSERT INTO trades (id, symbol) VALUES ('t1','600519')")
    c.execute("INSERT INTO trades (id, symbol) VALUES ('t2','600519')")
    c.execute("INSERT INTO trades (id, symbol) VALUES ('t3','600519')")
    c.execute("INSERT INTO trades (id, symbol) VALUES ('t4','600519')")
    # 连亏 2 笔后盈利 1 笔 → streak=0；倒序最新在前
    c.execute("INSERT INTO outcomes (trade_id, pnl_pct) VALUES ('t4', 0.02)")   # 最新：盈利
    c.execute("INSERT INTO outcomes (trade_id, pnl_pct) VALUES ('t3', -0.01)")  # 亏
    c.execute("INSERT INTO outcomes (trade_id, pnl_pct) VALUES ('t2', -0.02)")  # 亏
    c.execute("INSERT INTO outcomes (trade_id, pnl_pct) VALUES ('t1', 0.01)")   # 盈（最早）
    # 其他标的独立验证（symbol 过滤）
    c.execute("INSERT INTO trades (id, symbol) VALUES ('t5','000001')")
    c.execute("INSERT INTO outcomes (trade_id, pnl_pct) VALUES ('t5', -0.03)")
    c.commit()
    c.close()
    # 按 rowid 倒序最新在前：t4(盈) → streak=0
    assert compute_loss_streak(db, "600519") == 0
    # 000001 最新一笔亏损 → streak=1
    assert compute_loss_streak(db, "000001") == 1
