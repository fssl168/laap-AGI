# -*- coding: utf-8 -*-
"""大盘（上证指数）K 线图：近三年 + 上周。

数据源：腾讯日K接口（拉 800 根 ≈ 3.3 年）→ 落盘 kline.db → matplotlib 绘制。
输出：data/watchlist_kline/kline_3y.png 与 kline_lastweek.png（红涨绿跌，A股习惯）。
"""
import datetime
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

KLINE_API = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
UA = {"User-Agent": "Mozilla/5.0"}
CODE = "sh000001"  # 上证指数
OUT_DIR = str(Path(__file__).resolve().parent.parent.parent / "data" / "watchlist_kline")


def fetch_index_kline(code: str = CODE, count: int = 800) -> list:
    """拉取上证指数最近 count 根日 K：[[date, open, close, high, low, vol], ...]。"""
    url = f"{KLINE_API}?param={code},day,,,{count},qfq"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    entry = d.get("data", {}).get(code, {})
    rows = entry.get("day") or entry.get("qfqday") or []
    return rows


def persist(rows: list) -> int:
    from watchlist_kline_store import upsert_kline

    flat = [(CODE, r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in rows if len(r) >= 6]
    return upsert_kline(flat)


def draw_candles(rows, title, out_path, max_rows=None, show_ma=None):
    """绘制蜡烛图（红涨绿跌）+ 成交量；show_ma: 叠加 N 日均线。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False

    data = rows[-max_rows:] if max_rows else rows
    dates = [r[0] for r in data]
    xs = list(range(len(data)))
    opens = [float(r[1]) for r in data]
    closes = [float(r[2]) for r in data]
    highs = [float(r[3]) for r in data]
    lows = [float(r[4]) for r in data]
    vols = [float(r[5]) for r in data]

    fig, (ax, axv) = plt.subplots(2, 1, figsize=(14, 8), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1]})
    for i, x in enumerate(xs):
        color = "#e64545" if closes[i] >= opens[i] else "#1f9e4d"  # 红涨绿跌
        ax.vlines(x, lows[i], highs[i], color=color, linewidth=0.8)
        body_bottom = min(opens[i], closes[i])
        body_h = max(abs(closes[i] - opens[i]), 0.01)
        ax.add_patch(Rectangle((x - 0.32, body_bottom), 0.64, body_h,
                               facecolor=color, edgecolor=color))
        axv.bar(x, vols[i], color=color, width=0.64)

    if show_ma:
        for w in (show_ma if isinstance(show_ma, (list, tuple)) else [show_ma]):
            ma = []
            for i in range(len(closes)):
                if i + 1 >= w:
                    ma.append(sum(closes[i + 1 - w:i + 1]) / w)
                else:
                    ma.append(None)
            ax.plot(xs, ma, linewidth=1.0, label=f"MA{w}")
        ax.legend(loc="upper left", fontsize=9)

    # X 轴：显示部分日期
    step = max(len(data) // 10, 1)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels([dates[i] for i in range(0, len(dates), step)], fontsize=8, rotation=0)

    ax.set_title(title, fontsize=14)
    ax.grid(alpha=0.3)
    axv.set_ylabel("成交量")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def last_week_range(latest_date: str):
    """最新日期所在周的上一自然周（周一到周五）。"""
    latest = datetime.date.fromisoformat(latest_date)
    this_monday = latest - datetime.timedelta(days=latest.weekday())
    last_monday = this_monday - datetime.timedelta(days=7)
    last_friday = last_monday + datetime.timedelta(days=4)
    return last_monday.isoformat(), last_friday.isoformat()


def main():
    import os
    from pathlib import Path

    os.makedirs(OUT_DIR, exist_ok=True)

    print("拉取上证指数近三年日K...")
    rows = fetch_index_kline()
    if not rows:
        print("拉取失败")
        sys.exit(1)
    print(f"获得 {len(rows)} 根 ({rows[0][0]} ~ {rows[-1][0]})")

    n = persist(rows)
    print(f"已落盘 kline.db: {n} 行")

    # 近三年图
    p1 = draw_candles(rows, "上证指数 近三年日K（2023-04 ~ 2026-08）",
                      str(Path(OUT_DIR) / "kline_3y.png"), show_ma=[30, 120])
    print("近三年图:", p1)

    # 上周图
    lo, hi = last_week_range(rows[-1][0])
    week = [r for r in rows if lo <= r[0] <= hi]
    if not week:
        # 兜底：最后 5 个交易日
        week = rows[-5:]
        lo, hi = week[0][0], week[-1][0]
    p2 = draw_candles(week, f"上证指数 上周日K（{lo} ~ {hi}）",
                      str(Path(OUT_DIR) / "kline_lastweek.png"))
    print("上周图:", p2)


if __name__ == "__main__":
    main()
