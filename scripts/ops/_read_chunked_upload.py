#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取 chunked_upload.py 的 completing 部分"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
# completing 相关代码 + 前后文
cmd = """grep -n -B3 -A25 'completing' /vol1/@appcenter/trim.hermes/runtime/python/lib/python3.11/site-packages/gateway/platforms/qqbot/chunked_upload.py 2>/dev/null | head -60"""
_, out, _ = c.exec_command(cmd, timeout=30)
print(out.read().decode().strip()[:3500])
c.close()
