#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 NAS 侧 4 个待覆盖文件到本地临时目录, 用于 diff 审查 NAS 独有修改。"""
import os, paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"
OUT = r"D:\laap-AGI\.sync_review_20260816"
os.makedirs(OUT, exist_ok=True)

FILES = [
    "aris_brain/paper_trading_tools.py",
    "aris_brain/rules_defs.py",
    "aris_brain/rules_tools.py",
    "laap_brain/api.py",
]

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
for rel in FILES:
    dst = os.path.join(OUT, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        sftp.get(f"{NAS_ROOT}/{rel}", dst)
        size = os.path.getsize(dst)
        print(f"[OK] {rel} ({size} bytes) -> {dst}")
    except FileNotFoundError:
        print(f"[MISSING] {rel} (NAS 无此文件)")
sftp.close(); c.close()
print("DONE")
