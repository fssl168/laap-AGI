# -*- coding: utf-8 -*-
"""备份 laap_trading 全部表到 SQL 文件(COPY 格式), 供清理前留存。"""
import psycopg
import os
import datetime

conn = psycopg.connect(host="192.168.88.251", port=54322,
                       user="fileclaw", password="fileclaw_secret",
                       dbname="laap_trading", connect_timeout=5)
cur = conn.cursor()
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1")
tables = [r[0] for r in cur.fetchall()]
print("表:", tables)

backup_dir = r"D:\laap-AGI\backups"
os.makedirs(backup_dir, exist_ok=True)
path = os.path.join(backup_dir,
                    "laap_trading_pre_cleanup_" +
                    datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".sql")

with open(path, "w", encoding="utf-8") as f:
    f.write("-- laap_trading pre-cleanup backup "
            + datetime.datetime.now().isoformat() + "\n")
    for t in tables:
        cur.execute("SELECT COUNT(*) FROM " + t)
        cnt = cur.fetchone()[0]
        f.write("-- table {}: {} rows\n".format(t, cnt))
        cur.execute("SELECT * FROM " + t)
        cols = [d[0] for d in cur.description]
        f.write("COPY {} ({}) FROM stdin;\n".format(t, ", ".join(cols)))
        for row in cur.fetchall():
            vals = []
            for v in row:
                if v is None:
                    vals.append("\\N")
                elif isinstance(v, (int, float)):
                    vals.append(str(v))
                else:
                    s = str(v).replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")
                    vals.append(s)
            f.write("\t".join(vals) + "\n")
        f.write("\\.\n")

print("备份:", path, os.path.getsize(path), "bytes")
cur.close()
conn.close()
