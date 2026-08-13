# -*- coding: utf-8 -*-
"""开盘前（9:15 集合竞价后）实时短线评分 —— 输出候选股列表（供 cron 投递 QQ）。

- 实时行情：腾讯 qt.gtimg.cn 批量单请求拉 42 只（最新价/涨跌幅/量）
- 历史：kline.db（近 20 日 K，含均线/动量基线）
- 评分：动量 + 量能 + 均线排列 + 距高点（复用 _short_term_pick 逻辑，实时价覆盖最新收盘）
- 数据源：单请求批量，无并发/冷却问题
"""
import json
import re
import sys
import urllib.request

sys.path.insert(0, r"D:\laap-AGI")
sys.path.insert(0, r"D:\leanpython\daily_stock_analysis")

CODES = (
    "600326 002790 601238 000975 600960 601899 002131 002600 002584 600114 "
    "600718 002453 603992 603989 603067 002093 603618 600162 600589 002474 "
    "000938 002599 600789 002448 002261 002044 002036 002629 002613 002195 "
    "002125 600172 603121 603701 603278 603949 601360 002298 600249 600802 "
    "002322 002347"
)


def prefixed(c):
    return ("sh" + c) if c.startswith(("6", "9")) else ("sz" + c)


def fetch_realtime():
    """腾讯实时行情：批量单请求 → {code: {name, price, open, prev_close, pct, volume}}。"""
    q = ",".join(prefixed(c) for c in CODES.split())
    req = urllib.request.Request(
        f"http://qt.gtimg.cn/q={q}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "http://finance.qq.com/"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="ignore")
    out = {}
    for m in re.finditer(r'v_\w+="([^"]+)"', raw):
        f = m.group(1).split("~")
        if len(f) < 38:
            continue
        out[f[2]] = {
            "name": f[1], "price": float(f[3]), "open": float(f[5]),
            "prev_close": float(f[4]), "pct": float(f[32]),
            "volume": float(f[6]),
        }
    return out


def score_with_realtime(realtime):
    """结合 db 历史 + 实时价评分（与 _short_term_pick 同逻辑，最新收盘用实时价）。"""
    from watchlist_kline_store import get_kline

    results = []
    for code in CODES.split():
        rt = realtime.get(code)
        if not rt:
            continue
        k = get_kline(prefixed(code), days=20)
        if len(k) < 12:
            continue
        closes = [r[2] for r in k]
        vols = [r[5] for r in k]
        price = rt["price"]

        # 动量（实时价 vs 5/3 日前收盘）
        ret5 = (price - closes[-6]) / closes[-6] * 100 if len(closes) > 6 else 0
        ret3 = (price - closes[-4]) / closes[-4] * 100 if len(closes) > 4 else 0
        # 量比：今日成交量 / 前5日均量
        avg5 = sum(vols[-5:]) / 5 + 1e-9
        vol_ratio = rt["volume"] / avg5
        # 均线（用实时价近似最新）
        ma5 = (sum(closes[-4:]) + price) / 5
        ma10 = (sum(closes[-9:]) + price) / 10
        ma20 = (sum(closes) + price) / 21
        hi20 = max(r[3] for r in k)
        dist_hi = (price - hi20) / hi20 * 100

        score = 0.0
        score += max(ret5, 0) * 1.2
        score += max(ret3, 0) * 1.5
        score += min(vol_ratio, 3.0) * 2.0
        if ma5 > ma10 > ma20:
            score += 5.0
        if dist_hi > -3:
            score += 3.0
        results.append({
            "code": code, "name": rt["name"], "price": price,
            "pct": rt["pct"], "ret5": round(ret5, 2), "vol_ratio": round(vol_ratio, 2),
            "ma_bull": ma5 > ma10 > ma20, "dist_hi": round(dist_hi, 1),
            "score": round(score, 1),
        })

    results.sort(key=lambda x: -x["score"])
    return results


def main():
    try:
        realtime = fetch_realtime()
        if len(realtime) < 40:
            print(f"实时行情获取异常（{len(realtime)}/42），请稍后重试")
            sys.exit(1)
        scores = score_with_realtime(realtime)
        if not scores:
            print("评分失败：历史数据不足")
            sys.exit(1)

        lines = ["📈 【开盘前短线评分】集合竞价后实时数据",
                 f"（{len(scores)} 只自选股，红涨绿跌，评分=动量+量能+均线）", ""]
        for i, s in enumerate(scores[:5], 1):
            cost = s["price"] * 100
            budget = "✅预算内" if cost <= 1000 else "❌超预算"
            lines.append(
                f"{i}. {s['name']}~{s['code']}~  现价{s['price']:.2f} ({s['pct']:+.2f}%)"
                f"\n   评分{s['score']} | 5日动量{s['ret5']:+.1f}% | 量比{s['vol_ratio']:.1f}"
                f" | 多头{'✓' if s['ma_bull'] else '✗'}"
                f"\n   1手≈{cost:.0f}元 {budget}")
        lines.append("")
        lines.append("⚠️ 仅供参考，非投资建议；短线高风险，注意止损")
        print("\n".join(lines))
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 开盘评分任务失败：{exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
