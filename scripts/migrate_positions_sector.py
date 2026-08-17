#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补迁移: positions + sector_reports → PG laap_trading

本地 laap_trading.db 的这两表（PG schema 缺失/数据未迁移）补到 PG。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
PG = dict(host="192.168.88.251", port=54322, user="fileclaw",
          password="fileclaw_secret", dbname="laap_trading")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    qty INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0.0,
    entry_ts REAL NOT NULL DEFAULT 0,
    last_update_ts REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sector_reports (
    report_hash TEXT PRIMARY KEY,
    sector TEXT NOT NULL,
    content TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    char_count INTEGER NOT NULL DEFAULT 0,
    created_ts REAL NOT NULL DEFAULT 0
);
"""


def main() -> int:
    src = sqlite3.connect(str(ROOT / "data" / "laap_trading.db"))
    conn = psycopg.connect(**PG, connect_timeout=5)
    try:
        conn.cursor().execute(_SCHEMA)
        conn.commit()
        for table in ("positions", "sector_reports"):
            cur_src = src.execute(f"SELECT * FROM {table}")
            cols = [d[0] for d in cur_src.description]
            rows = cur_src.fetchall()
            if not rows:
                print(f"[{table}] 源空，跳过")
                continue
            cur_pg = conn.cursor()
            cur_pg.execute(f'SELECT COUNT(*) FROM "{table}"')
            existing = cur_pg.fetchone()[0]
            if existing > 0:
                print(f"[{table}] PG 已有 {existing} 行，跳过")
                continue
            ph = ", ".join(["%s"] * len(cols))
            col_str = ", ".join(f'"{c}"' for c in cols)
            cur_pg.executemany(
                f'INSERT INTO "{table}" ({col_str}) VALUES ({ph})',
                [tuple(r) for r in rows])
            conn.commit()
            print(f"[{table}] 迁移 {len(rows)} 行")
    finally:
        conn.close()
        src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
