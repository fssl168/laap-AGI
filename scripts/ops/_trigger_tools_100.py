# -*- coding: utf-8 -*-
"""继续发起 100 轮工具调用，扩展元学习会话数据积累（coding/intent 为主）"""
import sys

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from laap.agi.meta_session_db import count_by_domain
from aris_brain.aris_cognitive_bridge import get_bridge

# 100 轮工具调用序列（固定模式可复现；成功/失败 ≈ 80/20）
calls = []
# coding 45 轮
for i in range(15):
    calls.append(("generate_code", True if i % 5 else False))
for i in range(15):
    calls.append(("verify_generated_code", True if i % 4 else False))
for i in range(10):
    calls.append(("apply_patch", True if i % 5 else False))
calls += [("compile_and_test", True), ("generate_function", True),
          ("code_review", False), ("generate_class", True), ("fix_bug", True)]
# intent 25 轮
for i in range(10):
    calls.append(("ask_user_to_clarify", True if i % 4 else False))
for i in range(10):
    calls.append(("ask_followup_question", True if i % 5 else False))
for i in range(5):
    calls.append(("clarify_intent", True if i % 3 else False))
# api 15 轮
for i in range(12):
    calls.append(("retry_with_alternatives", True if i % 6 else False))
calls += [("api_call", True), ("api_call", False), ("fallback_request", True)]
# complex 8 轮
for i in range(8):
    calls.append(("decompose_task", True if i % 4 else False))
# general 7 轮
for i in range(4):
    calls.append(("map_to_known_pattern", True))
calls += [("review_notes", True), ("summarize", True), ("search_memory", False)]

assert len(calls) == 100, f"调用数应为 100，实际 {len(calls)}"

print(f"发起 {len(calls)} 轮工具调用 ...")
bridge = get_bridge()
for i, (name, ok) in enumerate(calls, 1):
    bridge.after_tool(name, {"ok": ok}, success=ok)
    if i % 20 == 0:
        print(f"  ... {i}/100")

print("\n=== SQLite 会话数据统计（累计） ===")
stats = count_by_domain()
total = 0
for domain, s in sorted(stats.items()):
    total += s["count"]
    print(f"  {domain:<10} 会话数={s['count']:>3}  成功={s['successful']:>3}")
print(f"  {'总计':<10} {total} 条")
