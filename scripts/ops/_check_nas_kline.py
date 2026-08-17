#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 NAS 上新文件就位"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
cmd = """cd /vol1/@appdata/trim.hermes/workspace/laap-AGI && echo '=== 新文件 ===' && ls -la scripts/migrate_sqlite_to_pg.py tests/test_kline_store.py watchlist_kline_store.py 2>&1 && echo '=== kline 存储层 PG 后端验证 ===' && KLINE_DB_BACKEND=postgres ./.venv/bin/python -c "
import sys; sys.path.insert(0, '.')
import watchlist_kline_store as ws
print('backend:', ws.backend_name())
rows = ws.get_kline('sh600519', days=3)
print('600519 PG 读取:', [(r[0], r[2]) for r in rows])
" 2>&1 | grep -vE 'tokenizers|TF-IDF'"""
_, out, _ = c.exec_command(cmd, timeout=60)
print(out.read().decode().strip()[-500:])
c.close()
