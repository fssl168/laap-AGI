# -*- coding: utf-8 -*-
"""个股K线图绘制工具。

用法:
  python _stock_kline_chart.py 002448          # 默认30日
  python _stock_kline_chart.py 002448 60       # 60日
  python _stock_kline_chart.py 002448 30 ma5  # 带均线
"""
import json
import sys
import urllib.request
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

KLINE_API = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"}
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "watchlist_kline"


def fetch_kline(symbol: str, count: int = 30) -> list:
    """拉取个股日K线（前复权）。"""
    # 判断沪/深
    if symbol.startswith("6"):
        code = f"sh{symbol}"
    else:
        code = f"sz{symbol}"
    
    url = f"{KLINE_API}?param={code},day,,,{count},qfq"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    
    entry = d.get("data", {}).get(code, {})
    rows = entry.get("qfqday") or entry.get("day") or []
    return rows


def draw_candlestick(rows: list, symbol: str, out_path: str, show_ma: list = None):
    """绘制蜡烛图（红涨绿跌）。"""
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False
    
    dates = [r[0] for r in rows]
    xs = list(range(len(rows)))
    opens = [float(r[1]) for r in rows]
    closes = [float(r[2]) for r in rows]
    highs = [float(r[3]) for r in rows]
    lows = [float(r[4]) for r in rows]
    vols = [float(r[5]) for r in rows]
    
    fig, (ax, axv) = plt.subplots(2, 1, figsize=(14, 8), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1]})
    
    for i, x in enumerate(xs):
        color = "#e64545" if closes[i] >= opens[i] else "#1f9e4d"
        ax.vlines(x, lows[i], highs[i], color=color, linewidth=0.8)
        body_bottom = min(opens[i], closes[i])
        body_h = max(abs(closes[i] - opens[i]), 0.01)
        ax.add_patch(Rectangle((x - 0.32, body_bottom), 0.64, body_h,
                               facecolor=color, edgecolor=color))
        axv.bar(x, vols[i], color=color, width=0.64)
    
    # 均线
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
    
    # X轴日期
    step = max(len(dates) // 10, 1)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels([dates[i] for i in range(0, len(dates), step)], 
                       fontsize=8, rotation=0)
    
    # 标题和网格
    ax.set_title(f"{symbol} 日K线（最近{len(rows)}日）", fontsize=14)
    ax.grid(alpha=0.3)
    axv.set_ylabel("成交量")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main():
    if len(sys.argv) < 2:
        print("用法: python _stock_kline_chart.py <股票代码> [天数] [均线...]")
        print("示例: python _stock_kline_chart.py 002448 30 ma5 ma10")
        sys.exit(1)
    
    symbol = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    ma_params = sys.argv[3:] if len(sys.argv) > 3 else None
    
    # 解析均线参数
    show_ma = []
    if ma_params:
        for p in ma_params:
            if p.lower().startswith("ma"):
                try:
                    show_ma.append(int(p[2:]))
                except:
                    pass
    
    print(f"拉取 {symbol} 近{count}日K线...")
    rows = fetch_kline(symbol, count)
    
    if not rows:
        print(f"⚠️ {symbol} 无数据")
        sys.exit(1)
    
    print(f"✅ 获得 {len(rows)} 根K线 ({rows[0][0]} ~ {rows[-1][0]})")
    
    # 输出路径
    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"kline_{symbol}.png"
    
    # 绘制
    draw_candlestick(rows, symbol, str(out_path), show_ma or None)
    print(f"📊 图表已保存: {out_path}")
    
    # 统计信息
    latest = rows[-1]
    prev = rows[-2] if len(rows) > 1 else latest
    change = (float(latest[2]) - float(prev[2])) / float(prev[2]) * 100
    print(f"\n最新收盘价: {latest[2]} ({change:+.2f}%)")


if __name__ == "__main__":
    main()
