#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite → PG16 数据迁移（一次性）

迁移:
  - data/paper_trading.db (13 表) → NAS PG16 laap_trading 库
  - data/watchlist_kline/kline.db (2 表) → NAS PG16 laap_kline 库

安全: 幂等（PG 已存在数据则跳过该表）；SQLite 源文件保留。
用法:
    python scripts/migrate_sqlite_to_pg.py [--kline-only] [--paper-only]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]

PG_HOST = "192.168.88.251"
PG_PORT = 54322
PG_USER = "fileclaw"
PG_PASSWORD = "fileclaw_secret"

PAPER_DB = "laap_trading"
KLINE_DB = "laap_kline"

# paper_trading 缺失的 2 表（PG schema 需补齐）
_EXTRA_PAPER_TABLES = """
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

# kline 库 schema
_KLINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_kline (
    code   TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL NOT NULL,
    close  REAL NOT NULL,
    high   REAL NOT NULL,
    low    REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_kline_code_date ON daily_kline (code, date);
CREATE TABLE IF NOT EXISTS stock_names (
    code    TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    updated TEXT
);
"""


def _pg_conn(db: str):
    return psycopg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER,
        password=PG_PASSWORD, dbname=db, connect_timeout=5)


def _sqlite_tables(sqlite_path: str) -> list:
    conn = sqlite3.connect(sqlite_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    finally:
        conn.close()


def _migrate_table(src: sqlite3.Connection, pg: psycopg.Connection,
                   table: str) -> int:
    """迁移单表。返回迁移行数（已存在则跳过返回 -1）。"""
    cur_src = src.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur_src.description]
    rows = cur_src.fetchall()
    if not rows:
        print(f"  [{table}] 空表，跳过")
        return 0

    # 幂等检查
    cur_pg = pg.cursor()
    try:
        cur_pg.execute(f'SELECT COUNT(*) FROM "{table}"')
        existing = cur_pg.fetchone()[0]
    except Exception:
        existing = 0
    if existing > 0:
        print(f"  [{table}] PG 已有 {existing} 行，跳过（幂等）")
        return -1

    # 批量插入
    placeholders = ", ".join(["%s"] * len(cols))
    col_str = ", ".join(f'"{c}"' for c in cols)
    sql = f'INSERT INTO "{table}" ({col_str}) VALUES ({placeholders})'
    cur_pg.executemany(sql, [tuple(r) for r in rows])
    pg.commit()
    print(f"  [{table}] 迁移 {len(rows)} 行")
    return len(rows)


def migrate_paper(pg_conn) -> None:
    """paper_trading.db → laap_trading。"""
    src_path = ROOT / "data" / "paper_trading.db"
    if not src_path.exists():
        print(f"[paper_trading] 源不存在: {src_path}")
        return
    print(f"[paper_trading] {src_path} → {PAPER_DB}")
    # 补建缺失表
    pg_conn.cursor().execute(_EXTRA_PAPER_TABLES)
    pg_conn.commit()
    src = sqlite3.connect(str(src_path))
    try:
        for table in _sqlite_tables(str(src_path)):
            _migrate_table(src, pg_conn, table)
    finally:
        src.close()


def migrate_kline(pg_conn) -> None:
    """kline.db → laap_kline。"""
    src_path = ROOT / "data" / "watchlist_kline" / "kline.db"
    if not src_path.exists():
        print(f"[kline] 源不存在: {src_path}")
        return
    print(f"[kline] {src_path} → {KLINE_DB}")
    pg_conn.cursor().execute(_KLINE_SCHEMA)
    pg_conn.commit()
    src = sqlite3.connect(str(src_path))
    try:
        for table in _sqlite_tables(str(src_path)):
            _migrate_table(src, pg_conn, table)
    finally:
        src.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kline-only", action="store_true")
    ap.add_argument("--paper-only", action="store_true")
    args = ap.parse_args()

    do_paper = not args.kline_only
    do_kline = not args.paper_only

    if do_paper:
        conn = _pg_conn(PAPER_DB)
        try:
            migrate_paper(conn)
        finally:
            conn.close()
    if do_kline:
        conn = _pg_conn(KLINE_DB)
        try:
            migrate_kline(conn)
        finally:
            conn.close()
    print("\n迁移完成（SQLite 源保留）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
