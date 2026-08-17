# -*- coding: utf-8 -*-
"""paper 观察期一键查看器：净值序列 + 成交明细 + M5 达标判据。

直接读 data/paper_trading.db（SQLite），不依赖服务进程。
用于论文 M5（真实 paper 成交序列 ≥1 个月）的进度核对——
周一调度器开跑后，随时一条命令即可判断证据是否在积累。

用法:
    python scripts/check_paper_performance.py
    python scripts/check_paper_performance.py --json       # 机器可读输出
    python scripts/check_paper_performance.py --top 15     # 成交明细最多显示 N 笔
    python scripts/check_paper_performance.py --db <path>  # 指定库路径
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # 允许懒加载 laap.* 模块（基准计算用）

# ── M5 验收标准（runbook §5）──
NET_VALUE_DAYS = 20      # 净值快照 ≥20 个交易日
MIN_TRADES = 10          # paper 成交 ≥10 笔
MIN_OBS_LOG = 20         # TradingSelf 观察日志 ≥20 条


def _default_db_path() -> Path:
    """数据库路径：优先 PAPER_TRADING_DB_PATH env，否则项目根 data/laap_trading.db。

    2026-08-17: SQLite 回退库从 paper_trading.db 改为 laap_trading.db（含历史数据）。
    2026-08-18: 非 Windows 平台忽略 .env 里的 Windows 盘符绝对路径（防 D: 垃圾目录）。
    """
    from laap.paper_trading.db import _is_windows_drive_abs
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PAPER_TRADING_DB_PATH="):
                p = Path(line.split("=", 1)[1].strip())
                if os.name != "nt" and _is_windows_drive_abs(str(p)):
                    break  # Windows 盘符路径在非 Windows 无效 → 回退项目根相对路径
                if p.exists():
                    return p
                break  # env 路径不存在 → 回退项目根相对路径
    # 2) 项目根相对默认
    return ROOT / "data" / "laap_trading.db"


def _local_ts(ts: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _sharpe(totals: list) -> float:
    """日收益夏普（年化 sqrt(252)）。序列 <2 返回 0。"""
    rets = []
    for i in range(1, len(totals)):
        prev = totals[i - 1]
        if prev and prev > 0:
            rets.append((totals[i] - prev) / prev)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    if var < 1e-12:
        return 0.0
    return math.sqrt(252) * mean / math.sqrt(var)


def _max_drawdown(totals: list) -> float:
    peak = float("-inf")
    mdd = 0.0
    for v in totals:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd
    return mdd


def main() -> int:
    ap = argparse.ArgumentParser(description="paper 观察期进度查看器")
    ap.add_argument("--db", default="", help="显式指定 SQLite 库路径（默认走 PG laap_trading）")
    ap.add_argument("--top", type=int, default=15, help="成交明细最多显示笔数")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--days", type=int, default=0,
                    help="只看最近 N 个净值快照（0=全部）")
    args = ap.parse_args()

    # 连接：显式 --db → SQLite 直连；否则 PaperDB（PG laap_trading 优先，SQLite 回退）
    if args.db:
        dbp = Path(args.db)
        if not dbp.exists():
            msg = (f"库不存在: {dbp}\n调度器还没写入数据？周一开跑后这里才会积累。"
                   f"\n确认服务已启动且 LAAP_QUANT_DAILY=1。")
            print(msg)
            return 1
        conn = sqlite3.connect(str(dbp))
        conn.row_factory = sqlite3.Row
    else:
        from laap.paper_trading.db import PaperDB
        db = PaperDB()
        conn = db.conn()
    cur = conn.cursor()

    # ── 净值序列 ──
    nv_rows = cur.execute(
        "SELECT ts, cash, equity, total FROM net_values ORDER BY ts ASC"
    ).fetchall()
    if args.days > 0:
        nv_rows = nv_rows[-args.days:]

    # 按日期聚合（同一天多条取最后一条，模拟日快照）
    daily: dict[str, dict] = {}
    for r in nv_rows:
        day = _local_ts(r["ts"])[:10]
        daily[day] = {"ts": r["ts"], "total": r["total"]}
    daily_dates = sorted(daily.keys())
    totals = [daily[d]["total"] for d in daily_dates]

    trades = cur.execute(
        "SELECT symbol, side, quantity, entry_price, exit_price, "
        "pnl, pnl_pct, hold_days, entry_ts, exit_ts "
        "FROM trades ORDER BY entry_ts ASC"
    ).fetchall()
    signals_n = cur.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    decisions_n = cur.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    outcomes_n = cur.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    orders_n = cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()

    # ── 观察日志 ──
    obs_n = 0
    obs_path = ROOT / "real_data" / "trading_self_observation_log.json"
    if obs_path.exists():
        try:
            obs_n = len(json.loads(obs_path.read_text(encoding="utf-8")))
        except Exception:
            obs_n = -1  # 解析失败

    # ── 指标 ──
    if totals:
        start_v, end_v = totals[0], totals[-1]
        total_return = (end_v - start_v) / start_v if start_v else 0.0
        sharpe = _sharpe(totals)
        mdd = _max_drawdown(totals)
    else:
        start_v = end_v = 0.0
        total_return = sharpe = mdd = 0.0

    # ── 换手 / 成本 / 基准（runbook §5 验收指标）──
    # A 股成本模型（与 backtest_runner 的 ashare 口径一致）
    _COMM, _STAMP, _SLIP = 0.00025, 0.0005, 0.001
    buy_notional = sum(t["quantity"] * t["entry_price"] for t in trades
                       if t["side"] == "buy")
    sell_notional = sum(t["quantity"] * (t["exit_price"] or 0) for t in trades
                        if t["exit_price"])
    avg_total = (sum(totals) / len(totals)) if totals else 0.0
    turnover = (buy_notional + sell_notional) / avg_total if avg_total else 0.0
    cost_drag = sum(
        t["quantity"] * t["entry_price"] * (_COMM + _SLIP)
        + t["quantity"] * (t["exit_price"] or 0) * (_COMM + _STAMP + _SLIP)
        for t in trades)
    cost_adj_return = (end_v - start_v - cost_drag) / start_v if start_v else 0.0

    # 成本后超额 vs 买入持有（简化价值加权基准：按各标的首次买入分配权重）
    benchmark_return = None
    if totals and nv_rows:
        span_days = max(1, (nv_rows[-1]["ts"] - nv_rows[0]["ts"]) / 86400)
        wf_days = int(span_days * 5 / 7) + 30
        weights: dict = {}
        for t in trades:
            if t["side"] == "buy" and t["symbol"] not in weights:
                weights[t["symbol"]] = t["quantity"] * t["entry_price"]
        wsum = sum(weights.values())
        if wsum > 0:
            rets = []
            try:
                from laap.paper_trading.kline_source import load_price_series
                for sym, w in weights.items():
                    closes = load_price_series(sym, days=wf_days, fallback=False)
                    if len(closes) >= 2:
                        si = max(0, len(closes) - max(1, int(span_days * 5 / 7)))
                        if closes[si] > 0:
                            rets.append((w / wsum, closes[-1] / closes[si] - 1))
            except Exception:
                pass
            if rets:
                benchmark_return = sum(w * r for w, r in rets)
    excess = (cost_adj_return - benchmark_return) if benchmark_return is not None else None

    criteria = {
        "net_value_days": len(daily_dates),
        "net_value_days_ok": len(daily_dates) >= NET_VALUE_DAYS,
        "trades": len(trades),
        "trades_ok": len(trades) >= MIN_TRADES,
        "obs_log": obs_n,
        "obs_log_ok": obs_n >= MIN_OBS_LOG,
        "signals": signals_n,
        "decisions": decisions_n,
        "outcomes": outcomes_n,
        "orders": orders_n,
    }
    passed = sum(1 for k in ("net_value_days_ok", "trades_ok", "obs_log_ok")
                 if criteria[k])
    if len(daily_dates) == 0:
        stage = "NOT_STARTED"
        stage_reason = "尚无净值快照（调度器未开始或未到交易日）"
    elif passed == 3:
        stage = "MEETS_CRITERIA"
        stage_reason = "三项验收标准全部达成，M5 证据可用"
    else:
        stage = "COLLECTING"
        stage_reason = "证据收集中，尚未达标"

    if args.json:
        out = {
            "stage": stage,
            "stage_reason": stage_reason,
            "net_value": {
                "snapshots": len(nv_rows),
                "trading_days": len(daily_dates),
                "first_day": daily_dates[0] if daily_dates else None,
                "last_day": daily_dates[-1] if daily_dates else None,
                "start_value": start_v,
                "end_value": end_v,
                "total_return": round(total_return, 6),
                "sharpe": round(sharpe, 4),
                "max_drawdown": round(mdd, 4),
                "turnover": round(turnover, 4),
                "cost_adj_return": round(cost_adj_return, 6),
                "benchmark_return": round(benchmark_return, 6) if benchmark_return is not None else None,
                "excess_vs_buyhold": round(excess, 6) if excess is not None else None,
            },
            "criteria": criteria,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # ── 人类可读 ──
    line = "=" * 72
    print(line)
    print("Paper 观察期进度 | 阶段: %s" % stage)
    print(line)
    print(f"净值快照: {len(nv_rows)} 条 | 交易日快照: {len(daily_dates)} 天 "
          f"({daily_dates[0] if daily_dates else '-'} ~ "
          f"{daily_dates[-1] if daily_dates else '-'})")
    if totals:
        print(f"起值 {start_v:,.0f} → 末值 {end_v:,.0f} | "
              f"总收益 {total_return:+.2%} | 夏普 {sharpe:.2f} | "
              f"最大回撤 {mdd:.2%}")
        print(f"换手率 {turnover:.2f} | 成本后收益 {cost_adj_return:+.2%}"
              + (f" | 买入持有基准 {benchmark_return:+.2%} | 成本后超额 {excess:+.2%}"
                 if excess is not None else " | 买入持有基准 不可用(无K线)"))
    else:
        print("（无净值数据）")
    print(f"成交: {len(trades)} 笔 | signals {signals_n} | orders {orders_n} | "
          f"decisions {decisions_n} | outcomes {outcomes_n} | "
          f"观察日志 {obs_n if obs_n >= 0 else '解析失败'} 条")

    print(line)
    print("M5 验收标准:")
    marks = {
        "net_value_days_ok": (f"交易日快照 >= {NET_VALUE_DAYS}", criteria["net_value_days_ok"]),
        "trades_ok": (f"成交笔数 >= {MIN_TRADES}", criteria["trades_ok"]),
        "obs_log_ok": (f"观察日志 >= {MIN_OBS_LOG}", criteria["obs_log_ok"]),
    }
    for key, (label, ok) in marks.items():
        print(f"  [{'✓' if ok else '✗'}] {label}  (当前 "
              f"{criteria[key.replace('_ok','') if key != 'net_value_days_ok' else 'net_value_days']})")
    print(line)

    if trades:
        print(f"最近成交（最多 {args.top} 笔，按时间升序）:")
        print(f"  {'symbol':<8}{'side':<5}{'qty':>6}{'entry':>10}{'exit':>10}"
              f"{'pnl':>10}{'pnl%':>8}{'hold':>5}  {'时间'}")
        shown = trades if len(trades) <= args.top else trades[-args.top:]
        for t in shown:
            print(f"  {t['symbol']:<8}{t['side']:<5}{t['quantity']:>6}"
                  f"{t['entry_price']:>10.2f}{t['exit_price']:>10.2f}"
                  f"{t['pnl']:>10.0f}{t['pnl_pct']*100:>7.1f}%{t['hold_days']:>5}"
                  f"  {_local_ts(t['entry_ts'])}")
    else:
        print("（尚无成交记录）")

    print(line)
    print(f"阶段: {stage} — {stage_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
