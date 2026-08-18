# -*- coding: utf-8 -*-
"""补齐 watchlist_kline_store 缺失标的的日K（腾讯源 akshare stock_zh_a_hist_tx）。

2026-08-18：get_kline 修复后暴露 12 个 watchlist 标的数据缺失
（300750/002594/000001/600511/600133/000523/600038/000410/000957/603663/603728/600999），
db 源不可用时 load_ohlcv 只能走 tushare，tushare 失败即 0 行。

用法: python scripts/_backfill_kline_missing.py
"""
from __future__ import annotations
import sys
import time
import random
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MISSING = ["300750", "002594", "000001", "600511", "600133", "000523",
           "600038", "000410", "000957", "603663", "603728", "600999"]


def _prefix(code: str) -> str:
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def main() -> None:
    import akshare as ak
    from watchlist_kline_store import upsert_kline

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=800)).strftime("%Y%m%d")
    total = 0
    for code in MISSING:
        try:
            df = ak.stock_zh_a_hist_tx(
                symbol=code, start_date=start, end_date=end, adjust="qfq")
            if df is None or len(df) < 10:
                print(f"[skip] {code}: 腾讯源无数据/不足10行")
                continue
            rows = []
            for _, r in df.iterrows():
                # 腾讯源英文列 (date/open/close/high/low/volume)；akshare 中文列兼容
                date_v = r.get("date") or r.get("日期")
                o, c, h, l, v = (r.get("open") or r.get("开盘"),
                                 r.get("close") or r.get("收盘"),
                                 r.get("high") or r.get("最高"),
                                 r.get("low") or r.get("最低"),
                                 r.get("volume") or r.get("成交量"))
                rows.append((_prefix(code), str(date_v),
                             float(o), float(c), float(h), float(l), float(v)))
            n = upsert_kline(rows)
            total += n
            print(f"[ok] {code}: {len(rows)} 行 -> 落盘 {n}")
        except Exception as e:
            print(f"[fail] {code}: {type(e).__name__}: {str(e)[:100]}")
        time.sleep(random.uniform(1.0, 2.0))  # 防限流
    print(f"\n完成，共落盘 {total} 行")


if __name__ == "__main__":
    main()
