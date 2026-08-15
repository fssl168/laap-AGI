#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 agent 自己起的 gateway 实例（避免与用户重启冲突）"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
# 杀掉 3351715(sudo) 和 3351717(gateway) —— 我自己起的
cmd = (
    "echo 'Qiu@121236' | sudo -S -p '' kill 3351715 3351717 2>/dev/null; "
    "sleep 3; "
    "ps aux | grep 'hermes_cli.main gateway' | grep -v grep | head -2 | awk '{print $2, $11, $12, $13}'"
)
_, out, _ = c.exec_command(cmd, timeout=30)
print("剩余 gateway 进程:")
print(out.read().decode().strip() or "(无)")
c.close()
