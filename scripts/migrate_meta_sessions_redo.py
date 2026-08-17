# -*- coding: utf-8 -*-
"""重新迁移 agi_state/meta_sessions.db → PG laap 库 + data/laap.db

背景 (2026-08-18): 旧迁移字段残缺 (ts=0/domain空) 且丢 11 条。
本脚本以 agi_state 的 111 条完整数据为源, UPSERT 覆盖 PG 残缺行 + 插入缺失行,
并同步修复本地 laap.db。完成后验证。

用法: PYTHONPATH= ./.venv/Scripts/python.exe scripts/migrate_meta_sessions_redo.py
"""
import sys
import sqlite3
from pathlib import Path

ROOT = Path(r"D:\laap-AGI")
SRC = ROOT / "agi_state" / "meta_sessions.db"
LOCAL = ROOT / "data" / "laap.db"

COLS = ["id", "concept", "strategy", "domain", "duration_minutes",
        "mastery_before", "mastery_after", "gain", "successful",
        "timestamp", "notes"]

def read_source() -> list:
    conn = sqlite3.connect(str(SRC))
    rows = conn.execute(
        f"SELECT {', '.join(COLS)} FROM meta_sessions").fetchall()
    conn.close()
    return rows

def upsert_pg(rows: list) -> tuple:
    import psycopg
    c = psycopg.connect(host="192.168.88.251", port=54322,
                        user="fileclaw", password="fileclaw_secret",
                        dbname="laap", connect_timeout=8)
    cur = c.cursor()
    # PG 现有 id 集合
    cur.execute("SELECT id FROM meta_sessions")
    existing = {r[0] for r in cur.fetchall()}
    inserted = 0
    updated = 0
    for row in rows:
        rid = row[0]
        cur.execute(f"""
            INSERT INTO meta_sessions ({', '.join(COLS)})
            VALUES ({', '.join(['%s'] * len(COLS))})
            ON CONFLICT (id) DO UPDATE SET
              concept=EXCLUDED.concept,
              strategy=EXCLUDED.strategy,
              domain=EXCLUDED.domain,
              duration_minutes=EXCLUDED.duration_minutes,
              mastery_before=EXCLUDED.mastery_before,
              mastery_after=EXCLUDED.mastery_after,
              gain=EXCLUDED.gain,
              successful=EXCLUDED.successful,
              timestamp=EXCLUDED.timestamp,
              notes=EXCLUDED.notes
        """, tuple(row))
        if rid in existing:
            updated += 1
        else:
            inserted += 1
    c.commit()
    # 验证
    cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE timestamp > 0), "
                "COUNT(*) FILTER (WHERE domain != '') FROM meta_sessions")
    n, n_ts, n_dom = cur.fetchone()
    c.close()
    return inserted, updated, (n, n_ts, n_dom)

def upsert_local(rows: list) -> tuple:
    conn = sqlite3.connect(str(LOCAL))
    cur = conn.cursor()
    existing = {r[0] for r in cur.execute("SELECT id FROM meta_sessions")}
    inserted = updated = 0
    for row in rows:
        rid = row[0]
        if rid in existing:
            cur.execute(f"DELETE FROM meta_sessions WHERE id = ?", (rid,))
            updated += 1
        else:
            inserted += 1
        cur.execute(
            f"INSERT INTO meta_sessions ({', '.join(COLS)}) "
            f"VALUES ({', '.join(['?'] * len(COLS))})", tuple(row))
    conn.commit()
    n = cur.execute("SELECT COUNT(*) FROM meta_sessions").fetchone()[0]
    n_ts = cur.execute("SELECT COUNT(*) FROM meta_sessions WHERE timestamp > 0").fetchone()[0]
    n_dom = cur.execute("SELECT COUNT(*) FROM meta_sessions WHERE domain != ''").fetchone()[0]
    conn.close()
    return inserted, updated, (n, n_ts, n_dom)

if __name__ == "__main__":
    rows = read_source()
    print(f"源数据: {len(rows)} 条 (agi_state/meta_sessions.db)")
    ins, upd, (n, n_ts, n_dom) = upsert_pg(rows)
    print(f"PG laap.meta_sessions: 插入 {ins}, 覆盖更新 {upd}, "
          f"总行数 {n} (有ts {n_ts}, 有domain {n_dom})")
    ins2, upd2, (n2, n2_ts, n2_dom) = upsert_local(rows)
    print(f"本地 data/laap.db: 插入 {ins2}, 重建 {upd2}, "
          f"总行数 {n2} (有ts {n2_ts}, 有domain {n2_dom})")
