# -*- coding: utf-8 -*-
"""重启 NAS 上的 LAAP 服务（5 环境变量 + sudo）。"""
import paramiko
import time

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"

SCRIPT = f"""export LAAP_TRSI_ENABLED=1 LAAP_EVO_ENABLED=1 LAAP_EVO_INTERVAL=3600 \\
  LAAP_QUANT_DAILY=1 LAAP_QUANT_DAILY_INTERVAL=86400
# 杀旧进程
pkill -f "laap_brain.api" 2>/dev/null; sleep 2
# 启动
cd {NAS_ROOT}
nohup ./.venv/bin/python -m laap_brain.api --port 11546 > /tmp/laap_restart.log 2>&1 &
# 等 health
for i in $(seq 1 30); do
  if curl -s http://127.0.0.1:11546/health | grep -q '"status": "ok"'; then
    echo "HEALTH_OK after ${{i}}s"; exit 0
  fi
  sleep 1
done
echo "HEALTH_TIMEOUT"; tail -20 /tmp/laap_restart.log; exit 1
"""

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
# 需要 sudo（进程属 trim.hermes）
cmd = f"echo '{PASS}' | sudo -S bash -c '{SCRIPT}' 2>&1"
_, out, err = c.exec_command(cmd, timeout=90)
o = out.read().decode()
e = err.read().decode()
print(o[-1500:])
if e:
    print("[err]", e[-300:])
c.close()
