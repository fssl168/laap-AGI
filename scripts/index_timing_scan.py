# -*- coding: utf-8 -*-
"""Track ①：指数择时（指数 MA20 状态门）× 42 只自选股横截面验证。

kline.db 同时含指数 sh000001（801 天）与 42 只自选股（64 天），按交易日对齐：
  external_regime[i] = 当日指数 close > 指数 20 日均线（True=允许做多 / False=离场/可做空）
在同一批标的上比较：
  buy_hold            买入持有
  long_only           多因子长期做多（无择时）
  long_only+timing    长期做多 + 指数择时
  long_short+timing   多空 + 指数择时
诚实结论：指数择时是否提升横截面表现（熊市窗口应显著）。
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


def _sma(values: List[float], window: int) -> List[float]:
    out = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(float("nan"))
        else:
            out.append(sum(values[i - window + 1:i + 1]) / window)
    return out


def load_index_regime(ma_window: int = 20) -> Dict[str, bool]:
    """sh000001 每日指数上行标记（close > MA）。"""
    conn = sqlite3.connect(str(KLINE_DB))
    rows = conn.execute(
        "SELECT date, close FROM daily_kline WHERE code='sh000001' "
        "ORDER BY date").fetchall()
    conn.close()
    dates = [r[0] for r in rows]
    closes = [float(r[1]) for r in rows]
    ma = _sma(closes, ma_window)
    regime: Dict[str, bool] = {}
    for i, d in enumerate(dates):
        v = ma[i]
        regime[d] = (closes[i] > v) if v == v else True  # NaN 前默认允许
    return regime


def load_stocks(min_days: int = 40) -> Dict[str, Dict]:
    conn = sqlite3.connect(str(KLINE_DB))
    rows = conn.execute(
        "SELECT code, date, open, close, high, low, volume FROM daily_kline "
        "WHERE code != 'sh000001' ORDER BY code, date").fetchall()
    conn.close()
    out: Dict[str, Dict] = {}
    for code, d, o, c, h, l, v in rows:
        out.setdefault(code, {"dates": [], "closes": [], "ohlcv": []})
        out[code]["dates"].append(d)
        out[code]["closes"].append(float(c))
        out[code]["ohlcv"].append((float(o), float(c), float(h), float(l), float(v)))
    return {k: v for k, v in out.items() if len(v["closes"]) >= min_days}


def main() -> int:
    ap = argparse.ArgumentParser(description="指数择时横截面验证")
    ap.add_argument("--min-days", type=int, default=40)
    ap.add_argument("--ma", type=int, default=20, help="指数 MA 窗口")
    ap.add_argument("--out", default="real_data/index_timing_scan.json")
    args = ap.parse_args()

    from laap.paper_trading.backtest_runner import BacktestRunner
    from laap.paper_trading.strategy import STRATEGY_PARAMS

    index_regime = load_index_regime(args.ma)
    stocks = load_stocks(args.min_days)
    runner = BacktestRunner()
    params = dict(STRATEGY_PARAMS)

    rows = []
    for code, info in sorted(stocks.items()):
        closes, ohlcv, dates = info["closes"], info["ohlcv"], info["dates"]
        ext = [bool(index_regime.get(d, True)) for d in dates]
        bh = (closes[-1] - closes[0]) / closes[0] if len(closes) > 1 else 0.0
        m_lo = runner.run_backtest(closes, params, ohlcv=ohlcv)
        m_lo_t = runner.run_backtest(closes, params, ohlcv=ohlcv,
                                     external_regime=ext)
        m_ls_t = runner.run_backtest(closes, params, ohlcv=ohlcv,
                                     long_short=True, external_regime=ext)
        rows.append({
            "code": code, "days": len(closes),
            "index_up_ratio": round(sum(1 for b in ext if b) / len(ext), 3),
            "buy_hold": round(bh, 4),
            "long_only": round(m_lo["cumulative_return"], 4),
            "long_only_timing": round(m_lo_t["cumulative_return"], 4),
            "long_short_timing": round(m_ls_t["cumulative_return"], 4),
        })

    def stat(key):
        vals = [r[key] for r in rows]
        return {"positive": sum(1 for v in vals if v > 0), "n": len(vals),
                "mean": round(sum(vals) / len(vals), 4) if vals else 0.0,
                "median": round(sorted(vals)[len(vals) // 2], 4) if vals else 0.0}

    summary = {
        "n_symbols": len(rows),
        "ma_window": args.ma,
        "buy_hold": stat("buy_hold"),
        "long_only": stat("long_only"),
        "long_only_timing": stat("long_only_timing"),
        "long_short_timing": stat("long_short_timing"),
        "timing_improves_lo": sum(1 for r in rows
                                  if r["long_only_timing"] > r["long_only"]),
        "timing_improves_ls": sum(1 for r in rows
                                  if r["long_short_timing"] > r["long_only"]),
    }

    print("=" * 92)
    print(f"指数择时横截面（MA{args.ma}，{summary['n_symbols']} 只自选股，64 天窗）")
    print("=" * 92)
    print(f"{'策略':<20} {'正收益':>8} {'平均':>10} {'中位':>10}")
    for name, label in (("buy_hold", "买入持有"),
                        ("long_only", "长期做多（无择时）"),
                        ("long_only_timing", "长期做多 + 指数择时"),
                        ("long_short_timing", "多空 + 指数择时")):
        s = summary[name]
        print(f"{label:<20} {s['positive']:>4}/{s['n']} {s['mean']:>9.2%} {s['median']:>9.2%}")
    print(f"\n择时 vs 无择时（长期做多）：改善 {summary['timing_improves_lo']}/{summary['n_symbols']}")
    print(f"多空择时 vs 无择时长期做多：改善 {summary['timing_improves_ls']}/{summary['n_symbols']}")

    out = Path(args.out)
    out.write_text(json.dumps({"summary": summary, "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已保存: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
