# -*- coding: utf-8 -*-
"""发起代码生成/意图澄清类工具调用，积累元学习会话数据（coding/intent 等领域）"""
import sys

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from laap.agi.meta_session_db import count_by_domain
from aris_brain.aris_cognitive_bridge import get_bridge

print("初始化 cognitive bridge ...")
bridge = get_bridge()
print(f"bridge 就绪 (laap_available={bridge._laap_available}, "
      f"meta_learning={'meta_learning' in bridge._laap_modules})")

# 工具调用序列（模拟真实工具执行完成后的 hook，成功/失败混合）
calls = [
    # coding 领域（代码生成/验证）
    ("generate_code", True),
    ("verify_generated_code", True),
    ("generate_code", True),
    ("verify_generated_code", False),
    ("apply_patch", True),
    # intent 领域（意图澄清）
    ("ask_user_to_clarify", True),
    ("ask_followup_question", True),
    ("clarify_intent", False),
    # 其他领域（对照）
    ("retry_with_alternatives", True),
    ("decompose_task", True),
    ("map_to_known_pattern", True),
]

for name, ok in calls:
    bridge.after_tool(name, {"ok": ok}, success=ok)
    print(f"  tool: {name:<28} success={ok}")

print("\n=== SQLite 会话数据统计 ===")
stats = count_by_domain()
for domain, s in sorted(stats.items()):
    print(f"  {domain:<10} 会话数={s['count']}  成功={s['successful']}")

if "coding" in stats and "intent" in stats:
    print(f"\n✅ coding={stats['coding']['count']} 条, intent={stats['intent']['count']} 条")
else:
    print("\n⚠️ 部分领域无数据，检查记录链路")
