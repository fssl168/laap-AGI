# -*- coding: utf-8 -*-
from pathlib import Path
"""每日自选股 K 线记忆与持久化（收盘后运行）。

1) 拉取 42 只自选股最近 60 根日 K（腾讯行情，免费无需 key）
   —— 并发 ≤2、批间随机冷却（防 IP 拉黑）
2) 完整日 K 落盘 SQLite（data/watchlist_kline/kline.db，趋势分析用）
3) 当日摘要写入 LAAP 语义记忆（日常回顾用）
4) 召回验证，输出摘要（供 cron 交付）

查询用法：
  python _memorize_kline_daily.py --trend 600326 [window]
  python _memorize_kline_daily.py --yesterday
  python _memorize_kline_daily.py --stats
"""
import json
import random
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, r"D:\leanpython\daily_stock_analysis")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

REFLECT = "http://localhost:11546/v1/reflect"
RECALL = "http://localhost:11546/v1/recall_memory"
KLINE_API = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
UA = {"User-Agent": "Mozilla/5.0"}

# 数据源抓取保护：最多 2 条并发；每批之间随机冷却，防 IP 被拉黑
MAX_CONCURRENCY = 2
BATCH_SIZE = 4
COOL_DOWN_RANGE = (1.0, 3.0)  # 批间随机冷却秒数
HISTORY_DAYS = 60  # 每次拉取的历史根数（≥30 满足均线分析）

WATCHLIST = (
    "600326 002790 601238 000975 600960 601899 002131 002600 002584 600114 "
    "600718 002453 603992 603989 603067 002093 603618 600162 600589 002474 "
    "000938 002599 600789 002448 002261 002044 002036 002629 002613 002195 "
    "002125 600172 603121 603701 603278 603949 601360 002298 600249 600802 "
    "002322 002347"
)


def _prefixed(code: str) -> str:
    return ("sh" + code) if code.startswith(("6", "9")) else ("sz" + code)


def fetch_kline(code: str, count: int = HISTORY_DAYS) -> list:
    """拉取单只股票最近 count 根日 K：[[date, open, close, high, low, vol], ...]。"""
    url = f"{KLINE_API}?param={_prefixed(code)},day,,,{count},qfq"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.load(r)
    data = d.get("data", {})
    entry = data.get(_prefixed(code), {})
    rows = entry.get("qfqday") or entry.get("day") or []
    return rows


def fetch_batch_concurrent(codes: list) -> tuple:
    """分批并发拉取：每批 BATCH_SIZE 只、并发 ≤ MAX_CONCURRENCY、批间随机冷却。

    返回 (rows_by_code, failures)。
    """
    rows_by_code = {}
    failures = []
    for i in range(0, len(codes), BATCH_SIZE):
        batch = codes[i:i + BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as ex:
            futs = {ex.submit(fetch_kline, c): c for c in batch}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    rows = fut.result()
                    if rows:
                        rows_by_code[c] = rows
                    else:
                        failures.append(c)
                except Exception:  # noqa: BLE001
                    failures.append(c)
        # 不定时冷却（随机 1~3 秒），避免连续请求触发数据源限流
        if i + BATCH_SIZE < len(codes):
            time.sleep(random.uniform(*COOL_DOWN_RANGE))
    return rows_by_code, failures


def fetch_names(codes: list) -> dict:
    """批量获取 代码→名称（腾讯实时接口，单个请求）。"""
    q = ",".join(_prefixed(c) for c in codes)
    url = f"http://qt.gtimg.cn/q={q}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "http://finance.qq.com/"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read().decode("gbk", errors="ignore")
    import re

    names = {}
    for m in re.finditer(r'v_\w+="([^"]+)"', raw):
        f = m.group(1).split("~")
        if len(f) >= 3:
            names[_prefixed(f[2])] = f[1]
    return names


def persist_kline(rows_by_code: dict) -> int:
    """完整日 K 落盘 SQLite。rows_by_code: {code: [[date,o,c,h,l,v],...]}"""
    from watchlist_kline_store import upsert_kline

    flat = []
    for code, rows in rows_by_code.items():
        for r in rows:
            if len(r) >= 6:
                flat.append((_prefixed(code), r[0], float(r[1]), float(r[2]),
                             float(r[3]), float(r[4]), float(r[5])))
    return upsert_kline(flat)


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def build_summary(rows_by_code, failures, latest_date, codes):
    """当日摘要（LAAP 记忆用）。"""
    parts = []
    for code in codes:
        rows = rows_by_code.get(code)
        if not rows:
            continue
        last = rows[-1]
        date, o, c, h, l = last[0], float(last[1]), float(last[2]), float(last[3]), float(last[4])
        pct = 0.0
        if len(rows) >= 2:
            prev_c = float(rows[-2][2])
            if prev_c:
                pct = (c - prev_c) / prev_c * 100.0
        arrow = "涨" if pct >= 0 else "跌"
        parts.append(f"{code} 收盘{c:.2f}元(涨跌幅{arrow}{abs(pct):.2f}%) 开盘{o:.2f}最高{h:.2f}最低{l:.2f}")
    return (
        f"【自选股K线记忆】{latest_date} 自选股日K收盘({len(parts)}只): "
        + "; ".join(parts)
        + f"; 数据源腾讯日K线, LAAP工具链路自动记录. "
        + (f"获取失败: {' '.join(failures)}." if failures else "")
    )


def cmd_trend(code: str, window: int = 5, days: int = 60):
    from watchlist_kline_store import get_ma, get_kline

    ma = get_ma(code, days=days, window=window)
    if not ma:
        print(f"{code}: 无数据（可能尚未落盘）")
        return
    closes = [r[1] for r in ma]
    latest = ma[-1]
    prev = ma[-2] if len(ma) > 1 else None
    direction = "上行" if prev and latest[2] > prev[2] else ("下行" if prev else "-")
    print(f"{code} 最近{len(ma)}个交易日 {window}日均线: 当前{latest[2]}({direction})")
    for date, close, m in ma[-10:]:
        print(f"  {date}  收盘{close:.2f}  MA{window}={m:.2f}")


def cmd_yesterday():
    from watchlist_kline_store import get_day_overview, get_latest_day

    date = get_latest_day()
    overview = get_day_overview(date, codes=[_prefixed(c) for c in WATCHLIST.split()])
    items = overview.get("items", {})
    if not items:
        print("数据库暂无自选股数据")
        return
    print(f"自选股概况（{overview['date']}，{len(items)}只）：")
    up = [k for k, v in items.items() if v.get("pct") is not None and v["pct"] >= 0]
    down = [k for k, v in items.items() if v.get("pct") is not None and v["pct"] < 0]
    print(f"  上涨 {len(up)} 只 | 下跌 {len(down)} 只")
    ranked = sorted(items.items(), key=lambda kv: (kv[1].get("pct") or 0), reverse=True)
    for code, v in ranked[:5]:
        print(f"   {code} 收盘{v['close']:.2f} ({v['pct']:+.2f}%)")
    if len(ranked) > 5:
        print("   ...")
        for code, v in ranked[-3:]:
            print(f"   {code} 收盘{v['close']:.2f} ({v['pct']:+.2f}%)")


def cmd_stats():
    from watchlist_kline_store import db_stats

    s = db_stats()
    print(f"K线存储层: {s['total_rows']} 行 / {s['codes']} 个标的 / {s['days']} 个交易日")
    print(f"数据库: {s['db_path']}")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--trend" and len(args) >= 2:
        code = args[1]
        window = int(args[2]) if len(args) > 2 else 5
        cmd_trend(_prefixed(code) if not code.startswith(("sh", "sz")) else code, window)
        return
    if args and args[0] == "--yesterday":
        cmd_yesterday()
        return
    if args and args[0] == "--stats":
        cmd_stats()
        return

    # ── 每日主流程：拉取 → 落盘 → LAAP 记忆 ──
    codes = WATCHLIST.split()
    rows_by_code, failures = fetch_batch_concurrent(codes)

    if not rows_by_code:
        print(f"自选股 K 线获取全部失败（{len(failures)} 只），跳过本次任务")
        sys.exit(1)

    # 完整日 K 落盘 SQLite
    try:
        n = persist_kline(rows_by_code)
        print(f"[存储] 已落盘 {n} 条日K记录 -> data/watchlist_kline/kline.db")
        # 顺带保存股票名称映射（单请求，供「名称+代码」展示）
        from watchlist_kline_store import upsert_stock_names

        names = fetch_names(codes)
        if names:
            upsert_stock_names(names)
    except Exception as exc:  # noqa: BLE001
        print(f"[存储] 落盘失败: {exc}")
        sys.exit(1)

    # 当日摘要写入 LAAP 记忆
    latest_date = rows_by_code[codes[0]][-1][0]
    summary = build_summary(rows_by_code, failures, latest_date, codes)
    try:
        post(REFLECT, {"output": summary})
    except Exception as exc:  # noqa: BLE001
        print(f"[记忆] 写入失败: {exc}")
        sys.exit(1)

    # 召回验证（查询词用 K 线独有特征，top3 内命中即成功）
    try:
        d2 = post(RECALL, {"query": "自选股 日K 收盘 涨跌幅", "limit": 3})
    except Exception as exc:  # noqa: BLE001
        print(f"[验证] 召回失败: {exc}")
        sys.exit(1)
    hits = d2.get("memories") or []
    ok = any("自选股K线记忆" in (m.get("text") or "") for m in hits)
    best = hits[0] if hits else {}

    print(summary[:2000])
    print(f"[记忆验证] 召回{'成功' if ok else '失败'} top_score={best.get('score', 0):.3f}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
