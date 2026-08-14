# -*- coding: utf-8 -*-
"""调用 LAAP 搜索 anthropic 意识空间论文（完整工具闭环，可复用版）。"""
import json
import sys
import urllib.request

API = "http://localhost:11546/v1/chat/completions"

SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "Search academic papers (中英文论文资料查询). Supports arXiv "
                           "and OpenAlex/Crossref including Chinese journals. Chinese "
                           "queries route to Crossref automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search topic"},
                    "max_results": {"type": "integer", "description": "Max results (default 5)"},
                    "source": {"type": "string", "enum": ["auto", "arxiv", "openalex", "crossref"]},
                    "language": {"type": "string", "enum": ["all", "zh", "en"]},
                },
                "required": ["query"],
            },
        },
    }
]


def chat(messages, tools=None):
    payload = {"model": "laap-core", "messages": messages}
    if tools:
        payload["tools"] = tools
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    user_msg = sys.argv[1] if len(sys.argv) > 1 else "搜索关于AGI的论文"
    max_results = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    messages = [{"role": "user", "content": user_msg}]

    print("=" * 66)
    print(f"STEP 1: LAAP 工具路由 — 「{user_msg}」")
    print("=" * 66)
    resp = chat(messages, SEARCH_TOOLS)
    choice = resp["choices"][0]
    print("finish_reason:", choice["finish_reason"], "| engine:", resp.get("engine"))
    if not choice["message"].get("tool_calls"):
        print("LAAP 未触发工具调用，文本回答：", (choice["message"].get("content") or "")[:200])
        return
    tc = choice["message"]["tool_calls"][0]
    fn_name = tc["function"]["name"]
    args = json.loads(tc["function"]["arguments"])
    print(f"tool_calls -> {fn_name} {args}")

    messages.append({"role": "assistant", "content": None, "tool_calls": [tc]})

    print("\n" + "=" * 66)
    print(f"STEP 2: 执行 {fn_name}(query={args.get('query')!r}, max_results={max_results}) — arXiv/Crossref")
    print("=" * 66)
    sys.path.insert(0, r"D:\leanpython\daily_stock_analysis")
    from src.agent.tools.search_tools import _handle_search_papers  # noqa: E402

    result = _handle_search_papers(
        args.get("query", ""),
        max_results=max_results,
        source=args.get("source", "auto"),
        language=args.get("language", "all"),
    )
    if "error" in result:
        print("搜索失败:", result["error"])
        return
    if result.get("hint"):
        print("提示:", result["hint"])
    for i, p in enumerate(result["papers"], 1):
        print(f"  [{i}] {p['title'][:78]}")
        print(f"      {', '.join(p['authors'][:3])} | {p.get('source','')} {p.get('year','')} | {p['url']}")

    messages.append({"role": "tool", "tool_call_id": tc["id"], "name": fn_name,
                     "content": json.dumps(result, ensure_ascii=False)})

    print("\n" + "=" * 66)
    print("STEP 3: 结果回填 → LAAP 总结")
    print("=" * 66)
    resp2 = chat(messages)
    print(f"engine: {resp2.get('engine')}")
    print((resp2["choices"][0]["message"].get("content") or "")[:1500])


if __name__ == "__main__":
    main()
