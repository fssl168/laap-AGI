# -*- coding: utf-8 -*-
"""
自选股/指数 K 线持久化存储层（PG16 优先，SQLite 回退）。

后端:
  - postgres（默认）: NAS fileclaw-postgres-vector PG16 的 laap_kline 库
  - sqlite（回退）:   WATCHLIST_KLINE_DB_PATH（默认 <项目根>/data/watchlist_kline/watchlist_kline_store.db，
                      PG 不可用时自动降级）

表：
  - daily_kline(code, date, open, close, high, low, volume) 主键(code, date)
    code 格式：sh600326 / sz002790 / sh000001(上证指数) 等
  - stock_names(code, name, updated)

能力：
  - upsert_kline：每日落盘（INSERT OR REPLACE）
  - get_kline：取个股/指数最近 N 天日 K
  - get_ma：N 日均线（趋势分析）
  - get_latest：最新交易日概况（日常回顾："昨天自选股怎么样"）

后端配置（环境变量）:
  KLINE_DB_BACKEND=postgres|sqlite（默认 postgres）
  KLINE_DB_URL=postgresql+asyncpg://fileclaw:fileclaw_secret@192.168.88.251:54322/laap_kline
    （DATABASE_URL 未设置时用此；否则回退 DATABASE_URL 的 host/port/user/pass + laap_kline 库）
  WATCHLIST_KLINE_DB_PATH=<本地 SQLite 回退库路径>（留空 = <项目根>/data/watchlist_kline/watchlist_kline_store.db）
"""
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger("watchlist_kline_store")

# K线/名称/日历读取缓存 TTL (秒) (2026-08-17): 高频读取走两级缓存
_KLINE_CACHE_TTL = int(os.environ.get("KLINE_CACHE_TTL", "60"))


def _default_db_path() -> Path:
    """SQLite 回退库路径：WATCHLIST_KLINE_DB_PATH 优先，否则项目根 data/。

    真库位置（2026-08-18 统一）：data/watchlist_kline/watchlist_kline_store.db
    （根目录 data/watchlist_kline_store.db 是 0 字节空壳，已废弃）。

    跨平台守卫（2026-08-18，同 laap.paper_trading.db）：非 Windows 平台忽略
    Windows 盘符绝对路径（如 D:/...），避免项目根下生成 D: 垃圾目录。
    """
    env_path = os.environ.get("WATCHLIST_KLINE_DB_PATH", "")
    if env_path:
        if os.name != "nt":
            try:
                from laap.paper_trading.db import _is_windows_drive_abs
                if _is_windows_drive_abs(env_path):
                    logger.warning(
                        f"WATCHLIST_KLINE_DB_PATH={env_path!r} 是 Windows 盘符路径，"
                        f"非 Windows 平台忽略，回退默认路径"
                    )
                    env_path = ""
            except Exception:
                pass  # 守卫加载失败时按字面路径处理
        if env_path:
            project_root = Path(__file__).resolve().parent
            # 前导斜杠（NAS 风格 /data/...）与相对路径（data/...）统一解析到项目根，
            # 避免 Path('/data/...') 在 Windows 上解析到当前盘符根目录（2026-08-18）
            if env_path.startswith("/") and not env_path.startswith("//"):
                env_path = env_path.lstrip("/")
            p = Path(env_path)
            if not p.is_absolute() and not env_path[1:2] == ":":
                return project_root / p
            return p
    return Path(__file__).resolve().parent / "data" / "watchlist_kline" / "watchlist_kline_store.db"


DB_PATH = _default_db_path()

# 后端选择：KLINE_DB_BACKEND 或继承 DATABASE_URL 存在性
_BACKEND = os.environ.get("KLINE_DB_BACKEND", "postgres")
if _BACKEND == "postgres" and not os.environ.get("DATABASE_URL") \
        and not os.environ.get("KLINE_DB_URL"):
    # 无任何 PG 配置时仍尝试默认（有 db.py 的默认参数可用）
    pass

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

-- 交易日历缓存 (2026-08-17: 替代 data/trading_calendar.json)
CREATE TABLE IF NOT EXISTS trading_calendar (
    key      TEXT PRIMARY KEY,      -- 'dates' | 'synced_at'
    value    TEXT NOT NULL
);
"""

# PG 连接（复用 db.py 的适配层）
_pg_conn_factory = None
_pg_available: bool | None = None


def _get_pg_conn():
    """惰性获取 PG 连接（KLINE_DB_URL 指定库）；失败返回 None（回退 SQLite）。"""
    global _pg_conn_factory, _pg_available
    if _pg_available is False:
        return None
    try:
        from laap.paper_trading.db import _PGConnection, _parse_database_url
        # 连接参数：KLINE_DB_URL > DATABASE_URL（库名随 URL）> 默认
        url = os.environ.get("KLINE_DB_URL") or os.environ.get("DATABASE_URL", "")
        dbname = "watchlist_kline_store"  # 默认 K线库
        if url:
            conf = _parse_database_url(url)
            if conf:
                host, port, user, password = (
                    conf["host"], conf["port"], conf["user"], conf["password"])
                dbname = conf["db"]
            else:
                host, port, user, password = (
                    "192.168.88.251", 54322, "fileclaw", "fileclaw_secret")
        else:
            host, port, user, password = (
                "192.168.88.251", 54322, "fileclaw", "fileclaw_secret")
        import psycopg
        raw = psycopg.connect(
            host=host, port=port, user=user, password=password,
            dbname=dbname, connect_timeout=5)
        _pg_available = True
        return _PGConnection(raw)
    except Exception as e:
        _pg_available = False
        logger.warning(f"kline PG 连接失败，回退 SQLite ({e})")
        return None


def _connect():
    """返回后端连接（PG 包装或 sqlite3，接口兼容：execute/executescript/commit/close/fetchone）。"""
    if _BACKEND == "postgres":
        conn = _get_pg_conn()
        if conn is not None:
            return conn
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def backend_name() -> str:
    """当前后端名称（诊断用）。"""
    if _BACKEND == "postgres" and _pg_available is not False:
        from laap.paper_trading.db import _parse_database_url
        url = os.environ.get("KLINE_DB_URL") or os.environ.get("DATABASE_URL", "")
        conf = _parse_database_url(url) if url else None
        dbname = conf["db"] if conf else "watchlist_kline_store"
        return f"postgres({dbname})"
    return "sqlite"


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
        # 写后失效缓存 (2026-08-17)
        try:
            from laap.paper_trading.cache_backend import cache_clear_prefix
            codes = {r[0] for r in rows}
            for code in codes:
                cache_clear_prefix(f"kline:{code}:")
        except Exception:
            pass
        return len(rows)
    finally:
        conn.close()


def get_kline(code: str, days: int = 30) -> list:
    """取个股/指数最近 N 天日 K（按日期升序）。

    2026-08-17: 两级缓存 (redis → 内存 TTL, 默认 60s) —— K线是最高频读取
    (每日 tick 34 只 × 多次), 缓存避免重复查库。
    """
    from laap.paper_trading.cache_backend import cache_get, cache_set
    ck = f"kline:{code}:{days}"
    cached = cache_get(ck)
    if cached is not None:
        return cached
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT date, open, close, high, low, volume FROM daily_kline "
            "WHERE code = ? ORDER BY date DESC LIMIT ?",
            (code, days),
        )
        rows = cur.fetchall()
        result = list(reversed(rows))  # 升序
        cache_set(ck, result, ttl=_KLINE_CACHE_TTL)
        return result
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
                "names": names, "db_path": str(DB_PATH),
                "backend": backend_name()}
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
    """读取 代码→名称 映射；codes 为 None 时返回全部。

    2026-08-17: 两级缓存 (redis → 内存 TTL)。
    """
    from laap.paper_trading.cache_backend import cache_get, cache_set
    ck = "kline:names:all" if not codes else "kline:names:" + ",".join(codes)
    cached = cache_get(ck)
    if cached is not None:
        return cached
    init_db()
    conn = _connect()
    try:
        if codes:
            ph = ",".join("?" for _ in codes)
            cur = conn.execute(f"SELECT code, name FROM stock_names WHERE code IN ({ph})", codes)
        else:
            cur = conn.execute("SELECT code, name FROM stock_names")
        result = {c: n for c, n in cur.fetchall()}
        cache_set(ck, result, ttl=_KLINE_CACHE_TTL)
        return result
    finally:
        conn.close()


# ── 交易日历缓存 (2026-08-17: 替代 data/trading_calendar.json) ──

def save_trading_calendar(dates: set, synced_at: str = "") -> None:
    """保存交易日历到 trading_calendar 表。

    key='dates'     → JSON 数组字符串
    key='synced_at' → 同步时间戳
    """
    import json
    init_db()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO trading_calendar (key, value) VALUES (?, ?)",
            ("dates", json.dumps(sorted(dates), ensure_ascii=False)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO trading_calendar (key, value) VALUES (?, ?)",
            ("synced_at", synced_at or ""),
        )
        conn.commit()
        # 写后失效缓存 (2026-08-17)
        try:
            from laap.paper_trading.cache_backend import cache_clear_prefix
            cache_clear_prefix("kline:trading_calendar")
        except Exception:
            pass
    finally:
        conn.close()


def load_trading_calendar() -> tuple:
    """读取交易日历缓存。返回 (dates_set, synced_at)；无数据返回 (None, '')。

    2026-08-17: 两级缓存 (redis → 内存 TTL) 加速高频读取。
    """
    import json
    from laap.paper_trading.cache_backend import cache_get, cache_set
    ck = "kline:trading_calendar"
    cached = cache_get(ck)
    if cached is not None:
        try:
            return set(cached.get("dates", [])), cached.get("synced_at", "")
        except Exception:
            pass
    init_db()
    conn = _connect()
    try:
        rows = dict(conn.execute("SELECT key, value FROM trading_calendar").fetchall())
    finally:
        conn.close()
    if "dates" not in rows:
        return None, ""
    try:
        dates = set(json.loads(rows["dates"]))
    except Exception:
        return None, ""
    cache_set(ck, {"dates": sorted(dates), "synced_at": rows.get("synced_at", "")},
              ttl=_KLINE_CACHE_TTL)
    return dates, rows.get("synced_at", "")
