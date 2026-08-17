#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查 NAS 上 redis 可用性"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
cmds = [
    "which redis-cli redis-server 2>/dev/null; ss -tlnp 2>/dev/null | grep 6379",
    "docker ps 2>/dev/null | grep -i redis; ls /vol1/@appdata 2>/dev/null | grep -i redis",
]
for cmd in cmds:
    _, out, _ = c.exec_command(cmd, timeout=20)
    print(f"$ {cmd[:40]}")
    print(out.read().decode().strip() or "(未发现)")
    print("---")
c.close()
