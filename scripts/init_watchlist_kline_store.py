# -*- coding: utf-8 -*-
"""方案B: 新建 watchlist_kline_store 库(三表) + 本地 kline.db + 清理旧库。

1. NAS PG16: CREATE DATABASE watchlist_kline_store
   → daily_kline / stock_names / sector_reports 三表
   → 数据从 laap_kline(daily_kline/stock_names) + laap_trading(sector_reports) 迁入
2. 本地 data/watchlist_kline_store.db: 同三表(SQLite)
3. laap_trading: DROP TABLE sector_reports (数据已迁)
4. laap 库: DROP TABLE daily_kline / stock_names (只留 meta_sessions)
5. laap_kline: 保留不动(兼容旧引用)
"""
import os
import sqlite3
import psycopg

PG_ADMIN = dict(host="192.168.88.251", port=54322, user="fileclaw",
                password="fileclaw_secret", dbname="postgres")
PG_NEW = dict(host="192.168.88.251", port=54322, user="fileclaw",
              password="fileclaw_secret", dbname="watchlist_kline_store")
LOCAL_KLINE = r"D:\laap-AGI\data\kline.db"

SCHEMA = """
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
CREATE TABLE IF NOT EXISTS sector_reports (
    report_hash TEXT PRIMARY KEY,
    sector TEXT NOT NULL,
    content TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    char_count INTEGER NOT NULL DEFAULT 0,
    created_ts REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sector_reports_sector ON sector_reports(sector);
CREATE INDEX IF NOT EXISTS idx_sector_reports_created ON sector_reports(created_ts);
"""


def create_pg_db():
    conn = psycopg.connect(**PG_ADMIN, connect_timeout=5)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname='watchlist_kline_store'")
    if cur.fetchone():
        print("watchlist_kline_store 库已存在")
    else:
        cur.execute("CREATE DATABASE watchlist_kline_store")
        print("已创建 watchlist_kline_store 库")
    cur.close()
    conn.close()


def _pg_tables(dbname):
    conn = psycopg.connect(host="192.168.88.251", port=54322, user="fileclaw",
                           password="fileclaw_secret", dbname=dbname, connect_timeout=5)
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY 1")
    tables = [r[0] for r in cur.fetchall()]
    conn.close()
    return tables


def _pg_copy(db_src, table):
    """跨库复制: 源库读全部行 → 新库插入。"""
    src = psycopg.connect(host="192.168.88.251", port=54322, user="fileclaw",
                          password="fileclaw_secret", dbname=db_src, connect_timeout=5)
    dst = psycopg.connect(**PG_NEW, connect_timeout=5)
    dst.autocommit = True
    sc = src.cursor()
    dc = dst.cursor()
    sc.execute("SELECT * FROM " + table)
    cols = [d.name for d in sc.description]
    rows = sc.fetchall()
    if rows:
        ph = ",".join(["%s"] * len(cols))
        for r in rows:
            dc.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph}) ON CONFLICT DO NOTHING", r)
    print(f"  {db_src}.{table}: 迁移 {len(rows)} 行")
    sc.close(); dc.close(); src.close(); dst.close()


def init_pg_new():
    conn = psycopg.connect(**PG_NEW, connect_timeout=5)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(SCHEMA)
    conn.commit()
    tables = [r[0] for r in cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1").fetchall()]
    print(f"watchlist_kline_store 表: {tables}")
    cur.close()
    conn.close()
    # 迁移数据
    for t in ["daily_kline", "stock_names"]:
        if t in _pg_tables("laap_kline"):
            _pg_copy("laap_kline", t)
    if "sector_reports" in _pg_tables("laap_trading"):
        _pg_copy("laap_trading", "sector_reports")


def init_local_kline():
    if os.path.exists(LOCAL_KLINE):
        os.remove(LOCAL_KLINE)
    conn = sqlite3.connect(LOCAL_KLINE)
    conn.executescript(SCHEMA)
    conn.commit()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1").fetchall()]
    print(f"本地 {LOCAL_KLINE}: {tables}")
    conn.close()


def cleanup():
    # laap_trading: 删 sector_reports
    if "sector_reports" in _pg_tables("laap_trading"):
        conn = psycopg.connect(host="192.168.88.251", port=54322, user="fileclaw",
                               password="fileclaw_secret", dbname="laap_trading", connect_timeout=5)
        conn.autocommit = True
        conn.cursor().execute("DROP TABLE sector_reports")
        conn.close()
        print("laap_trading: 已删 sector_reports")
    else:
        print("laap_trading: 无 sector_reports")
    # laap 库: 删 kline 两表(留 meta_sessions)
    conn = psycopg.connect(host="192.168.88.251", port=54322, user="fileclaw",
                           password="fileclaw_secret", dbname="laap", connect_timeout=5)
    conn.autocommit = True
    cur = conn.cursor()
    for t in ["daily_kline", "stock_names"]:
        if t in _pg_tables("laap"):
            cur.execute(f"DROP TABLE {t}")
            print(f"laap 库: 已删 {t}")
    conn.close()
    print("laap 库剩余:", _pg_tables("laap"))


if __name__ == "__main__":
    create_pg_db()
    init_pg_new()
    init_local_kline()
    cleanup()
    print("完成")
