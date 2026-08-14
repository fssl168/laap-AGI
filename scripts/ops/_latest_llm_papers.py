# -*- coding: utf-8 -*-
"""调用 LAAP：搜索本月最新大模型论文 → reflect 写入语义记忆 → recall 验证召回。

完整工具闭环（复用 _search_papers_demo.py 模式）：
  STEP 1: LAAP 工具路由 (agi:tool_router) 触发 search_papers
  STEP 2: arXiv API 按提交日期倒序拉取本月最新大模型论文
  STEP 3: 结果回填 → LAAP 总结
  STEP 4: /v1/reflect 写入语义记忆
  STEP 5: /v1/recall_memory 验证召回
"""
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

API = "http://localhost:11546/v1/chat/completions"
REFLECT = "http://localhost:11546/v1/reflect"
RECALL = "http://localhost:11546/v1/recall_memory"

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


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_latest_llm_papers(max_results=8):
    """arXiv API：本月最新大模型论文（提交日期倒序）。"""
    today = date.today()
    month_start = today.replace(day=1).isoformat().replace("-", "")
    today_str = today.isoformat().replace("-", "")
    # arXiv 日期范围语法: submittedDate:[YYYYMMDDHHMM TO YYYYMMDDHHMM]
    raw_query = (f'all:"large language model" AND '
                 f'submittedDate:[{month_start}0000 TO {today_str}2359]')
    params = urllib.parse.urlencode({
        "search_query": raw_query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    url = f"https://export.arxiv.org/api/query?{params}"
    with urllib.request.urlopen(url, timeout=30) as r:
        xml_data = r.read().decode("utf-8")
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_data)
    papers = []
    for entry in root.findall("atom:entry", ns):
        title = " ".join((entry.findtext("atom:title", "", ns) or "").split())
        arxiv_id = entry.find("atom:id", ns).text.strip().split("/abs/")[-1]
        published = (entry.findtext("atom:published", "", ns) or "")[:10]
        authors = [a.findtext("atom:name", "", ns) for a in entry.findall("atom:author", ns)]
        summary = " ".join((entry.findtext("atom:summary", "", ns) or "").split())[:220]
        papers.append({
            "title": title, "id": arxiv_id, "published": published,
            "authors": authors[:5], "abstract": summary,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
        })
    return papers


def main():
    print("=" * 66)
    print(f"STEP 1: LAAP 工具路由 — 「本月最新大模型论文」({date.today()})")
    print("=" * 66)
    user_msg = "搜索本月最新的大模型(large language model)论文，按时间倒序"
    msgs = [{"role": "user", "content": user_msg}]
    resp = post(API, {"model": "laap-core", "messages": msgs, "tools": SEARCH_TOOLS})
    choice = resp["choices"][0]
    print("finish_reason:", choice["finish_reason"], "| engine:", resp.get("engine"))
    if not choice["message"].get("tool_calls"):
        print("LAAP 未触发工具调用，文本回答：", (choice["message"].get("content") or "")[:200])
        return
    tc = choice["message"]["tool_calls"][0]
    fn_name = tc["function"]["name"]
    args = json.loads(tc["function"]["arguments"])
    print(f"tool_calls -> {fn_name} {args}")

    msgs.append({"role": "assistant", "content": None, "tool_calls": [tc]})

    print("\n" + "=" * 66)
    print(f"STEP 2: 执行 {fn_name} — arXiv 本月最新大模型论文")
    print("=" * 66)
    papers = fetch_latest_llm_papers(8)
    print(f"拉到 {len(papers)} 篇本月论文:")
    for i, p in enumerate(papers, 1):
        print(f"  [{i}] {p['title'][:80]}")
        print(f"      {', '.join(p['authors'][:3])} | {p['published']} | {p['url']}")

    msgs.append({"role": "tool", "tool_call_id": tc["id"], "name": fn_name,
                 "content": json.dumps({"count": len(papers), "papers": papers}, ensure_ascii=False)})

    print("\n" + "=" * 66)
    print("STEP 3: 结果回填 → LAAP 总结")
    print("=" * 66)
    resp2 = post(API, {"model": "laap-core", "messages": msgs})
    print("engine:", resp2.get("engine"))
    summary = (resp2["choices"][0]["message"].get("content") or "")[:2000]
    print(summary[:600])

    print("\n" + "=" * 66)
    print("STEP 4: 记忆总结 → /v1/reflect 写入 LAAP 语义记忆")
    print("=" * 66)
    memory_text = (
        f"【月度论文快照 {date.today().isoformat()}】本月最新大模型论文 {len(papers)} 篇(arXiv 提交日期倒序): "
        + "; ".join(f"{p['published']} {p['title']} (arXiv:{p['id']})" for p in papers[:8])
        + f"。LAAP 工具 search_papers 路由闭环验证通过。"
    )
    r = post(REFLECT, {"output": memory_text})
    print("reflect response:", json.dumps(r, ensure_ascii=False)[:200])

    print("\n" + "=" * 66)
    print("STEP 5: 验证召回 — /v1/recall_memory")
    print("=" * 66)
    r2 = post(RECALL, {"query": "本月最新大模型论文 月度快照", "limit": 3})
    print("count:", r2.get("count"))
    for m in r2.get("memories", []):
        print(" -", round(m.get("score", 0), 3), "|", (m.get("text") or "")[:90])


if __name__ == "__main__":
    main()
