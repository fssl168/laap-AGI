# -*- coding: utf-8 -*-
"""
自选股/指数 K 线持久化存储层（SQLite）。

数据库：D:\\laap-AGI\\data\\watchlist_kline\\kline.db
表：
  - daily_kline(code, date, open, close, high, low, volume) 主键(code, date)
    code 格式：sh600326 / sz002790 / sh000001(上证指数) 等

能力：
  - upsert_kline：每日落盘（INSERT OR REPLACE）
  - get_kline：取个股/指数最近 N 天日 K
  - get_ma：N 日均线（趋势分析）
  - get_latest：最新交易日概况（日常回顾："昨天自选股怎么样"）
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(r"D:\laap-AGI\data\watchlist_kline\kline.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_kline (
    code   TEXT NOT NULL,
    date   TEXT NOT NULL,      -- YYYY-MM-DD
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


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def upsert_kline(rows) -> int:
    """批量落盘：rows = [(code, date, open, close, high, low, volume), ...]。"""
    if not rows:
        return 0
    init_db()
    conn = _connect()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_kline (code, date, open, close, high, low, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_kline(code: str, days: int = 30) -> list:
    """取个股/指数最近 N 天日 K（按日期升序）。"""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT date, open, close, high, low, volume FROM daily_kline "
            "WHERE code = ? ORDER BY date DESC LIMIT ?",
            (code, days),
        )
        rows = cur.fetchall()
        return list(reversed(rows))  # 升序
    finally:
        conn.close()


def get_ma(code: str, days: int = 30, window: int = 5) -> list:
    """N 日均线序列（趋势分析）：[(date, close, ma_window), ...]，按日期升序。"""
    kline = get_kline(code, days=days)
    out = []
    closes = [r[2] for r in kline]
    for i, (date, _, close, _, _, _) in enumerate(kline):
        if i + 1 >= window:
            ma = sum(closes[i + 1 - window: i + 1]) / window
            out.append((date, close, round(ma, 2)))
    return out


def get_latest_day() -> str:
    """数据库中最新的交易日。"""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute("SELECT MAX(date) FROM daily_kline")
        row = cur.fetchone()
        return row[0] if row and row[0] else ""
    finally:
        conn.close()


def get_trading_days(limit: int = 5) -> list:
    """最近 N 个交易日（降序，最新在前）。"""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT DISTINCT date FROM daily_kline ORDER BY date DESC LIMIT ?", (limit,))
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def get_day_overview(date: str = "", codes: list = None) -> dict:
    """某交易日的概况（日常回顾）：{code: {close, pct, open, high, low, volume}}。

    pct 基于前一交易日收盘计算；codes 为 None 时取当日全部记录。
    """
    init_db()
    conn = _connect()
    try:
        if not date:
            date = get_latest_day()
        if not date:
            return {"date": "", "items": {}}
        items = {}
        if codes:
            ph = ",".join("?" for _ in codes)
            cur = conn.execute(
                f"SELECT code, date, open, close, high, low, volume FROM daily_kline "
                f"WHERE date = ? AND code IN ({ph})", (date, *codes))
        else:
            cur = conn.execute(
                "SELECT code, date, open, close, high, low, volume FROM daily_kline WHERE date = ?",
                (date,))
        rows = cur.fetchall()
        prev = {}
        for code, _, _, _, _, _, _ in rows:
            pc = conn.execute(
                "SELECT close FROM daily_kline WHERE code = ? AND date < ? ORDER BY date DESC LIMIT 1",
                (code, date)).fetchone()
            prev[code] = pc[0] if pc else None
        for code, d, o, c, h, l, v in rows:
            pct = round((c - prev[code]) / prev[code] * 100.0, 2) if prev.get(code) else None
            items[code] = {"date": d, "open": o, "close": c, "high": h, "low": l,
                           "volume": v, "pct": pct}
        return {"date": date, "items": items}
    finally:
        conn.close()


def db_stats() -> dict:
    """存储层统计信息。"""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute("SELECT COUNT(*), COUNT(DISTINCT code), COUNT(DISTINCT date) FROM daily_kline")
        total, codes, days = cur.fetchone()
        names = conn.execute("SELECT COUNT(*) FROM stock_names").fetchone()[0]
        return {"total_rows": total, "codes": codes, "days": days,
                "names": names, "db_path": str(DB_PATH)}
    finally:
        conn.close()


# ── 股票名称映射 ─────────────────────────────────────────────

def upsert_stock_names(names: dict) -> int:
    """保存 代码→名称 映射：{code: name}。"""
    if not names:
        return 0
    init_db()
    conn = _connect()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO stock_names (code, name, updated) VALUES (?, ?, datetime('now'))",
            [(c, n) for c, n in names.items()],
        )
        conn.commit()
        return len(names)
    finally:
        conn.close()


def get_stock_names(codes: list = None) -> dict:
    """读取 代码→名称 映射；codes 为 None 时返回全部。"""
    init_db()
    conn = _connect()
    try:
        if codes:
            ph = ",".join("?" for _ in codes)
            cur = conn.execute(f"SELECT code, name FROM stock_names WHERE code IN ({ph})", codes)
        else:
            cur = conn.execute("SELECT code, name FROM stock_names")
        return {c: n for c, n in cur.fetchall()}
    finally:
        conn.close()
