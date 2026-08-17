#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 NAS LAAP 重启后状态（PG 双库 + 组件）"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
cmd = """grep -iE 'M4|QuantDaily|backend|kline|Error|Traceback|failed' /vol1/@appdata/trim.hermes/workspace/laap-AGI/laap-nas.log 2>&1 | tail -8; echo '=== health ==='; curl -s --max-time 5 http://127.0.0.1:11546/health 2>&1 | head -c 90; echo; echo '=== kline API（应 PG 真实数据）==='; curl -s --max-time 8 'http://127.0.0.1:11546/v1/quant/kline?symbol=600519&days=3' 2>&1 | head -c 150"""
_, out, _ = c.exec_command(cmd, timeout=60)
print(out.read().decode().strip()[-700:])
c.close()
