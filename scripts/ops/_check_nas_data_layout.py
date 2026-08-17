# -*- coding: utf-8 -*-
"""查看 NAS data/ 目录布局与 .env 全文关键项。"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"

CMDS = [
    f"ls -la {NAS_ROOT}/data/ 2>/dev/null | head -30",
    f"ls -la {NAS_ROOT}/data/watchlist_kline/ 2>/dev/null | head -10",
    f"grep -E 'DATABASE_URL|KLINE_DB_URL|WATCHLIST|PAPER_TRADING_DB_PATH|PAPER_TRADING_DB_BACKEND|KLINE_DB_BACKEND' {NAS_ROOT}/.env 2>/dev/null",
    f"ls -la {NAS_ROOT}/data/laap_trading.db {NAS_ROOT}/data/paper_trading.db 2>/dev/null",
]

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
for cmd in CMDS:
    _, out, err = c.exec_command(cmd, timeout=20)
    o = out.read().decode().strip()
    e = err.read().decode().strip()
    print(f"$ {cmd.split('2>/dev/null')[0].strip()[:100]}")
    if o:
        print(o)
    if e:
        print("  [err]", e[:200])
    print()
c.close()
