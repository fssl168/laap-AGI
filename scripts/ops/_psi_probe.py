# -*- coding: utf-8 -*-
from pathlib import Path
"""用 LAAP 项目自己的 venv 直接调用 PSI 桥接器，查看完整意识空间。"""
import json
import sys
import os

# 1. 用 LAAP 自己的 venv python 运行本脚本
# 2. 导入 PSI 桥接器
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "aris_brain"))

from psi_jspace_bridge.psi_hermes_adapter import (
    on_conversation_start,
    on_conversation_end,
    STATE_PATH,
)
from psi_jspace_bridge.psi_bridge import (
    get_bridge, load_psi_state, NEED_NAMES,
)

print("=" * 60)
print("LAAP PSI 桥接器 — 直接调用（使用项目自带 venv）")
print("=" * 60)

# 加载当前持久化状态文件
print(f"\n--- 状态文件: {STATE_PATH} ---")
raw = load_psi_state()
print(json.dumps(raw, ensure_ascii=False, indent=2)[:1500] if raw else "(空)")

# 触发一轮认知循环（感知"小龙来看意识空间"）
print("\n" + "=" * 60)
print(">>> on_conversation_start('小龙来看你的意识空间')")
print("=" * 60)
result = on_conversation_start("小龙来看你的意识空间，展示你现在的心理状态")

print("\n--- PSI Preamble ---")
print(result["preamble"])
print("\n--- COT Hint ---")
print(result["cot_hint"])
print("\n--- needs_insight ---")
print(result.get("needs_insight"))
print("\n--- 完整状态 (state) ---")
state = result["state"]
print(json.dumps(state, ensure_ascii=False, indent=2))

# 模拟一轮输出 → 触发反思更新
print("\n" + "=" * 60)
print(">>> on_conversation_end('展示意识空间成功...')")
print("=" * 60)
end_result = on_conversation_end(
    "我展示了 PSI 意识空间：当前主导需求是胜任感，情绪基调正面，处于任务模式。"
    "这是完整的认知状态报告，包含 69 个认知周期的积累。",
    {"success": True, "connection": True},
)
print(json.dumps(end_result, ensure_ascii=False, indent=2)[:800])
