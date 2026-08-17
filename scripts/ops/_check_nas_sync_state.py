# -*- coding: utf-8 -*-
"""检查 NAS 上 LAAP 相关文件与 .env 状态（对比本地改动点）。"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"

CMDS = [
    # .env 关键路径
    f"grep -E 'WATCHLIST_KLINE_DB_PATH|PAPER_TRADING_DB_PATH|KLINE_DB_BACKEND|PAPER_TRADING_DB_BACKEND' {NAS_ROOT}/.env 2>/dev/null || echo '.env 无匹配'",
    # paper_trading_tools 旧契约还在吗
    f"grep -c 'run_backtest(strategy)' {NAS_ROOT}/aris_brain/paper_trading_tools.py 2>/dev/null || echo '文件不存在'",
    # watchlist_kline_store 默认路径
    f"grep -n 'data.*watchlist_kline_store.db' {NAS_ROOT}/watchlist_kline_store.py 2>/dev/null | head -3",
    # db.py 归一化
    f"grep -c 'lstrip' {NAS_ROOT}/laap/paper_trading/db.py 2>/dev/null || echo '无 lstrip'",
    # conftest KLINE 隔离
    f"grep -c 'KLINE_DB_BACKEND' {NAS_ROOT}/tests/conftest.py 2>/dev/null || echo '无'",
    # 根目录空壳文件是否存在
    f"ls -la {NAS_ROOT}/data/watchlist_kline_store.db 2>/dev/null; ls -la {NAS_ROOT}/data/watchlist_kline/watchlist_kline_store.db 2>/dev/null",
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
        print("  [err]", e[:200])
    print()
c.close()
