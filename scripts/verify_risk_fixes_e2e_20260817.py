#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""风控漂移整改全链路 E2E 逐项核验（2026-08-17）。

在临时 DB + 真实 PaperClosedLoop/PaperLedger/risk_gate 上，逐项验证：
  A. 正常批量信号 → R1-R5 全放行 → 下单成交（含费扣费）
  B. R1 宽止损拒绝 + risk_rejections 审计
  C. R2 单票超 10% 拒绝 + 审计
  D. R3 总仓超 50% 拒绝 + 审计
  E. R4 当日亏损熔断拒绝 + 审计
  F. R5 连亏停开仓拒绝 + 审计
  G. 持仓监控：止盈/止损/移动止损自动平仓（monitor_positions + NewsSignalWorker daemon）
  H. fail-closed：风控异常 → 拒绝下单（绝不静默放行）
  I. decide_and_trade 拒绝落 risk_rejections（quant_bridge/trading_self 直达路径）

运行：TMPDIR=/tmp python scripts/verify_risk_fixes_e2e_20260817.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TMPDIR", "/tmp")

from laap.paper_trading.db import PaperDB
from laap.paper_trading.paper_service import PaperClosedLoop
from laap.paper_trading.models import DecisionAction
from laap.paper_trading.market_source import StubMarketSource
from laap.paper_trading.risk_gate import (
    RiskGate, record_rejection, compute_loss_streak, compute_daily_pnl)
from laap.paper_trading.daily_pipeline import monitor_positions
from laap.paper_trading.strategy import STRATEGY_PARAMS

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class FakeMarket:
    """非降级行情源（used_fallback=False），供真实成交。"""
    def __init__(self, prices=None):
        self.prices = prices or {}
    def get_price(self, symbol, ts=None):
        return self.prices.get(symbol, 100.0), {"source": "test", "used_fallback": False}


class FakeMemory:
    def add(self, *a, **kw): pass
    def recall(self, *a, **kw): return []
    def encode_experience(self, *a, **kw):
        # 对齐 UnifiedMemory 生产契约: 返回 dict {"episode_id": ...}
        # (原返回 str 导致 encode_lesson 里 result.get() 报
        #  'str' object has no attribute 'get' —— 测试桩不匹配生产契约)
        return {"episode_id": "ep_test"}


def _mk(db_name, price=100.0):
    p = os.path.join(os.environ.get("TEMP", "/tmp"), db_name)
    if os.path.exists(p):
        os.remove(p)
    db = PaperDB(db_path=p)
    loop = PaperClosedLoop(db=db, market=FakeMarket({}), memory=FakeMemory(),
                           initial_cash=1_000_000.0, enforce_t1=False)
    loop.market = FakeMarket({"600519": price})
    return loop


def _buy_ohlcv():
    closes = [12.0 - 0.05 * i for i in range(60)] + [9.0 + 0.02 + 0.06 * i for i in range(12)]
    vols = [100000] * len(closes)
    vols[-1] = 500000
    return [(i, c, c * 1.01, c * 0.99, vols[i]) for i, c in enumerate(closes)]


def _params(**over):
    p = dict(STRATEGY_PARAMS)
    p["position_scale"] = 0.05
    p.update(over)
    return p


def _insert_closed_loss_today(db, symbol, pnl):
    conn = db.conn(); now = time.time()
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price,"
        " exit_price, pnl, pnl_pct, entry_ts, exit_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (f"e2e_r4_{int(now)}", f"o_r4_{int(now)}", symbol, "buy",
         3000, 100.0, 90.0, pnl, pnl / 300000.0, now, now))
    conn.commit(); conn.close()


def _insert_streak(db, symbol, n):
    conn = db.conn(); now = time.time(); y = now - 86400
    for i in range(n):
        tid = f"e2e_cl_{symbol}_{i}_{int(now)}"
        conn.execute(
            "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price,"
            " exit_price, pnl, pnl_pct, entry_ts, exit_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tid, f"o_{tid}", symbol, "buy", 100, 100.0, 95.0, -500.0, -0.05, y, y))
        conn.execute("INSERT INTO outcomes (trade_id, pnl_pct, hold_days) VALUES (?,?,?)",
                     (tid, -0.01, 1))
    conn.commit(); conn.close()


def _rejections(db, symbol, rule):
    conn = db.conn()
    rows = conn.execute(
        "SELECT rule_id, reason FROM risk_rejections WHERE symbol=? AND rule_id=?",
        (symbol, rule)).fetchall()
    conn.close()
    return rows


def _run_daily(loop, symbol="600519", **params_over):
    return loop.run_daily_cycle([symbol], _params(**params_over),
                                ohlcv_map={symbol: _buy_ohlcv()},
                                strategy="multi_factor")


# ════════════════════════════════════════════════════════════
def main():
    # 交易时段桩：decide_and_trade 有时间门，E2E 全程模拟盘中 14:00
    import laap.paper_trading.paper_service as _ps
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
    _ps.datetime = _FakeDT

    print("=" * 70)
    print("风控漂移整改 全链路 E2E 逐项核验")
    print("=" * 70)

    # ── A. 正常批量信号 → R1-R5 全放行 → 成交 ──
    print("\n[A] 正常批量信号 → 预算→RiskGate→下单成交（R1-R5 全放行，含费扣费）")
    loop = _mk("e2e_a.db", price=9.68)
    res = _run_daily(loop)
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    check("批量信号产生 buy", bool(buys), f"signals={[s['action'] for s in res['signals']]}")
    positions = loop.ledger.open_positions()
    check("买入真实成交", len(positions) == 1, f"open={len(positions)}")
    check("R1-R5 无拒绝（正常路径）", _rejections(loop.db, "600519", "R1") == []
          and _rejections(loop.db, "600519", "R4") == []
          and _rejections(loop.db, "600519", "R5") == [],
          "无 R1/R4/R5 审计行")
    if positions:
        pos = positions[0]
        check("成交价/数量合理", pos.quantity >= 100 and pos.entry_price > 0,
              f"qty={pos.quantity} entry={pos.entry_price:.2f}")

    # ── B. R1 宽止损 → 拒绝 + 审计 ──
    print("\n[B] R1 止损距离>5% → 拒绝 + risk_rejections")
    loop = _mk("e2e_b.db", price=9.68)
    res = _run_daily(loop, stop_loss_pct=0.10)
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    check("R1 拒绝买入", not buys, f"signals={[s['action'] for s in res['signals']]}")
    check("R1 审计落库", len(_rejections(loop.db, "600519", "R1")) >= 1,
          f"rows={len(_rejections(loop.db, '600519', 'R1'))}")

    # ── C. R2 单票上限：批量预裁剪生效 + 直接超限仍被门拒 ──
    print("\n[C] R2 单票≤10%：批量预裁剪生效；直接超限仍被拒 + 审计")
    loop = _mk("e2e_c.db", price=9.68)
    res = _run_daily(loop, position_scale=0.5)  # 意图 50% → 预裁剪到 ≤10%
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    check("批量意图 50% → 仍成交（预裁剪）", bool(buys),
          f"signals={[s['action'] for s in res['signals']]}")
    positions = loop.ledger.open_positions()
    if positions:
        pos = positions[0]
        pv = pos.quantity * pos.entry_price
        ta = loop.ledger.cash + sum(p.quantity * p.entry_price for p in loop.ledger.open_positions())
        check("单票 ≤10% 账户", pv <= 0.10 * ta + 1e-6, f"pos={pv:.0f} cap={0.10*ta:.0f}")
    # 直接超限 qty（绕过预裁剪）→ R2 门仍拒绝 + 审计
    loop2 = _mk("e2e_c2.db", price=100.0)
    r2 = loop2.decide_and_trade("600519", DecisionAction.BUY, 200000,
                                trigger_price=100.0, rationale="r2", allow_fallback=True)
    check("直接超限 → R2 拒绝", r2.get("status") == "blocked" and "R2" in r2.get("reason", ""),
          f"reason={r2.get('reason','')[:40]}")
    check("R2 审计落库", len(_rejections(loop2.db, "600519", "R2")) >= 1)

    # ── D. R3 总仓超 50% → 拒绝 + 审计 ──
    print("\n[D] R3 总仓>50% → 拒绝 + risk_rejections")
    loop = _mk("e2e_d.db", price=9.68)
    # 预置 48% 已持仓（昨日记账的未平仓头寸，用账本内存现金扣减）
    conn = loop.db.conn(); now = time.time(); y = now - 86400
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        ("e2e_exist", "o_exist", "000001", "buy", 48000, 10.0, y))
    conn.commit(); conn.close()
    loop.ledger.cash -= 480000  # 已持仓占用现金
    res = _run_daily(loop, position_scale=0.05)  # 新增 ~5% → 总 ~53% > 50%
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    check("R3 拒绝买入", not buys, f"signals={[s['action'] for s in res['signals']]}")
    check("R3 审计落库", len(_rejections(loop.db, "600519", "R3")) >= 1)

    # ── E. R4 当日亏损熔断 → 拒绝 + 审计 ──
    print("\n[E] R4 当日已实现亏损 -3% → 熔断拒绝 + 审计")
    loop = _mk("e2e_e.db", price=9.68)
    _insert_closed_loss_today(loop.db, "600519", -30000)  # -3% of 1M
    check("compute_daily_pnl 识别当日亏损", compute_daily_pnl(loop.db, loop.market) == -30000.0,
          f"daily_pnl={compute_daily_pnl(loop.db, loop.market)}")
    res = _run_daily(loop)
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    check("R4 熔断拒绝买入", not buys, f"signals={[s['action'] for s in res['signals']]}")
    check("R4 审计落库", len(_rejections(loop.db, "600519", "R4")) >= 1)

    # ── F. R5 连亏停开仓 → 拒绝 + 审计 ──
    print("\n[F] R5 连亏 3 笔 → 停开仓拒绝 + 审计")
    loop = _mk("e2e_f.db", price=9.68)
    _insert_streak(loop.db, "600519", 3)
    check("compute_loss_streak=3", compute_loss_streak(loop.db, "600519") == 3,
          f"streak={compute_loss_streak(loop.db, '600519')}")
    res = _run_daily(loop)
    buys = [s for s in res["signals"] if s.get("action") == "buy"]
    check("R5 拒绝买入", not buys, f"signals={[s['action'] for s in res['signals']]}")
    check("R5 审计落库", len(_rejections(loop.db, "600519", "R5")) >= 1)

    # ── G. 持仓监控：止盈/止损/移动止损自动平仓 + daemon 接线 ──
    print("\n[G] 持仓监控自动平仓（止盈/止损/移动止损 + NewsSignalWorker daemon）")
    loop = _mk("e2e_g.db")
    conn = loop.db.conn(); y = time.time() - 90000
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        ("e2e_pos", "o_pos", "600519", "buy", 100, 100.0, y))
    conn.commit(); conn.close()
    # 止损：现价 89（-11% ≤ -5%）
    loop.market = FakeMarket({"600519": 89.0})
    state = {}
    m = monitor_positions(loop, state)
    check("止损自动平仓", any(c["reason"] == "stop_loss" for c in m["closed"]),
          f"closed={[(c['symbol'], c.get('reason')) for c in m['closed']]}")
    check("平仓后无持仓", len(loop.ledger.open_positions()) == 0)

    # 止盈：重新开仓，现价 +25%
    loop = _mk("e2e_g2.db")
    conn = loop.db.conn(); y = time.time() - 90000
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        ("e2e_tp", "o_tp", "600519", "buy", 100, 100.0, y))
    conn.commit(); conn.close()
    loop.market = FakeMarket({"600519": 125.0})
    m = monitor_positions(loop, {})
    check("止盈自动平仓", any(c["reason"] == "take_profit" for c in m["closed"]))

    # 移动止损：tick1 高 115，tick2 跌到 108（回撤 6.1% ≥ 5%）
    loop = _mk("e2e_g3.db")
    conn = loop.db.conn(); y = time.time() - 90000
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        ("e2e_ts", "o_ts", "600519", "buy", 100, 100.0, y))
    conn.commit(); conn.close()
    loop.market = FakeMarket({"600519": 115.0})
    state = {}
    monitor_positions(loop, state)  # tick1: high=115
    loop.market = FakeMarket({"600519": 108.0})
    m = monitor_positions(loop, state)  # tick2: 回撤 6.1%
    check("移动止损自动平仓", any(c["reason"] == "trailing_stop" for c in m["closed"]))

    # NewsSignalWorker daemon 接线（盘中自动平仓入口）
    from laap.paper_trading.news_pipeline import NewsSignalWorker, NewsSignalPipeline
    loop = _mk("e2e_g4.db")
    conn = loop.db.conn(); y = time.time() - 90000
    conn.execute(
        "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        ("e2e_w", "o_w", "600519", "buy", 100, 100.0, y))
    conn.commit(); conn.close()
    loop.market = FakeMarket({"600519": 89.0})
    worker = NewsSignalWorker(NewsSignalPipeline(loop=loop, db=loop.db),
                              symbols=["600519"], enabled=True)
    m = worker._monitor_open_positions()
    check("NewsSignalWorker 盘中监控接线", m is not None
          and any(c["reason"] == "stop_loss" for c in m["closed"])
          and worker.last_monitor is m,
          f"last_monitor closed={[c['symbol'] for c in (worker.last_monitor or {}).get('closed', [])]}")

    # ── H. fail-closed：风控异常 → 拒绝下单 ──
    print("\n[H] fail-closed：风控检查异常 → 拒绝（绝不静默放行）")
    loop = _mk("e2e_h.db")
    orig = RiskGate.check_signal
    def _boom(*a, **kw):
        raise RuntimeError("risk gate exploded")
    RiskGate.check_signal = _boom
    try:
        res = loop.decide_and_trade("600519", DecisionAction.BUY, 100,
                                    trigger_price=100.0, rationale="fc",
                                    allow_fallback=True)
        check("异常 → blocked", res.get("status") == "blocked",
              f"status={res.get('status')} reason={res.get('reason','')[:50]}")
        check("异常未放行下单", not loop.ledger.open_positions())
    finally:
        RiskGate.check_signal = orig

    # ── I. decide_and_trade 拒绝落审计（trading_self/quant_bridge 直达路径）──
    print("\n[I] decide_and_trade 拒绝 → risk_rejections 审计")
    loop = _mk("e2e_i.db")
    _insert_closed_loss_today(loop.db, "600519", -30000)  # 触发 R4
    res = loop.decide_and_trade("600519", DecisionAction.BUY, 100,
                                trigger_price=100.0, rationale="audit",
                                allow_fallback=True)
    check("decide_and_trade 拒绝", res.get("status") == "blocked" and "R4" in res.get("reason", ""),
          f"reason={res.get('reason','')[:40]}")
    check("审计落 risk_rejections", len(_rejections(loop.db, "600519", "R4")) >= 1)

    # ── 汇总 ──
    print("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"核验汇总: {passed} PASS / {failed} FAIL")
    if failed:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  FAIL: {name} {detail}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
