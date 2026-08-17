#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 laap-AGI/data/ 目录 → NAS 同步

内容: laap_trading.db / trading_calendar.json(兼容) / watchlist_kline_store.db
（K线真库在 data/watchlist_kline/ 子目录，2026-08-18 统一；根目录同名文件是空壳勿同步）
"""
import hashlib
import os
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"
LOCAL_ROOT = r"D:\laap-AGI"

FILES = [
    "data/laap_trading.db",
    "data/trading_calendar.json",
    "data/watchlist_kline/watchlist_kline_store.db",
    "data/watchlist_kline/candidates_10d.png",
    "data/watchlist_kline/kline_3y.png",
    "data/watchlist_kline/kline_lastweek.png",
]


def md5_norm(data: bytes) -> str:
    return hashlib.md5(data.replace(b"\r\n", b"\n")).hexdigest()


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PASS,
              timeout=15, banner_timeout=15, auth_timeout=15,
              allow_agent=False, look_for_keys=False)
    _, out, _ = c.exec_command(f"mkdir -p {NAS_ROOT}/data/watchlist_kline")
    print(out.read().decode())

    sftp = c.open_sftp()
    ok = 0
    for rel in FILES:
        local = os.path.join(LOCAL_ROOT, rel.replace("/", os.sep))
        if not os.path.exists(local):
            print(f"  [skip] 本地不存在: {rel}")
            continue
        sftp.put(local, f"{NAS_ROOT}/{rel}")
        ok += 1
    sftp.close()
    print(f"上传完成: {ok} 文件")

    # md5 校验
    sftp = c.open_sftp()
    mismatch = 0
    for rel in FILES:
        local = os.path.join(LOCAL_ROOT, rel.replace("/", os.sep))
        if not os.path.exists(local):
            continue
        with open(local, "rb") as f:
            loc_md5 = md5_norm(f.read())
        try:
            with sftp.open(f"{NAS_ROOT}/{rel}", "rb") as f:
                nas = f.read()
        except Exception:
            mismatch += 1
            print(f"  [MISS] {rel}")
            continue
        if md5_norm(nas) != loc_md5:
            mismatch += 1
            print(f"  [MISMATCH] {rel}")
    sftp.close()
    print(f"校验: {'全部一致 ✅' if mismatch == 0 else f'{mismatch} 不一致 ❌'}")
    c.close()


if __name__ == "__main__":
    main()
