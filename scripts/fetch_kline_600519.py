# -*- coding: utf-8 -*-
"""拉取 600519 贵州茅台真实日K → 写入 watchlist_kline_store.db

数据源: 腾讯行情 fqkline 接口 (前复权日K)
用法:
    python scripts/fetch_kline_600519.py            # 拉最近 320 天
    python scripts/fetch_kline_600519.py --days 500 # 拉最近 500 天
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CODE = "sh600519"
NAME = "贵州茅台"


def fetch_tencent_kline(code: str, days: int) -> list:
    """腾讯 fqkline 接口：返回 [(date, open, close, high, low, volume), ...] 升序。"""
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={code},day,,,{days},qfq")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    node = data["data"][code]
    # 优先 qfqday（前复权），否则 day
    rows = node.get("qfqday") or node.get("day") or []
    out = []
    for r in rows:
        # [date, open, close, high, low, volume] (部分接口 volume 在 [5])
        date, o, c, h, l = r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])
        vol = float(r[5]) if len(r) > 5 else 0.0
        out.append((date, o, c, h, l, vol))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=320)
    args = ap.parse_args()

    rows = fetch_tencent_kline(CODE, args.days)
    if not rows:
        print(f"[ERR] 未拉到 {CODE} 数据")
        return 1
    print(f"[OK] 拉取 {CODE} {NAME}: {len(rows)} 天 ({rows[0][0]} ~ {rows[-1][0]})")

    from watchlist_kline_store import upsert_kline, upsert_stock_names
    db_rows = [(CODE, d, o, c, h, l, v) for d, o, c, h, l, v in rows]
    n = upsert_kline(db_rows)
    upsert_stock_names({CODE: NAME})
    print(f"[OK] 写入 {n} 行到 watchlist_kline_store.db")

    # 验证
    from watchlist_kline_store import get_kline, db_stats
    k = get_kline(CODE, days=5)
    print(f"[OK] 验证读取最近5天:")
    for r in k:
        print(f"    {r[0]} open={r[1]} close={r[2]} high={r[3]} low={r[4]} vol={r[5]}")
    print(f"[OK] db_stats: {db_stats()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
