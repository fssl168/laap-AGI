# -*- coding: utf-8 -*-
"""用 LAAP 工具记录自选股（LAAP 路由 add_watchlist → 写入记忆 → 验证召回）。"""
import json
import urllib.request

API = "http://localhost:11546/v1/chat/completions"
REFLECT = "http://localhost:11546/v1/reflect"
RECALL = "http://localhost:11546/v1/recall_memory"

WATCHLIST_TOOLS = [{"type": "function", "function": {
    "name": "add_watchlist",
    "description": "Record the user's stock watchlist (记录自选股列表) into memory. "
                   "Extracts all A-share stock codes from the message.",
    "parameters": {"type": "object", "properties": {
        "stock_codes": {"type": "string", "description": "Space-separated A-share codes"}},
        "required": ["stock_codes"]}}}]

WATCHLIST = (
    "600326 002790 601238 000975 600960 601899 002131 002600 002584 600114 "
    "600718 002453 603992 603989 603067 002093 603618 600162 600589 002474 "
    "000938 002599 600789 002448 002261 002044 002036 002629 002613 002195 "
    "002125 600172 603121 603701 603278 603949 601360 002298 600249 600802 "
    "002322 002347"
)


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    user_msg = f"请把我的自选股 {WATCHLIST} 记录到记忆里"
    print("=" * 66)
    print("STEP 1: LAAP 工具路由 — add_watchlist")
    print("=" * 66)
    msgs = [{"role": "user", "content": user_msg}]
    d = post(API, {"model": "laap-core", "messages": msgs, "tools": WATCHLIST_TOOLS})
    c = d["choices"][0]
    print("finish:", c["finish_reason"], "| engine:", d.get("engine"))
    if not c["message"].get("tool_calls"):
        print("未触发工具:", (c["message"].get("content") or "")[:120])
        return
    tc = c["message"]["tool_calls"][0]
    args = json.loads(tc["function"]["arguments"])
    codes = (args.get("stock_codes") or "").split()
    print(f"tool_calls -> {tc['function']['name']} | 提取代码 {len(codes)} 只")
    msgs.append({"role": "assistant", "content": None, "tool_calls": [tc]})

    print("\n" + "=" * 66)
    print("STEP 2: 执行 — 写入 LAAP 语义记忆")
    print("=" * 66)
    summary = (
        f"【自选股记忆】用户A股自选股列表({len(codes)}只): {' '.join(codes)}; "
        "用于每日关注行情与选股分析."
    )
    r = post(REFLECT, {"output": summary})
    print("reflect:", json.dumps(r, ensure_ascii=False)[:80])

    msgs.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"],
                 "content": json.dumps({"written": True, "count": len(codes)})})
    d2 = post(API, {"model": "laap-core", "messages": msgs})
    print("回填 engine:", d2.get("engine"))

    print("\n" + "=" * 66)
    print("STEP 3: 验证召回 — 「我的自选股」")
    print("=" * 66)
    d3 = post(RECALL, {"query": "我的自选股列表", "limit": 3})
    print("count:", d3.get("count"))
    for m in d3.get("memories", []):
        print(" -", round(m.get("score", 0), 3), "|", (m.get("text") or "")[:90])


if __name__ == "__main__":
    main()
