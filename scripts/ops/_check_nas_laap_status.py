# -*- coding: utf-8 -*-
"""检查 NAS LAAP 服务状态。"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"

CMDS = [
    f"ps aux | grep 'laap_brain.api' | grep -v grep",
    f"curl -s http://127.0.0.1:11546/health",
]

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
for cmd in CMDS:
    _, out, err = c.exec_command(cmd, timeout=20)
    o = out.read().decode().strip()
    e = err.read().decode().strip()
    print(f"$ {cmd[:80]}")
    if o:
        print(o)
    if e:
        print("[err]", e[:200])
    print()
c.close()
