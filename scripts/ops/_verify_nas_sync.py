# -*- coding: utf-8 -*-
"""验证 NAS 同步结果。"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"

CMDS = [
    f"grep -E 'KLINE_DB_URL|WATCHLIST_KLINE_DB_PATH|PAPER_TRADING_DB_PATH' {NAS_ROOT}/.env",
    f"grep -c 'run_backtest(strategy)' {NAS_ROOT}/aris_brain/paper_trading_tools.py",
    f"grep -c 'BacktestRunner' {NAS_ROOT}/aris_brain/paper_trading_tools.py",
    f"grep -c 'watchlist_kline.*watchlist_kline_store.db' {NAS_ROOT}/watchlist_kline_store.py",
    f"grep -c 'lstrip' {NAS_ROOT}/laap/paper_trading/db.py",
    f"grep -c 'KLINE_DB_BACKEND' {NAS_ROOT}/tests/conftest.py",
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
    print(f"$ {cmd.split('2>/dev/null')[0].strip()[:90]}")
    if o:
        print("  ", o)
    if e:
        print("  [err]", e[:150])
c.close()
