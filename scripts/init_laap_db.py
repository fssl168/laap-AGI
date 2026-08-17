# -*- coding: utf-8 -*-
"""建立本地 laap.db (SQLite, 排除 paper_trading) + 远程 PG16 laap 库补表。

laap.db 包含(非 paper_trading 的关系表):
  1. daily_kline    (K线, watchlist_kline_store)
  2. stock_names    (股票名称)
  3. meta_sessions  (元认知会话, laap/agi/meta_session_db)

远程 PG16 laap 库: 已存在 paper_trading 11 表, 补缺失的
daily_kline / stock_names / meta_sessions (sector_reports 属 paper_trading, 跳过)。
"""
import os
import sqlite3
import psycopg

LOCAL_DB = r"D:\laap-AGI\data\laap.db"
PG = dict(host="192.168.88.251", port=54322, user="fileclaw",
          password="fileclaw_secret", dbname="laap")

KLINE_SCHEMA = """
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

META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta_sessions (
    id TEXT PRIMARY KEY,
    concept TEXT NOT NULL,
    strategy TEXT NOT NULL,
    domain TEXT NOT NULL,
    duration_minutes REAL,
    mastery_before REAL,
    mastery_after REAL,
    gain REAL,
    successful INTEGER,
    timestamp REAL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_meta_sessions_domain ON meta_sessions(domain);
CREATE INDEX IF NOT EXISTS idx_meta_sessions_ts ON meta_sessions(timestamp);
"""


def init_local():
    """本地 SQLite laap.db(3 表)。"""
    if os.path.exists(LOCAL_DB):
        os.remove(LOCAL_DB)  # 全新重建
    conn = sqlite3.connect(LOCAL_DB)
    conn.executescript(KLINE_SCHEMA)
    conn.executescript(META_SCHEMA)
    conn.commit()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1").fetchall()]
    print(f"本地 {LOCAL_DB}: {tables}")
    conn.close()


def init_pg():
    """远程 PG16 laap 库补表(只补非 paper_trading 的 3 表)。"""
    conn = psycopg.connect(**PG, connect_timeout=5)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(KLINE_SCHEMA)
    cur.execute(META_SCHEMA)
    cur.execute("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY 1")
    tables = [r[0] for r in cur.fetchall()]
    print(f"PG laap 库现有表: {tables}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    init_local()
    init_pg()
    print("完成")
