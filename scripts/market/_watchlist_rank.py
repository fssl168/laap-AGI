# -*- coding: utf-8 -*-
"""解析 LAAP 语义记忆中昨天(2026-08-12)自选股K线, 按涨跌幅排名, 补充股票名称."""
import json
import re
import urllib.request
import urllib.parse

LAAP = "http://localhost:11546"


def recall(query, limit=3):
    body = json.dumps({"query": query, "limit": limit}).encode("utf-8")
    req = urllib.request.Request(
        LAAP + "/v1/recall_memory", data=body,
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def parse_stocks(text):
    """解析: 600326 收盘7.82元(涨跌幅跌0.13%) 开盘7.75最高7.95最低7.71"""
    stocks = []
    # 按 ';' 或 ' 数据源' 切分语句
    for part in re.split(r";\s*", text):
        part = re.sub(r"^【[^】]*】\s*[\d-]*\s*自选股日K收盘\(\d+只\):\s*", "", part)
        m = re.match(
            r"(\d{6})\s+收盘([\d.]+)元\(涨跌幅(涨|跌)([\d.]+)%\)"
            r"(?:\s+开盘([\d.]+)最高([\d.]+)最低([\d.]+))?",
            part)
        if m:
            code, close, sign, pct, o, h, l = m.groups()
            pct = float(pct) * (1 if sign == "涨" else -1)
            stocks.append({
                "code": code, "close": float(close), "pct": pct,
                "open": float(o) if o else None,
                "high": float(h) if h else None,
                "low": float(l) if l else None,
            })
    return stocks


def fetch_names(codes):
    """腾讯行情接口批量取名称(单次请求)."""
    symbols = ",".join(
        ("sh" if c.startswith("6") else "sz") + c for c in codes)
    url = "https://qt.gtimg.cn/q=" + symbols
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk")
        names = {}
        for line in raw.strip().split(";"):
            m = re.search(r'="[^~]+~([^~]+)~(\d{6})~', line)
            if m:
                names[m.group(2)] = m.group(1)
        return names
    except Exception as e:
        print("[warn] name fetch failed:", e)
        return {}


def main():
    # 1) 走 LAAP 召回昨天(8-12)自选股K线
    results = recall("自选股K线记忆 2026-08-12 日K收盘 涨跌幅", limit=3)
    mem = None
    for m in results.get("memories", []):
        if "2026-08-12" in m.get("text", "") and "自选股K线" in m.get("text", ""):
            mem = m
            break
    if not mem:
        print("LAAP 未召回 2026-08-12 自选股K线记忆")
        return
    print(f"[LAAP recall] score={mem.get('score'):.3f} ts={mem.get('timestamp')}")

    stocks = parse_stocks(mem["text"])
    print(f"[parse] {len(stocks)} 只股票")
    if len(stocks) != 42:
        print("[warn] 数量 != 42, 检查解析")

    stocks.sort(key=lambda s: s["pct"], reverse=True)
    names = fetch_names([s["code"] for s in stocks])

    print(f"\n=== 自选股涨跌幅排名 2026-08-12 (LAAP记忆) ===")
    print(f"{'#':>2} {'股票':<12} {'代码':<8} {'收盘':>8} {'涨跌幅':>8}")
    for i, s in enumerate(stocks, 1):
        nm = names.get(s["code"], "")
        label = f"{nm}~{s['code']}~" if nm else s["code"]
        arrow = "▲" if s["pct"] >= 0 else "▼"
        print(f"{i:>2} {label:<16} {s['close']:>8.2f} {arrow}{abs(s['pct']):>7.2f}%")

    ups = [s for s in stocks if s["pct"] > 0]
    downs = [s for s in stocks if s["pct"] < 0]
    flat = [s for s in stocks if s["pct"] == 0]
    avg = sum(s["pct"] for s in stocks) / len(stocks)
    print(f"\n上涨 {len(ups)} / 下跌 {len(downs)} / 平盘 {len(flat)} | 平均涨跌幅 {avg:+.2f}%")


if __name__ == "__main__":
    main()
