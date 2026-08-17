#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 NAS venv 装 psycopg（PG16 客户端）"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
cmd = (
    "cd /vol1/@appdata/trim.hermes/workspace/laap-AGI && "
    "./.venv/bin/python -m pip install 'psycopg[binary]' 2>&1 | tail -2"
)
_, out, _ = c.exec_command(cmd, timeout=180)
print(out.read().decode().strip())
c.close()
