# -*- coding: utf-8 -*-
from pathlib import Path
"""用 LAAP 读取《Mapping the Mind of a Large Language Model》全文并记忆总结。"""
import json
import urllib.request

API = "http://localhost:11546/v1/chat/completions"
REFLECT = "http://localhost:11546/v1/reflect"
RECALL = "http://localhost:11546/v1/recall_memory"
MD_PATH = str(Path(__file__).resolve().parent.parent.parent / "Mapping_the_Mind_of_a_LLM_Anthropic_fulltext.md")

READ_TOOLS = [{"type": "function", "function": {
    "name": "read_paper",
    "description": "Read the full text of an Anthropic interpretability paper (Mapping the Mind of a Large Language Model). Returns the complete article.",
    "parameters": {"type": "object", "properties": {"paper_id": {"type": "string"}}, "required": ["paper_id"]}}}]

PAPER_SUMMARY = (
    "【论文记忆】Anthropic《Mapping the Mind of a Large Language Model》(2024-05-21, 官网论文): "
    "首次用 dictionary learning(稀疏自编码器) 映射 Claude 3 Sonnet 中间层, 提取数百万可解释概念特征 "
    "(城市/人物/科学领域/编程语法), 特征多语言多模态; 发现特征间连接(circuits), 含抽象心智状态概念; "
    "宣称是首次对生产级大模型内部做详细审视, 用于可解释性与 AI 安全; "
    "技术细节见 companion 论文《Scaling Monosemanticity: Extracting Interpretable Features from Claude 3 Sonnet》. "
    "阅读来源: anthropic.com/research/mapping-mind-language-model"
)


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    print("=" * 66)
    print("STEP 1: LAAP 工具路由 — read_paper")
    print("=" * 66)
    msgs = [{"role": "user", "content": "帮我读一下 Mapping the Mind of a Large Language Model 这篇论文的全文"}]
    d = post(API, {"model": "laap-core", "messages": msgs, "tools": READ_TOOLS})
    c = d["choices"][0]
    print("finish:", c["finish_reason"], "| engine:", d.get("engine"))
    tc = c["message"]["tool_calls"][0]
    fn = tc["function"]["name"]
    print(f"tool_calls -> {fn} {tc['function']['arguments']}")
    msgs.append({"role": "assistant", "content": None, "tool_calls": [tc]})

    print("\n" + "=" * 66)
    print(f"STEP 2: 执行 {fn} — 读取论文全文")
    print("=" * 66)
    fulltext = open(MD_PATH, encoding="utf-8").read()
    print(f"读取 {len(fulltext)} 字符")
    # 全文太长，作为工具结果截断给 LAAP（记忆条目用精炼版）
    tool_content = fulltext[:6000]
    msgs.append({"role": "tool", "tool_call_id": tc["id"], "name": fn, "content": tool_content})

    print("\n" + "=" * 66)
    print("STEP 3: 结果回填 → LAAP 处理")
    print("=" * 66)
    d2 = post(API, {"model": "laap-core", "messages": msgs})
    print("engine:", d2.get("engine"))
    print((d2["choices"][0]["message"].get("content") or "")[:300])

    print("\n" + "=" * 66)
    print("STEP 4: 记忆总结 → /v1/reflect 写入 LAAP 语义记忆")
    print("=" * 66)
    summary = PAPER_SUMMARY
    r = post(REFLECT, {"output": summary})
    print("reflect response:", json.dumps(r, ensure_ascii=False)[:150])
    print("written:", summary[:80], "...")

    print("\n" + "=" * 66)
    print("STEP 5: 验证召回 — /v1/recall_memory")
    print("=" * 66)
    r2 = post(RECALL, {"query": "Mapping the Mind 意识空间 论文", "limit": 3})
    print("count:", r2.get("count"))
    for m in r2.get("memories", []):
        print(" -", round(m.get("score", 0), 3), "|", (m.get("text") or "")[:80])


if __name__ == "__main__":
    main()
