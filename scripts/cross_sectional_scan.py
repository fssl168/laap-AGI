# -*- coding: utf-8 -*-
"""Track ①：42 只自选股横截面验证（kline.db，64 天窗口，真实 OHLCV）。

在同一批标的上比较三个策略族/变体的横截面表现：
  buy_hold    买入持有
  long_only   多因子长期做多（默认 STRATEGY_PARAMS）
  long_short  多因子多空（同一参数）
  regime60    多因子 + 60 日均线趋势过滤

指标：正收益占比 / 跑赢买入持有占比 / 平均收益 / 平均超额。
诚实结论：横截面证据（42 个点）检验因子族是否有普适 edge。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

KLINE_DB = Path(__file__).resolve().parents[1] / "data" / "watchlist_kline" / "kline.db"


def load_all() -> Dict[str, Dict]:
    """kline.db → {code: {closes, ohlcv}}（升序，仅取 >= min_days 的标的）。"""
    conn = sqlite3.connect(str(KLINE_DB))
    rows = conn.execute(
        "SELECT code, date, open, close, high, low, volume FROM daily_kline "
        "WHERE code != 'sh000001' ORDER BY code, date").fetchall()
    conn.close()
    out: Dict[str, Dict] = {}
    for code, _d, o, c, h, l, v in rows:
        out.setdefault(code, {"closes": [], "ohlcv": []})
        out[code]["closes"].append(float(c))
        out[code]["ohlcv"].append((float(o), float(c), float(h), float(l), float(v)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="42 只自选股横截面策略验证")
    ap.add_argument("--min-days", type=int, default=40)
    ap.add_argument("--out", default="real_data/cross_sectional_scan.json")
    args = ap.parse_args()

    from laap.paper_trading.backtest_runner import BacktestRunner
    from laap.paper_trading.strategy import STRATEGY_PARAMS

    data = load_all()
    runner = BacktestRunner()
    params = dict(STRATEGY_PARAMS)

    rows = []
    for code, info in sorted(data.items()):
        closes, ohlcv = info["closes"], info["ohlcv"]
        if len(closes) < args.min_days:
            continue
        bh = (closes[-1] - closes[0]) / closes[0] if len(closes) > 1 else 0.0
        m_lo = runner.run_backtest(closes, params, ohlcv=ohlcv)
        m_ls = runner.run_backtest(closes, params, ohlcv=ohlcv, long_short=True)
        m_rg = runner.run_backtest(closes, params, ohlcv=ohlcv, regime_ma=60)
        rows.append({
            "code": code, "days": len(closes),
            "buy_hold": round(bh, 4),
            "long_only": round(m_lo["cumulative_return"], 4),
            "long_short": round(m_ls["cumulative_return"], 4),
            "regime60": round(m_rg["cumulative_return"], 4),
            "lo_excess": round(m_lo["cumulative_return"] - bh, 4),
            "ls_excess": round(m_ls["cumulative_return"] - bh, 4),
        })

    def stat(key, fn):
        vals = [r[key] for r in rows]
        return {"positive": sum(1 for v in vals if v > 0), "n": len(vals),
                "mean": round(sum(vals) / len(vals), 4) if vals else 0.0,
                "median": round(sorted(vals)[len(vals)//2], 4) if vals else 0.0}

    summary = {
        "n_symbols": len(rows),
        "buy_hold": stat("buy_hold", None),
        "long_only": stat("long_only", None),
        "long_short": stat("long_short", None),
        "regime60": stat("regime60", None),
        "lo_beat_bh": sum(1 for r in rows if r["lo_excess"] > 0),
        "ls_beat_bh": sum(1 for r in rows if r["ls_excess"] > 0),
    }

    print("=" * 88)
    print(f"横截面扫描：{summary['n_symbols']} 只自选股（kline.db，真实 OHLCV，64 天窗）")
    print("=" * 88)
    print(f"{'策略':<12} {'正收益':>8} {'跑赢买入持有':>12} {'平均收益':>10} {'中位收益':>10}")
    print(f"{'买入持有':<12} {summary['buy_hold']['positive']:>4}/{summary['n_symbols']} "
          f"{'-':>12} {summary['buy_hold']['mean']:>9.2%} {summary['buy_hold']['median']:>9.2%}")
    for name in ("long_only", "long_short", "regime60"):
        s = summary[name]
        bb = summary["lo_beat_bh"] if name == "long_only" else (
            summary["ls_beat_bh"] if name == "long_short" else "-")
        label = {"long_only": "长期做多", "long_short": "多空",
                 "regime60": "regime60"}[name]
        beat = f"{bb:>4}/{summary['n_symbols']}" if isinstance(bb, int) else f"{'-':>12}"
        print(f"{label:<12} {s['positive']:>4}/{summary['n_symbols']} "
              f"{beat:>12} {s['mean']:>9.2%} {s['median']:>9.2%}")

    out = Path(args.out)
    out.write_text(json.dumps({"summary": summary, "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已保存: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
