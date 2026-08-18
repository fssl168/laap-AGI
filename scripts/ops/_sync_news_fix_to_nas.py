# -*- coding: utf-8 -*-
"""同步新闻采集修复到 NAS（2026-08-19）:
  - laap/paper_trading/news_pipeline.py  (_freshness_ok days=15 + 日期新鲜度)
  - watchlist_kline_store.py             (upsert_stock_names PG 兼容 + time import)
  - laap/paper_trading/quant_config.py   (LOCAL_LLM_URL → llama.cpp 8080)
  - .env                                 (LLM_SOURCES 顺序 + LOCAL_LLM_URL/MODEL)
"""
import hashlib
import io
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"
LOCAL_ROOT = r"D:\laap-AGI"

FILES = [
    "laap/paper_trading/news_pipeline.py",
    "watchlist_kline_store.py",
    "laap/paper_trading/quant_config.py",
]

ENV_FIXES = [
    ("LLM_SOURCES=openai,urllib,ollama,local,cli",
     "LLM_SOURCES=openai,urllib,local,ollama,cli"),
    ("LOCAL_LLM_URL=http://localhost:1234/v1/chat/completions",
     "LOCAL_LLM_URL=http://localhost:8080/v1/chat/completions"),
    ("LOCAL_LLM_MODEL=local-model",
     "LOCAL_LLM_MODEL=glm-4-9b"),
]


def md5_norm(data: bytes) -> str:
    return hashlib.md5(data.replace(b"\r\n", b"\n")).hexdigest()


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PASS,
              timeout=15, banner_timeout=15, auth_timeout=15,
              allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    ts = "20260819"
    # 备份 + 上传
    for rel in FILES:
        try:
            c.exec_command(f"cp {NAS_ROOT}/{rel} {NAS_ROOT}/{rel}.bak_{ts}")
        except Exception:
            pass
        local = LOCAL_ROOT + "\\" + rel.replace("/", "\\")
        with open(local, "rb") as f:
            data = f.read()
        local_md5 = md5_norm(data)
        c.exec_command(f"mkdir -p {NAS_ROOT}/{'/'.join(rel.split('/')[:-1])}")
        sftp.putfo(io.BytesIO(data), f"{NAS_ROOT}/{rel}")
        with sftp.open(f"{NAS_ROOT}/{rel}", "rb") as f:
            remote_md5 = md5_norm(f.read())
        print(f"  [{'OK' if local_md5 == remote_md5 else 'MD5-MISMATCH!'}] {rel}")
    # .env 修正
    with sftp.open(f"{NAS_ROOT}/.env", "rb") as f:
        env = f.read().decode("utf-8", errors="replace")
    for old, new in ENV_FIXES:
        if old in env:
            env = env.replace(old, new)
            print(f"  .env 替换: {old.split('=')[0]}")
        else:
            print(f"  [skip] 未找到: {old.split('=')[0]}")
    with sftp.open(f"{NAS_ROOT}/.env", "wb") as f:
        f.write(env.encode("utf-8"))
    sftp.close()
    print("上传完成")
    c.close()


if __name__ == "__main__":
    main()
