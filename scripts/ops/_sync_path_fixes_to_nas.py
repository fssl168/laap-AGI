# -*- coding: utf-8 -*-
"""同步本次路径修复到 NAS（2026-08-18）。

文件: watchlist_kline_store.py / laap/paper_trading/db.py / tests/conftest.py /
      scripts/backtest_multi_symbol.py / scripts/ops/_sync_data_to_nas.py /
      aris_brain/paper_trading_tools.py / scripts/migrate_*.py
NAS .env 修正: KLINE_DB_URL→watchlist_kline_store 库, PAPER_TRADING_DB_PATH→data/laap_trading.db,
      新增 WATCHLIST_KLINE_DB_PATH=data/watchlist_kline_store.db（NAS 真库在根目录）。
"""
import hashlib
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"
LOCAL_ROOT = r"D:\laap-AGI"

FILES = [
    "watchlist_kline_store.py",
    "laap/paper_trading/db.py",
    "tests/conftest.py",
    "scripts/backtest_multi_symbol.py",
    "scripts/ops/_sync_data_to_nas.py",
    "aris_brain/paper_trading_tools.py",
    "scripts/migrate_meta_sessions_redo.py",
    "scripts/migrate_rsi_tables.py",
]

ENV_FIXES = [
    # (旧, 新) 精确行替换
    ("KLINE_DB_URL=postgresql+asyncpg://fileclaw:fileclaw_secret@192.168.88.251:54322/laap_kline",
     "KLINE_DB_URL=postgresql+asyncpg://fileclaw:fileclaw_secret@192.168.88.251:54322/watchlist_kline_store"),
    ("PAPER_TRADING_DB_PATH=D:/laap-AGI/data/paper_trading.db",
     "PAPER_TRADING_DB_PATH=data/laap_trading.db"),
]
WATCHLIST_LINE = "WATCHLIST_KLINE_DB_PATH=data/watchlist_kline_store.db"


def md5_norm(data: bytes) -> str:
    return hashlib.md5(data.replace(b"\r\n", b"\n")).hexdigest()


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PASS,
              timeout=15, banner_timeout=15, auth_timeout=15,
              allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    # 1. 备份 NAS 目标文件
    ts = "20260818"
    for rel in FILES + [".env"]:
        try:
            sftp.stat(f"{NAS_ROOT}/{rel}")
            c.exec_command(f"cp {NAS_ROOT}/{rel} {NAS_ROOT}/{rel}.bak_{ts}")
        except FileNotFoundError:
            pass
    # 2. 上传文件（md5 归一化校验）
    ok = 0
    for rel in FILES:
        local = LOCAL_ROOT + "\\" + rel.replace("/", "\\")
        with open(local, "rb") as f:
            data = f.read()
        local_md5 = md5_norm(data)
        # 确保目录存在
        c.exec_command(f"mkdir -p {NAS_ROOT}/{'/'.join(rel.split('/')[:-1])}")
        sftp.putfo(__import__("io").BytesIO(data), f"{NAS_ROOT}/{rel}")
        with sftp.open(f"{NAS_ROOT}/{rel}", "rb") as f:
            remote_md5 = md5_norm(f.read())
        if local_md5 == remote_md5:
            ok += 1
        else:
            print(f"  [MD5 MISMATCH] {rel}")
    # 3. 修正 NAS .env
    with sftp.open(f"{NAS_ROOT}/.env", "rb") as f:
        env = f.read().decode("utf-8", errors="replace")
    env_bak = env
    for old, new in ENV_FIXES:
        if old in env:
            env = env.replace(old, new)
            print(f"  .env 替换: {old[:60]}...")
        else:
            print(f"  [skip] 未找到: {old[:60]}...")
    if WATCHLIST_LINE.split("=")[0] not in env:
        env += "\n" + WATCHLIST_LINE + "\n"
        print(f"  .env 追加: {WATCHLIST_LINE}")
    with sftp.open(f"{NAS_ROOT}/.env", "wb") as f:
        f.write(env.encode("utf-8"))
    sftp.close()
    print(f"上传完成: {ok}/{len(FILES)} 文件")
    c.close()


if __name__ == "__main__":
    main()
