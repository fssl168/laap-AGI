#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查 chunked_upload completing 步骤的完成/失败日志"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
cmds = [
    # completing 之后的所有 chunked_upload 相关
    "grep -iE 'chunked_upload|complet|upload.*(success|done|finish|fail|error)|complete' /vol1/@appdata/trim.hermes/hermes/logs/gateway.log 2>/dev/null | tail -20",
    # 00:39:52 之后 2 分钟内全部日志
    "awk '/2026-08-16 00:39:52/,/2026-08-16 00:41:5/' /vol1/@appdata/trim.hermes/hermes/logs/gateway.log 2>/dev/null | tail -20",
    # 00:48:25 之后
    "awk '/2026-08-16 00:48:25/,/2026-08-16 00:49:5/' /vol1/@appdata/trim.hermes/hermes/logs/gateway.log 2>/dev/null | tail -15",
]
for cmd in cmds:
    _, out, _ = c.exec_command(cmd, timeout=30)
    print(f"$ {cmd[:45]}...")
    print(out.read().decode().strip()[:2000])
    print("---")
c.close()
