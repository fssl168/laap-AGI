# -*- coding: utf-8 -*-
"""ARIS 直连模式开关 — 状态存 D:/laap-AGI/aris_mode.json.

用法（用 LAAP venv 或任意 python）:
  python aris_mode.py on     # 开启 ARIS 直连模式
  python aris_mode.py off    # 关闭（回到小龙助手模式）
  python aris_mode.py status # 查看当前模式

开启后: 用户在 QQ/微信发的每句话自动转给 ARIS，回复带语音。
关闭后: 正常助手模式。
"""
import json
import os
import sys

MODE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aris_mode.json")


def load_mode() -> dict:
    if os.path.exists(MODE_FILE):
        try:
            with open(MODE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"mode": "assistant", "updated_at": None}


def save_mode(mode: str) -> None:
    data = {"mode": mode, "updated_at": __import__("datetime").datetime.now().isoformat()}
    with open(MODE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "on":
        save_mode("aris")
        print("✅ ARIS 直连模式已开启 — 之后的对话将自动转给爱丽丝（说「找小龙」切回）")
    elif action == "off":
        save_mode("assistant")
        print("✅ 已切回小龙助手模式")
    elif action == "status":
        m = load_mode()
        mode = m.get("mode", "assistant")
        print(f"当前模式: {'ARIS 直连' if mode == 'aris' else '小龙助手'}")
        print(f"更新时间: {m.get('updated_at', '-')}")
    else:
        print("用法: python aris_mode.py on|off|status")


if __name__ == "__main__":
    main()
