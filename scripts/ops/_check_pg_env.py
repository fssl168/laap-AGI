#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查 fileclaw-postgres-vector 容器的 PG 用户/密码/库名"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
cmd = (
    "echo 'Qiu@121236' | sudo -S -p '' docker inspect fileclaw-postgres-vector "
    "--format '{{range .Config.Env}}{{println .}}{{end}}' 2>&1 | grep -iE 'POSTGRES|PG'"
)
_, out, _ = c.exec_command(cmd, timeout=30)
print(out.read().decode().strip())
c.close()
