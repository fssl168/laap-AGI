# -*- coding: utf-8 -*-
"""调用 LAAP 工具总结今日 A 股市场行情（LAAP 路由 → 腾讯行情 → 回填总结）。"""
import json
import re
import urllib.request

API = "http://localhost:11546/v1/chat/completions"

MARKET_TOOLS = [{"type": "function", "function": {
    "name": "get_market_overview",
    "description": "Get today's A-share market overview: major indices (上证指数/深证成指/创业板指), "
                   "change percent, turnover. 获取今日A股大盘行情总结。",
    "parameters": {"type": "object", "properties": {"region": {"type": "string", "enum": ["cn", "hk", "us"]}},
                   "required": []}}}]

INDICES = [("000001", "上证指数"), ("399001", "深证成指"), ("399006", "创业板指")]


def fetch_market():
    """腾讯行情 API：拉三大指数。"""
    q = ",".join(("sh" + code if code.startswith("0") else "sz" + code) for code, _ in INDICES)
    req = urllib.request.Request(f"http://qt.gtimg.cn/q={q}",
                                 headers={"User-Agent": "Mozilla/5.0", "Referer": "http://finance.qq.com/"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="ignore")
    rows = {}
    for m in re.finditer(r'v_\w+="([^"]+)"', raw):
        f = m.group(1).split("~")
        if len(f) < 38:
            continue
        rows[f[2]] = {
            "name": f[1],
            "price": float(f[3]),
            "prev_close": float(f[4]),
            "open": float(f[5]),
            "change": float(f[31]),
            "change_pct": float(f[32]),
            "high": float(f[33]),
            "low": float(f[34]),
            "turnover_yi": round(float(f[37]) / 10000.0, 1),  # 万 → 亿
        }
    indices = [rows[code] for code, _ in INDICES if code in rows]
    return {
        "date": "2026-08-11",
        "count": len(indices),
        "indices": indices,
    }


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    print("=" * 66)
    print("STEP 1: LAAP 工具路由 — 「总结一下今天A股市场行情」")
    print("=" * 66)
    msgs = [{"role": "user", "content": "总结一下今天A股市场行情"}]
    d = post(API, {"model": "laap-core", "messages": msgs, "tools": MARKET_TOOLS})
    c = d["choices"][0]
    print("finish:", c["finish_reason"], "| engine:", d.get("engine"))
    if not c["message"].get("tool_calls"):
        print("未触发工具:", (c["message"].get("content") or "")[:120])
        return
    tc = c["message"]["tool_calls"][0]
    print(f"tool_calls -> {tc['function']['name']} {tc['function']['arguments']}")
    msgs.append({"role": "assistant", "content": None, "tool_calls": [tc]})

    print("\n" + "=" * 66)
    print("STEP 2: 执行 get_market_overview — 腾讯行情")
    print("=" * 66)
    result = fetch_market()
    for ix in result["indices"]:
        arrow = "▲" if ix["change"] >= 0 else "▼"
        print(f"  {ix['name']}: {ix['price']} {arrow}{abs(ix['change']):.2f} ({ix['change_pct']:+.2f}%) 成交{ix['turnover_yi']}亿")

    msgs.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"],
                 "content": json.dumps(result, ensure_ascii=False)})

    print("\n" + "=" * 66)
    print("STEP 3: 结果回填 → LAAP 输出")
    print("=" * 66)
    d2 = post(API, {"model": "laap-core", "messages": msgs})
    print("engine:", d2.get("engine"))
    print((d2["choices"][0]["message"].get("content") or "")[:1200])


if __name__ == "__main__":
    main()
