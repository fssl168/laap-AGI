# -*- coding: utf-8 -*-
from pathlib import Path
"""调用 LAAP 工具回答「昨天的自选股怎么样」（LAAP 路由 → kline.db 查询 → 回填）。"""
import json
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

API = "http://localhost:11546/v1/chat/completions"

WATCHLIST_TOOLS = [{"type": "function", "function": {
    "name": "get_watchlist_status",
    "description": "Query the user's stock watchlist status from the local kline database: "
                   "trading day overview, up/down counts, top/bottom performers. "
                   "查询自选股某交易日概况，date 支持 前天(-2)/昨天(-1)/今天(0)/YYYY-MM-DD。",
    "parameters": {"type": "object", "properties": {
        "date": {"type": "string", "description": "交易日：前天/昨天/今天 或 YYYY-MM-DD（默认最新）"}},
        "required": []}}}]

CODES = (
    "600326 002790 601238 000975 600960 601899 002131 002600 002584 600114 "
    "600718 002453 603992 603989 603067 002093 603618 600162 600589 002474 "
    "000938 002599 600789 002448 002261 002044 002036 002629 002613 002195 "
    "002125 600172 603121 603701 603278 603949 601360 002298 600249 600802 "
    "002322 002347"
)


def prefixed(code):
    return ("sh" + code) if code.startswith(("6", "9")) else ("sz" + code)


def query_status(date=""):
    """查 kline.db 交易日概况（带股票名称）。

    date: "" → 最新交易日；"-1" → 昨天；"-2" → 前天；"YYYY-MM-DD" → 指定日期。
    """
    from watchlist_kline_store import (get_day_overview, get_latest_day,
                                       get_stock_names, get_trading_days)

    if not date:
        date = get_latest_day()
    elif str(date).startswith("-"):
        days = get_trading_days(limit=10)
        offset = abs(int(date))
        if offset >= len(days):
            return {"date": str(date), "count": 0, "error": f"交易日不足（仅{len(days)}个）"}
        date = days[offset]
    overview = get_day_overview(date, codes=[prefixed(c) for c in CODES.split()])
    items = overview.get("items", {})
    if not items:
        return {"date": date, "count": 0, "error": "无数据"}
    names = get_stock_names([prefixed(c) for c in CODES.split()])
    with_pct = {k: v for k, v in items.items() if v.get("pct") is not None}
    up = sorted([v for v in with_pct.values() if v["pct"] >= 0], key=lambda x: -x["pct"])
    down = sorted([v for v in with_pct.values() if v["pct"] < 0], key=lambda x: x["pct"])

    def fmt(code, v):
        # 名称+代码（下标）：贵州茅台~600519~
        return {"name": names.get(code, ""), "code": code[2:],
                "close": v["close"], "pct": v["pct"]}

    ranked = sorted(items.items(), key=lambda kv: -(kv[1].get("pct") or 0))
    return {
        "date": overview["date"],
        "count": len(items),
        "up": len(up),
        "down": len(down),
        "top_gainers": [fmt(c, v) for c, v in ranked[:3]],
        "top_losers": [fmt(c, v) for c, v in ranked[-3:]],
        "avg_pct": round(sum(v["pct"] for v in with_pct.values()) / len(with_pct), 2) if with_pct else 0,
    }


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    import sys as _sys

    user_msg = _sys.argv[1] if len(_sys.argv) > 1 else "昨天的自选股怎么样"
    print("=" * 66)
    print(f"STEP 1: LAAP 工具路由 — 「{user_msg}」")
    print("=" * 66)
    msgs = [{"role": "user", "content": user_msg}]
    d = post(API, {"model": "laap-core", "messages": msgs, "tools": WATCHLIST_TOOLS})
    c = d["choices"][0]
    print("finish:", c["finish_reason"], "| engine:", d.get("engine"))
    if not c["message"].get("tool_calls"):
        print("未触发工具:", (c["message"].get("content") or "")[:150])
        return
    tc = c["message"]["tool_calls"][0]
    args = json.loads(tc["function"]["arguments"])
    print(f"tool_calls -> {tc['function']['name']} {tc['function']['arguments']}")
    msgs.append({"role": "assistant", "content": None, "tool_calls": [tc]})

    print("\n" + "=" * 66)
    print(f"STEP 2: 执行 {tc['function']['name']} — 查询 kline.db")
    print("=" * 66)
    result = query_status(args.get("date", ""))
    print(f"交易日 {result['date']} | {result['count']} 只 | 涨 {result.get('up')} 跌 {result.get('down')}")

    def row_fmt(item):
        return f"{item['name']}~{item['code']}~ 收盘{item['close']:.2f} ({item['pct']:+.2f}%)"

    print("涨幅前三:")
    for it in result.get("top_gainers", []):
        print("  ", row_fmt(it))
    print("跌幅前三:")
    for it in result.get("top_losers", []):
        print("  ", row_fmt(it))

    msgs.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"],
                 "content": json.dumps(result, ensure_ascii=False)})

    print("\n" + "=" * 66)
    print("STEP 3: 结果回填 → LAAP 输出")
    print("=" * 66)
    d2 = post(API, {"model": "laap-core", "messages": msgs})
    print("engine:", d2.get("engine"))
    print((d2["choices"][0]["message"].get("content") or "")[:1000])


if __name__ == "__main__":
    main()
