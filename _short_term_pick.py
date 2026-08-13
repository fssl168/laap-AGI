# -*- coding: utf-8 -*-
"""短线激进型选股：动量 + 量能 + 均线排列 综合评分（基于 kline.db 历史数据）。"""
import sys

sys.path.insert(0, r"D:\laap-AGI")

from watchlist_kline_store import get_kline, get_stock_names  # noqa: E402

CODES = (
    "600326 002790 601238 000975 600960 601899 002131 002600 002584 600114 "
    "600718 002453 603992 603989 603067 002093 603618 600162 600589 002474 "
    "000938 002599 600789 002448 002261 002044 002036 002629 002613 002195 "
    "002125 600172 603121 603701 603278 603949 601360 002298 600249 600802 "
    "002322 002347"
)


def prefixed(c):
    return ("sh" + c) if c.startswith(("6", "9")) else ("sz" + c)


def analyze():
    names = get_stock_names([prefixed(c) for c in CODES.split()])
    scores = []
    for code in CODES.split():
        k = get_kline(prefixed(code), days=20)
        if len(k) < 12:
            continue
        dates = [r[0] for r in k]
        closes = [r[2] for r in k]
        vols = [r[5] for r in k]
        latest_date = dates[-1]

        # 动量：近 5 日涨幅、近 3 日涨幅
        ret5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) > 6 else 0
        ret3 = (closes[-1] - closes[-4]) / closes[-4] * 100 if len(closes) > 4 else 0
        # 量能：近5日均量 / 前5日均量
        vol_ratio = (sum(vols[-5:]) / 5) / (sum(vols[-10:-5]) / 5 + 1e-9)
        # 均线：MA5 / MA10 / MA20
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes) / len(closes)
        # 距 20 日高点
        hi20 = max(r[3] for r in k)
        dist_hi = (closes[-1] - hi20) / hi20 * 100

        # 短线激进评分（动量为主，量能放大加分，均线多头加分）
        score = 0.0
        score += max(ret5, 0) * 1.2          # 5日动量
        score += max(ret3, 0) * 1.5          # 3日动量（更短线）
        score += min(vol_ratio, 3.0) * 2.0   # 量能放大
        if ma5 > ma10 > ma20:
            score += 5.0                     # 多头排列
        if dist_hi > -3:
            score += 3.0                     # 接近新高（强势）
        scores.append({
            "code": code, "name": names.get(prefixed(code), ""),
            "close": closes[-1], "ret5": round(ret5, 2), "ret3": round(ret3, 2),
            "vol_ratio": round(vol_ratio, 2), "ma_bull": ma5 > ma10 > ma20,
            "dist_hi": round(dist_hi, 1), "date": latest_date, "score": round(score, 1),
        })

    scores.sort(key=lambda x: -x["score"])
    return scores


def main():
    scores = analyze()
    print(f"=== 短线激进候选（数据截至 {scores[0]['date']} 收盘，42只自选股）===")
    print(f"{'排名':<4}{'名称':<8}{'代码':<8}{'收盘':<8}{'5日涨跌':<8}{'量比':<6}{'多头':<5}{'距高点':<7}{'评分'}")
    for i, s in enumerate(scores[:8], 1):
        print(f"{i:<4}{s['name']:<8}{s['code']:<8}{s['close']:<8.2f}{s['ret5']:<8.2f}"
              f"{s['vol_ratio']:<6.2f}{'✓' if s['ma_bull'] else '✗':<5}{s['dist_hi']:<7.1f}{s['score']}")


if __name__ == "__main__":
    main()
