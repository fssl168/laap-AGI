#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查 NAS venv 的 transformers/tokenizers/torch 版本"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
cmd = """cd /vol1/@appdata/trim.hermes/workspace/laap-AGI && ./.venv/bin/python -m pip list 2>/dev/null | grep -iE 'transformers|tokenizers|torch|sentence'"""
_, out, _ = c.exec_command(cmd, timeout=60)
print(out.read().decode().strip() or "(无相关包)")
c.close()
