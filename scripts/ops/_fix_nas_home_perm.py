#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查并修复 trim.hermes home 日志目录权限，然后重启 gateway"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)

# 检查目录权限
cmds = [
    "ls -ld /vol1/@appdata/trim.hermes/home /vol1/@appdata/trim.hermes/home/.hermes /vol1/@appdata/trim.hermes/home/.hermes/logs 2>&1",
    "ls -ld /vol1/@appdata/trim.hermes/hermes/logs 2>&1",
]
for cmd in cmds:
    _, out, _ = c.exec_command(cmd, timeout=20)
    print(out.read().decode().strip())
    print("---")

# 修复权限（chown 给 trim.hermes）
fix = (
    "echo 'Qiu@121236' | sudo -S -p '' chown -R trim.hermes:trim.hermes "
    "/vol1/@appdata/trim.hermes/home/.hermes 2>&1; "
    "echo 'Qiu@121236' | sudo -S -p '' chmod -R u+rwx "
    "/vol1/@appdata/trim.hermes/home/.hermes 2>&1; "
    "echo FIX_DONE"
)
_, out, _ = c.exec_command(fix, timeout=30)
print(out.read().decode().strip())
c.close()
