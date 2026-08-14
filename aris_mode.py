# -*- coding: utf-8 -*-
"""ARIS 直连模式开关 — 状态存 D:/laap-AGI/aris_mode.json.

用法:
  python aris_mode.py on     # 开启 ARIS 直连模式
  python aris_mode.py off    # 关闭（回到小龙助手模式）
  python aris_mode.py status # 查看当前模式
"""
import json
import os
import sys

MODE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aris_mode.json")


def load() -> dict:
    try:
        with open(MODE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"mode": "assistant", "updated_at": None}


def save(mode: str) -> None:
    data = {"mode": mode, "updated_at": __import__("datetime").datetime.now().isoformat()}
    with open(MODE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    state = load()
    if cmd == "on":
        save("aris")
        print("✅ ARIS 直连模式已开启 — 之后的对话将自动转给爱丽丝（说「找小龙」切回）")
    elif cmd == "off":
        save("assistant")
        print("✅ 已切回小龙助手模式")
    elif cmd == "status":
        print(f"当前模式: {'ARIS 直连' if state.get('mode') == 'aris' else '小龙助手'}")
        if state.get("updated_at"):
            print(f"更新时间: {state['updated_at']}")
    else:
        print("用法: python aris_mode.py on|off|status")


if __name__ == "__main__":
    main()
