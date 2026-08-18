# -*- coding: utf-8 -*-
"""验证 NAS 新闻采集修复。"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"

CMDS = [
    # 启动日志：无"数据陈旧"= _freshness_ok 修复生效
    f"grep -a 'NewsSignalWorker started\\|数据陈旧' {NAS_ROOT}/laap-nas.log | tail -3",
    # 代码确认
    f"grep -c 'days=15' {NAS_ROOT}/laap/paper_trading/news_pipeline.py",
    f"grep -c 'time.time()' {NAS_ROOT}/watchlist_kline_store.py",
    f"grep -E 'LLM_SOURCES|LOCAL_LLM_URL' {NAS_ROOT}/.env",
    # health
    f"curl -s http://127.0.0.1:11546/health",
]

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
for cmd in CMDS:
    _, out, err = c.exec_command(cmd, timeout=30)
    o = out.read().decode().strip()
    e = err.read().decode().strip()
    print(f"$ {cmd.split('2>/dev/null')[0].strip()[:80]}")
    if o:
        print(o)
    if e:
        print("[err]", e[:150])
    print()
c.close()
