#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 .fuse_* 文件系统残留(每天 cron 跑一次)。"""
import os
import pathlib

ROOT = r"D:\laap-AGI"
CLEANED = 0

for d in ["data", "data/watchlist_kline", "data/watchlist_kline/kline"]:
    if not os.path.isdir(d):
        continue
    for e in os.listdir(d):
        if e.startswith(".fuse_"):
            try:
                os.remove(os.path.join(d, e))
                CLEANED += 1
            except Exception:
                pass

# 根目录 PUA 字符条目
for e in pathlib.Path(ROOT).iterdir():
    name = e.name
    if any(0xf000 <= ord(c) <= 0xf8ff for c in name):
        try:
            if e.is_dir():
                import shutil
                shutil.rmtree(e)
            else:
                os.remove(e)
            CLEANED += 1
        except Exception:
            pass

print(f"[cleanup_fuse] 清理 {CLEANED} 个残留文件")
