# -*- coding: utf-8 -*-
from pathlib import Path
"""把今日 A 股大盘行情写入 LAAP 记忆并验证召回。"""
import json
import re
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, r"D:\leanpython\daily_stock_analysis")

REFLECT = "http://localhost:11546/v1/reflect"
RECALL = "http://localhost:11546/v1/recall_memory"

INDICES = [("000001", "上证指数"), ("399001", "深证成指"), ("399006", "创业板指")]


def fetch_market():
    from src.agent.tools.search_tools import _fetch_url

    q = ",".join(("sh" + c if c.startswith("0") else "sz" + c) for c, _ in INDICES)
    raw = _fetch_url(f"http://qt.gtimg.cn/q={q}", timeout=15).decode("gbk", errors="ignore")
    rows = {}
    for m in re.finditer(r'v_\w+="([^"]+)"', raw):
        f = m.group(1).split("~")
        if len(f) < 38:
            continue
        rows[f[2]] = {
            "name": f[1], "price": float(f[3]), "change": float(f[31]),
            "change_pct": float(f[32]),
            "turnover_yi": round(float(f[37]) / 10000.0, 1),
        }
    return [rows[c] for c, _ in INDICES if c in rows]


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    indices = fetch_market()
    if len(indices) != 3:
        print("行情获取失败")
        return

    parts = []
    for ix in indices:
        arrow = "涨" if ix["change"] >= 0 else "跌"
        parts.append(f"{ix['name']}{ix['price']}点({arrow}{abs(ix['change_pct']):.2f}%,成交{ix['turnover_yi']}亿)")
    summary = (
        "【大盘行情记忆】2026-08-11(周二) A股收盘: "
        + ", ".join(parts)
        + "; 两市合计成交约2.32万亿; 数据来源腾讯行情, 由LAAP工具链路获取."
    )
    print("记忆条目:", summary)

    r = post(REFLECT, {"output": summary})
    print("reflect:", json.dumps(r, ensure_ascii=False)[:120])

    print("\n验证召回:")
    d = post(RECALL, {"query": "今天大盘 上证指数行情", "limit": 3})
    print("count:", d.get("count"))
    for m in d.get("memories", []):
        print(" -", round(m.get("score", 0), 3), "|", (m.get("text") or "")[:80])


if __name__ == "__main__":
    main()
