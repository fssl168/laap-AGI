#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正确重启 NAS Hermes gateway（设置 trim.hermes 的 HOME）"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)

# 1. 确认旧 gateway 已死
_, out, _ = c.exec_command("ps aux | grep 'hermes_cli.main gateway' | grep -v grep | wc -l", timeout=20)
print("旧 gateway 进程数:", out.read().decode().strip())

# 2. 用正确 HOME 启动（trim.hermes 用户，home=/vol1/@appdata/trim.hermes/home）
cmd = (
    "echo 'Qiu@121236' | sudo -S -p '' -u trim.hermes env "
    "HOME=/vol1/@appdata/trim.hermes/home "
    "HERMES_DATA_DIR=/vol1/@appdata/trim.hermes/hermes "
    "nohup /vol1/@appcenter/trim.hermes/runtime/python/bin/python3.11.real "
    "-m hermes_cli.main gateway run --accept-hooks "
    "> /vol1/@appdata/trim.hermes/hermes/gateway-restart.log 2>&1 & "
    "sleep 12; "
    "ps aux | grep 'hermes_cli.main gateway' | grep -v grep | head -1 | awk '{print $2}'"
)
_, out, err = c.exec_command(cmd, timeout=60)
print("NEW_GW_PID:", out.read().decode().strip())
print("ERR:", err.read().decode()[:200])
c.close()
