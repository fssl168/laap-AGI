#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 scripts/ 目录全量 → NAS 同步（含 market/、ops/ 子目录，排除 __pycache__ 与 .bat）"""
import hashlib
import os
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"
LOCAL_ROOT = r"D:\laap-AGI"

EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_EXT = {".pyc", ".pyo", ".bat"}  # .bat 为 Windows 本地启动脚本，不同步


def collect(root: str) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1]
            if ext in EXCLUDE_EXT:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, LOCAL_ROOT).replace(os.sep, "/")
            out.append(rel)
    return sorted(out)


def md5_norm(data: bytes) -> str:
    return hashlib.md5(data.replace(b"\r\n", b"\n")).hexdigest()


def main():
    files = collect(os.path.join(LOCAL_ROOT, "scripts"))
    print(f"待同步: {len(files)} 个文件")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PASS,
              timeout=15, banner_timeout=15, auth_timeout=15,
              allow_agent=False, look_for_keys=False)
    # 确保子目录存在
    subdirs = {os.path.dirname(rel) for rel in files if "/" in rel}
    for d in subdirs:
        c.exec_command(f"mkdir -p {NAS_ROOT}/{d}")

    sftp = c.open_sftp()
    for rel in files:
        local = os.path.join(LOCAL_ROOT, rel.replace("/", os.sep))
        sftp.put(local, f"{NAS_ROOT}/{rel}")
    sftp.close()
    print(f"上传完成: {len(files)} 文件")

    sftp = c.open_sftp()
    mismatch = 0
    for rel in files:
        local = os.path.join(LOCAL_ROOT, rel.replace("/", os.sep))
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
