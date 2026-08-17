#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补装 NAS sentence-transformers（上次超时）"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
cmd = (
    "cd /vol1/@appdata/trim.hermes/workspace/laap-AGI && "
    "./.venv/bin/python -m pip install --timeout 120 --retries 5 sentence-transformers 2>&1 | tail -3"
)
_, out, _ = c.exec_command(cmd, timeout=600)
print(out.read().decode().strip()[-400:])
c.close()
