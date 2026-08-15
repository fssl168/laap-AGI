#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查 gateway 主日志中 MCP/QQ/laap 状态"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
cmds = [
    "ls -t /vol1/@appdata/trim.hermes/hermes/logs/gateway*.log 2>/dev/null | head -3",
    "grep -iE 'laap_brain|QQBot|WebSocket|MCP.*connect|model.*laap' /vol1/@appdata/trim.hermes/hermes/logs/gateway.log 2>/dev/null | tail -12",
    "cat /vol1/@appdata/trim.hermes/hermes/gateway_state.json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"gateway:\", d.get(\"gateway_state\")); print(\"platforms:\", {k: v.get(\"state\") for k, v in d.get(\"platforms\", {}).items()})'",
]
for cmd in cmds:
    _, out, _ = c.exec_command(cmd, timeout=30)
    print(f"$ {cmd[:50]}...")
    print(out.read().decode().strip()[:1000])
    print("---")
c.close()
