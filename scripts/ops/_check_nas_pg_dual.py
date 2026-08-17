#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 NAS 上双库 PG 就绪（laap_trading + laap_kline）"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
cmd = """cd /vol1/@appdata/trim.hermes/workspace/laap-AGI && KLINE_DB_BACKEND=postgres ./.venv/bin/python -c "
import sys; sys.path.insert(0, '.')
import watchlist_kline_store as ws
print('kline backend:', ws.backend_name())
print('kline stats:', {k: ws.db_stats()[k] for k in ('total_rows','codes','backend')})
from laap.paper_trading.db import PaperDB
db = PaperDB()
print('paper backend:', db.backend)
conn = db.conn()
n = conn.execute('SELECT COUNT(*) FROM news_items').fetchone()[0]
print('news_items in PG:', n)
conn.close()
" 2>&1 | grep -vE 'tokenizers|TF-IDF'"""
_, out, _ = c.exec_command(cmd, timeout=60)
print(out.read().decode().strip()[-500:])
c.close()
