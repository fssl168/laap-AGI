#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同步 LAAP 修改文件到 NAS (SFTP)。
用法: python sync_laap_to_nas.py
"""
import os
import paramiko

HOST = "192.168.88.251"
PORT = 18922
USER = "wolf"
PASS = "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"
LOCAL_ROOT = r"D:\laap-AGI"

# (本地相对路径, NAS相对路径) — 本次修改的文件
FILES = [
    "aris_brain/longform_synthesizer.py",
    "aris_brain/paper_trading_tools.py",
    "aris_brain/rules_defs.py",
    "aris_brain/rules_engine.py",
    "aris_brain/rules_tools.py",
    "laap/paper_trading/data_sources.py",
    "laap/paper_trading/kline_source.py",
    "laap/paper_trading/memory_bridge.py",
    "laap/paper_trading/news_intel.py",
    "laap/paper_trading/quant_bridge.py",
    "laap/paper_trading/quant_config.py",
    "laap/paper_trading/trading_self.py",
    "laap_brain/api.py",
    "laap_brain/psi_core_integration.py",
    ".env",
]

def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PASS,
              timeout=15, banner_timeout=15, auth_timeout=15,
              allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    ok, fail = [], []
    for rel in FILES:
        local = os.path.join(LOCAL_ROOT, rel)
        # SFTP 路径必须用 / 分隔（os.path.join 在 Windows 生成 \ 会 No such file）
        remote = NAS_ROOT + "/" + rel.replace("\\", "/")
        if not os.path.exists(local):
            fail.append(f"{rel}: 本地不存在")
            continue
        try:
            sftp.put(local, remote)
            ok.append(rel)
        except Exception as e:
            fail.append(f"{rel}: {e}")
    sftp.close()
    c.close()
    print(f"✅ 成功 {len(ok)} 个:")
    for f in ok:
        print(f"  {f}")
    if fail:
        print(f"❌ 失败 {len(fail)} 个:")
        for f in fail:
            print(f"  {f}")
    return 0 if not fail else 1

if __name__ == "__main__":
    raise SystemExit(main())
