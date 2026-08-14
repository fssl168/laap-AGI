# -*- coding: utf-8 -*-
from pathlib import Path
"""ARIS 微信语音对讲 — 问 ARIS → 文本回复 → mp3 → silk(微信原生语音气泡).

用法（用 LAAP venv）:
  ./.venv/Scripts/python.exe aris_voice.py "你的问题"
输出 MEDIA:<silk路径> — Hermes 微信网关对 .silk 走原生气泡分支。
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

VOICE_MAP = {
    # 固定：女声 + 中文 + 清新自然（晓伊 Xiaoyi，清新自然）
    "zm_yunxi": "zh-CN-XiaoyiNeural",      # 原云希(男) → 固定晓伊(清新自然)
    "zm_yunyang": "zh-CN-XiaoyiNeural",
    "zm_xiaoxiao": "zh-CN-XiaoyiNeural",
    "zm_xiaoyi": "zh-CN-XiaoyiNeural",
    "zf_xiaoxiao": "zh-CN-XiaoyiNeural",   # ARIS 返回的 zf 前缀音色 → 晓伊
    "zh-CN-XiaoyiNeural": "zh-CN-XiaoyiNeural",
    "zh-CN-YunxiNeural": "zh-CN-XiaoyiNeural",
    "zh-CN-YunyangNeural": "zh-CN-XiaoyiNeural",
}

# 默认音色（固定清新自然）
DEFAULT_VOICE = "zh-CN-XiaoyiNeural"

# 多轮历史
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aris_chat_history.json")


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


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


def ask_aris(question: str) -> dict:
    history = load_history()
    history.append({"role": "user", "content": question})
    history = history[-40:]
    resp = post(f"{API}/v1/chat/completions",
                {"model": "laap-core", "messages": history})
    text = (resp["choices"][0]["message"].get("content") or "").strip()
    history.append({"role": "assistant", "content": text})
    save_history(history)
    return {"text": text, "engine": resp.get("engine", "?")}


def get_tts_params(question: str) -> dict:
    try:
        d = post(f"{API}/v1/express", {"user_input": question, "user_id": "7897F052C6EE724AF85E4AC7277BB089"})
        return d.get("tts", {})
    except Exception:
        return {"voice": "zm_yunxi", "speed": 1.0, "pitch_shift": 0.0, "language": "zh"}


async def synth_speech(text: str, tts_params: dict, out_path: str) -> bool:
    import edge_tts
    # 固定清新自然：任何来源的音色参数都映射到 DEFAULT_VOICE
    voices = [DEFAULT_VOICE]
    speed = float(tts_params.get("speed", 1.0) or 1.0)
    rate = f"+{int((speed - 1) * 100)}%" if speed >= 1 else f"{int((speed - 1) * 100)}%"
    pitch = float(tts_params.get("pitch_shift", 0.0) or 0)
    pitch_str = f"+{int(pitch * 100)}Hz" if pitch >= 0 else f"{int(pitch * 100)}Hz"
    last_err = None
    for v in voices:
        if not v:
            continue
        voice = VOICE_MAP.get(v, DEFAULT_VOICE)
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch_str)
            await communicate.save(out_path)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                return True
        except Exception as e:
            last_err = e
            await asyncio.sleep(2)
    if last_err:
        raise last_err
    return False


def mp3_to_silk(mp3_path: str, silk_path: str) -> bool:
    """mp3 → wav(24kHz mono) → silk(腾讯标准, 24000Hz 16bit)."""
    import pilk
    wav = mp3_path.replace(".mp3", "_conv.wav")
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", mp3_path, "-ar", "24000", "-ac", "1", "-f", "wav", wav],
        capture_output=True,
    )
    if r.returncode != 0:
        print("ffmpeg 失败:", r.stderr.decode()[-200:])
        return False
    try:
        pilk.encode(wav, silk_path, pcm_rate=24000, tencent=True)
    finally:
        if os.path.exists(wav):
            os.remove(wav)
    return os.path.exists(silk_path) and os.path.getsize(silk_path) > 500


def main():
    question = sys.argv[1] if len(sys.argv) > 1 else "Aris，跟我说句话吧"
    print(f"Q: {question}")

    # 1. 问 ARIS
    ans = ask_aris(question)
    print(f"[engine: {ans['engine']}]")
    print(f"Aris: {ans['text'][:400]}")

    if not ans["text"] or len(ans["text"].strip()) < 4:
        print("⚠️ ARIS 回复过短(可能命中机械 fallback),跳过语音合成。")
        print(f"回复原文: {ans['text']!r}")
        return

    # 2. TTS 参数
    tts = get_tts_params(question)

    # 3. 合成 mp3
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    mp3_path = os.path.join(CACHE_DIR, f"aris_{ts}.mp3")
    silk_path = os.path.join(CACHE_DIR, f"aris_{ts}.silk")
    try:
        ok = asyncio.run(synth_speech(ans["text"], tts, mp3_path))
        if not ok:
            print("⚠️ 语音合成失败(edge-tts 限流?),重试一次...")
            asyncio.run(asyncio.sleep(2))
            ok = asyncio.run(synth_speech(ans["text"], tts, mp3_path))
            if not ok:
                print("重试仍失败。")
                return
    except Exception as e:
        print(f"⚠️ 合成失败: {e}")
        return

    # 4. 转 silk
    if not mp3_to_silk(mp3_path, silk_path):
        print("⚠️ silk 转换失败,退回 mp3 附件")
        print(f"MEDIA:{mp3_path}")
        return

    print(f"✅ 语音就绪: {silk_path} ({os.path.getsize(silk_path)//1024}KB)")
    print(f"MEDIA:{silk_path}")


if __name__ == "__main__":
    main()
