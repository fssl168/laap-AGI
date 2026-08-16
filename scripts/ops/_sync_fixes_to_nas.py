#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传 4 个修复文件到 NAS: 备份 → 上传 → md5 归一化校验。
覆盖: aris_brain/paper_trading_tools.py(新增), rules_defs.py, rules_tools.py, laap_brain/api.py
"""
import hashlib, os, time, paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"
LOCAL_ROOT = r"D:\laap-AGI"
STAMP = time.strftime("%Y%m%d_%H%M%S")

FILES = [
    "aris_brain/paper_trading_tools.py",
    "aris_brain/rules_defs.py",
    "aris_brain/rules_tools.py",
    "laap_brain/api.py",
]

def md5_norm(data: bytes) -> str:
    return hashlib.md5(data.replace(b"\r\n", b"\n")).hexdigest()

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()

# 1. 备份 NAS 侧已有文件
for rel in FILES:
    remote = f"{NAS_ROOT}/{rel}"
    try:
        sftp.stat(remote)
        bak = f"{remote}.bak_sync_{STAMP}"
        sftp.rename(remote, bak)
        print(f"[BAK] {rel} -> .bak_sync_{STAMP}")
    except FileNotFoundError:
        print(f"[NEW] {rel} (NAS 原无此文件, 跳过备份)")

# 2. 上传 + md5 校验
for rel in FILES:
    local = os.path.join(LOCAL_ROOT, rel.replace("/", os.sep))
    with open(local, "rb") as f:
        data = f.read()
    remote = f"{NAS_ROOT}/{rel}"
    sftp.putfo(__import__("io").BytesIO(data), remote)
    # 回读校验
    with sftp.open(remote, "rb") as rf:
        remote_data = rf.read()
    lmd5, rmd5 = md5_norm(data), md5_norm(remote_data)
    status = "OK" if lmd5 == rmd5 else "MISMATCH!"
    print(f"[{'OK' if lmd5==rmd5 else 'FAIL'}] {rel} local={lmd5[:10]} remote={rmd5[:10]} {status}")

sftp.close(); c.close()
print("DONE")
