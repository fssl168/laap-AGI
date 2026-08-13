# -*- coding: utf-8 -*-
"""候选股近 10 日走势图（短线选股评分前 N 名，2×2 蜡烛子图，红涨绿跌）。"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\laap-AGI")

from _short_term_pick import analyze as pick_analyze  # noqa: E402
from watchlist_kline_store import get_kline  # noqa: E402

OUT = Path(r"D:\laap-AGI\data\watchlist_kline\candidates_10d.png")
TOPN = 4


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False

    picks = pick_analyze()[:TOPN]
    if not picks:
        print("无候选数据")
        sys.exit(1)

    cols = 2
    rows = (len(picks) + 1) // 2
    fig, axes = plt.subplots(rows, cols, figsize=(14, 4.5 * rows), squeeze=False)
    fig.suptitle(f"短线候选股 近10日走势（数据截至 {picks[0]['date']} 收盘，红涨绿跌）",
                 fontsize=15)

    for idx, p in enumerate(picks):
        ax = axes[idx // cols][idx % cols]
        k = get_kline(("sh" if p["code"].startswith(("6", "9")) else "sz") + p["code"], days=10)
        if not k:
            ax.text(0.5, 0.5, "无数据", ha="center", transform=ax.transAxes)
            continue
        xs = list(range(len(k)))
        opens = [r[1] for r in k]
        closes = [r[2] for r in k]
        highs = [r[3] for r in k]
        lows = [r[4] for r in k]
        vols = [r[5] for r in k]

        for i, x in enumerate(xs):
            color = "#e64545" if closes[i] >= opens[i] else "#1f9e4d"
            ax.vlines(x, lows[i], highs[i], color=color, linewidth=1)
            body_bottom = min(opens[i], closes[i])
            body_h = max(abs(closes[i] - opens[i]), 0.01)
            ax.add_patch(Rectangle((x - 0.32, body_bottom), 0.64, body_h,
                                   facecolor=color, edgecolor=color))

        ret = p.get("ret5", 0)
        ax.set_title(f"{p['name']}~{p['code']}~  收盘{p['close']:.2f}  5日{ret:+.2f}%",
                     fontsize=11)
        ax.set_xticks(range(0, len(k), max(len(k) // 5, 1)))
        ax.set_xticklabels([r[0][5:] for r in k][::max(len(k) // 5, 1)], fontsize=7)
        ax.grid(alpha=0.3)

    # 隐藏多余子图
    for idx in range(len(picks), rows * cols):
        axes[idx // cols][idx % cols].axis("off")

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(OUT, dpi=110)
    plt.close(fig)
    print(f"已生成: {OUT} ({len(picks)} 只)")


if __name__ == "__main__":
    main()
