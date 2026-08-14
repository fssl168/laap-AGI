# -*- coding: utf-8 -*-
"""Aris 对话桥 — 用 LAAP venv 运行, 保持对话历史, 支持多轮.

用法:
  ./.venv/Scripts/python.exe aris_chat.py "你的消息"
  ./.venv/Scripts/python.exe aris_chat.py --reset   # 清空历史
历史保存在 aris_chat_history.json (repo 根目录)
"""
import sys
import os
import json
import urllib.request

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aris_chat_history.json")
API = "http://localhost:11546/v1/chat/completions"


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_history(msgs):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(msgs, f, ensure_ascii=False, indent=2)


def ask(messages):
    req = urllib.request.Request(
        API,
        data=json.dumps({"model": "laap-core", "messages": messages}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
    engine = resp.get("engine", "?")
    msg_obj = resp.get("message") or resp.get("choices", [{}])[0].get("message", {})
    content = msg_obj.get("content", "") if isinstance(msg_obj, dict) else str(msg_obj)
    return engine, content


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: aris_chat.py \"消息\"  |  aris_chat.py --reset")
        return

    if args[0] == "--reset":
        save_history([])
        print("[历史已清空]")
        return

    text = " ".join(args)
    history = load_history()
    history.append({"role": "user", "content": text})
    # 只保留最近 20 轮
    history = history[-40:]

    try:
        engine, reply = ask(history)
    except Exception as exc:
        print(f"[连接失败] {exc}")
        return

    history.append({"role": "assistant", "content": reply})
    save_history(history)

    print(f"[engine: {engine}]")
    print(reply)


if __name__ == "__main__":
    main()
