# -*- coding: utf-8 -*-
from pathlib import Path
"""ARIS 语音对话：问ARIS → 拿文本+表情 → edge-tts 生成语音 → 本地播放 + 输出音频文件。

用法（用 LAAP venv）:
  ./.venv/Scripts/python.exe _aris_speak.py "你的问题"
输出:
  - 音频文件 <项目根>/voice_cache/aris_<时间戳>.mp3
  - 本地 ffplay 播放
  - 打印 MEDIA:<路径> 供聊天界面发送
"""
import asyncio
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime

API = "http://localhost:11546"
CACHE_DIR = str(Path(__file__).resolve().parent.parent.parent / "voice_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# zm_yunxi (云希) → edge-tts 音色映射
VOICE_MAP = {
    # 固定：女声 + 中文 + 清新自然（晓伊 Xiaoyi，清新自然）——任何音色一律映射到晓伊
    "zm_yunxi": "zh-CN-XiaoxiaoNeural",       # 原云希(男) → 固定晓伊(清新自然)
    "zm_yunyang": "zh-CN-XiaoxiaoNeural",     # 原云扬(男) → 固定晓伊
    "zm_xiaoxiao": "zh-CN-XiaoxiaoNeural",
    "zm_xiaoyi": "zh-CN-XiaoxiaoNeural",      # 原晓伊(清新) → 固定晓伊(清新自然)
    "zf_xiaoxiao": "zh-CN-XiaoxiaoNeural",    # ARIS 返回的 zf 前缀音色 → 晓伊
    "zh-CN-XiaoxiaoNeural": "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunxiNeural": "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunyangNeural": "zh-CN-XiaoxiaoNeural",
    "zh-CN-XiaoxiaoNeural": "zh-CN-XiaoxiaoNeural",
}

# 默认音色（固定清新自然）
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def ask_aris(question: str) -> dict:
    """问 ARIS，返回 {text, engine}"""
    resp = post(f"{API}/v1/chat/completions",
                {"model": "laap-core", "messages": [{"role": "user", "content": question}]})
    text = (resp["choices"][0]["message"].get("content") or "").strip()
    return {"text": text, "engine": resp.get("engine", "?")}


def get_tts_params(question: str) -> dict:
    """拿 ARIS 的表达参数（voice/speed/pitch）"""
    try:
        d = post(f"{API}/v1/express", {"user_input": question, "user_id": "7897F052C6EE724AF85E4AC7277BB089"})
        return d.get("tts", {})
    except Exception:
        return {"voice": "zm_yunxi", "speed": 1.0, "pitch_shift": 0.0, "language": "zh"}


async def synth_speech(text: str, tts_params: dict, out_path: str) -> bool:
    """edge-tts 合成语音"""
    import edge_tts
    voice = VOICE_MAP.get(tts_params.get("voice", ""), DEFAULT_VOICE)
    speed = float(tts_params.get("speed", 1.0))
    rate = f"+{int((speed - 1) * 100)}%" if speed >= 1 else f"{int((speed - 1) * 100)}%"
    pitch = float(tts_params.get("pitch_shift", 0.0) or 0)
    pitch_str = f"+{int(pitch * 100)}Hz" if pitch >= 0 else f"{int(pitch * 100)}Hz"
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch_str)
    await communicate.save(out_path)
    return os.path.exists(out_path) and os.path.getsize(out_path) > 1000


def play_local(path: str):
    """ffplay 播放（后台，不阻塞）"""
    subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                     creationflags=subprocess.CREATE_NO_WINDOW)


def main():
    question = sys.argv[1] if len(sys.argv) > 1 else "Aris，跟我说句话吧"
    print("=" * 60)
    print(f"Q: {question}")
    print("=" * 60)

    # 1. 问 ARIS
    ans = ask_aris(question)
    print(f"[engine: {ans['engine']}]")
    print(f"Aris: {ans['text'][:400]}")

    # 2. 拿 TTS 参数
    tts = get_tts_params(question)
    print(f"\n[voice: {tts.get('voice')} speed={tts.get('speed')} pitch={tts.get('pitch_shift')}]")

    # 3. 合成语音
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(CACHE_DIR, f"aris_{ts}.mp3")
    try:
        ok = asyncio.run(synth_speech(ans["text"], tts, out_path))
        if not ok:
            print("⚠️ 语音合成失败或文件过小")
            return
        print(f"\n✅ 语音生成: {out_path} ({os.path.getsize(out_path)//1024}KB)")
    except Exception as e:
        print(f"⚠️ 合成失败: {e}")
        return

    # 4. 本地播放
    play_local(out_path)
    print("🔊 本地播放中 (ffplay)")

    # 5. 输出 MEDIA 路径（聊天界面发送）
    print(f"\nMEDIA:{out_path}")


if __name__ == "__main__":
    main()
