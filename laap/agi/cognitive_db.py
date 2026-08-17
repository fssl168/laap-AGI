# -*- coding: utf-8 -*-
"""认知引擎关系库存储层 (2026-08-17)。

三模块 JSON → DB 迁移的统一读写层:
  - rsi_engine:      rsi_params / rsi_attempts / rsi_goals
  - evolution_audit: evolution_audit
  - meta_learning:   meta_sessions(已有) / strategy_efficacy /
                     knowledge_transfers / meta_learning_meta

后端: PG16 优先(laap 库), SQLite 回退(data/laap.db) —— 与 paper_trading 一致。
调用方 save/load 逻辑不变, 只换存储后端; JSON 保留为兼容回退。

用法:
  from laap.agi.cognitive_db import get_db, upsert, fetch_all, truncate
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("laap.agi.cognitive_db")

# 默认路径
_DEFAULT_SQLITE = os.environ.get(
    "COGNITIVE_DB_PATH",
    str(Path(__file__).resolve().parents[2] / "data" / "laap.db"))
_DEFAULT_PG_DB = "laap"
_PG_HOST = os.environ.get("PAPER_TRADING_PG_HOST", "192.168.88.251")
_PG_PORT = int(os.environ.get("PAPER_TRADING_PG_PORT", "54322"))
_PG_USER = os.environ.get("PAPER_TRADING_PG_USER", "fileclaw")
_PG_PASSWORD = os.environ.get("PAPER_TRADING_PG_PASSWORD", "fileclaw_secret")

# 后端选择: COGNITIVE_DB_BACKEND=postgres|sqlite (默认 postgres, 同 paper_trading)
_BACKEND = os.environ.get("COGNITIVE_DB_BACKEND", "postgres").lower()

# 读取缓存 TTL (秒) (2026-08-17): 认知引擎读取走两级缓存 redis→内存
_READ_TTL = int(os.environ.get("COGNITIVE_CACHE_TTL", "30"))

# identity 列的表(PG 插入需 OVERRIDING SYSTEM VALUE)
_IDENTITY_TABLES = {"evolution_audit", "knowledge_transfers"}

# 表 → 主键(用于 upsert 的 ON CONFLICT)
_PK = {
    "rsi_params": "name",
    "rsi_attempts": "id",
    "rsi_goals": "id",
    "meta_sessions": "id",
    "meta_learning_meta": "key",
    "evolution_audit": None,          # autoincrement, 无自然主键 → 追加
    "strategy_efficacy": "strategy,domain",   # 唯一键 (strategy, domain)
    "knowledge_transfers": None,      # autoincrement
}


class _PgConn:
    """psycopg 连接的 sqlite3 风格包装(最小子集)。"""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params: Any = None):
        cur = self._raw.cursor()
        if params:
            cur.execute(sql, list(params))
        else:
            cur.execute(sql)
        return _PgCur(cur)

    def executemany(self, sql: str, seq: List[tuple]):
        cur = self._raw.cursor()
        cur.executemany(sql, [list(p) for p in seq])
        return _PgCur(cur)

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


class _PgCur:
    def __init__(self, cur):
        self._cur = cur
        self.description = getattr(cur, "description", None)

    def fetchall(self):
        rows = self._cur.fetchall()
        cols = [d.name for d in (self.description or [])]
        if cols:
            return [dict(zip(cols, r)) for r in rows]
        return rows

    def fetchone(self):
        return self._cur.fetchone()


def _connect_pg():
    """PG 连接(laap 库); 失败返回 None。"""
    try:
        import psycopg
        conn = psycopg.connect(
            host=_PG_HOST, port=_PG_PORT, user=_PG_USER,
            password=_PG_PASSWORD, dbname=_DEFAULT_PG_DB, connect_timeout=5)
        return _PgConn(conn)
    except Exception as e:
        logger.warning(f"cognitive_db: PG 连接失败, 回退 SQLite: {e}")
        return None


def _connect():
    """后端连接: PG 优先, SQLite 回退。"""
    if _BACKEND == "postgres":
        conn = _connect_pg()
        if conn is not None:
            return conn
    conn = sqlite3.connect(_DEFAULT_SQLITE)
    conn.row_factory = sqlite3.Row
    return conn


def _adapt_sql(sql: str) -> str:
    """SQLite `?` 占位符 → PG `%s`(无参数时不转换)。"""
    if "?" not in sql:
        return sql
    out, i = [], 0
    for ch in sql:
        if ch == "?":
            out.append(f"%s")
        else:
            out.append(ch)
    return "".join(out)


def _is_pg(conn) -> bool:
    return isinstance(conn, _PgConn)


def upsert(table: str, row: Dict[str, Any]) -> None:
    """插入或更新一行。row: {col: value}。list/dict 值自动 JSON 序列化。"""
    conn = _connect()
    try:
        cols = list(row.keys())
        vals = []
        for c in cols:
            v = row[c]
            if isinstance(v, (list, dict, tuple)):
                v = json.dumps(v, ensure_ascii=False)
            vals.append(v)
        ph = ",".join(["?"] * len(cols))
        sql = "INSERT INTO {} ({}) VALUES ({})".format(table, ",".join(cols), ph)
        pk = _PK.get(table)
        if pk and all(c in row for c in pk.split(",")):
            conflict = "({})".format(pk)
            up = ",".join(f"{c}=?" for c in cols if c not in pk.split(","))
            sql += " ON CONFLICT {} DO UPDATE SET {}".format(conflict, up)
            params = vals + [vals[i] for i, c in enumerate(cols) if c not in pk.split(",")]
        elif _is_pg(conn) and table in _IDENTITY_TABLES:
            sql = "INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES ({})".format(
                table, ",".join(cols), ph)
            params = vals
        else:
            params = vals
        if _is_pg(conn):
            sql = _adapt_sql(sql)
        conn.execute(sql, params)
        conn.commit()
        invalidate(table)  # 写后失效缓存 (2026-08-17)
    finally:
        conn.close()


def fetch_all(table: str, limit: int = 1000) -> List[Dict[str, Any]]:
    """读全部行(倒序, 限 limit)。

    2026-08-17: 两级缓存 (redis → 内存 TTL) —— 高速读取命中缓存不查 DB。
    TTL: 默认 30s (认知引擎写入频率中等, 短暂延迟可接受)。
    """
    from laap.paper_trading.cache_backend import cache_get, cache_set
    ck = f"cognitive:{table}:all:{limit}"
    cached = cache_get(ck)
    if cached is not None:
        return cached
    conn = _connect()
    try:
        pk = _PK.get(table)
        if pk:
            order = pk
        elif _is_pg(conn):
            order = "id"
        else:
            order = "rowid"
        sql = f"SELECT * FROM {table} ORDER BY {order} DESC LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
        result = [dict(r) for r in rows]
        cache_set(ck, result, ttl=_READ_TTL)
        return result
    finally:
        conn.close()


def fetch_where(table: str, where: str, params: List[Any],
                limit: int = 1000) -> List[Dict[str, Any]]:
    """按条件查询。

    2026-08-17: 两级缓存 (redis → 内存 TTL)。
    """
    from laap.paper_trading.cache_backend import cache_get, cache_set
    ck = f"cognitive:{table}:w:{where}:{','.join(str(p) for p in params)}:{limit}"
    cached = cache_get(ck)
    if cached is not None:
        return cached
    conn = _connect()
    try:
        sql = f"SELECT * FROM {table} WHERE {where} LIMIT {int(limit)}"
        if _is_pg(conn):
            sql = _adapt_sql(sql)
        rows = conn.execute(sql, params).fetchall()
        result = [dict(r) for r in rows]
        cache_set(ck, result, ttl=_READ_TTL)
        return result
    finally:
        conn.close()


def invalidate(table: str) -> None:
    """写入后失效该表的缓存 (2026-08-17)。"""
    from laap.paper_trading.cache_backend import cache_clear_prefix
    try:
        cache_clear_prefix(f"cognitive:{table}:")
    except Exception:
        pass


def truncate(table: str) -> None:
    """清空表。"""
    conn = _connect()
    try:
        if _is_pg(conn):
            conn.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
        else:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        invalidate(table)  # 写后失效缓存 (2026-08-17)
    finally:
        conn.close()


def set_meta(key: str, value: Any) -> None:
    """写 meta_learning_meta 计数器。"""
    upsert("meta_learning_meta", {"key": key, "value": str(value)})


def get_meta(key: str, default: Any = 0) -> Any:
    """读 meta_learning_meta 计数器。"""
    rows = fetch_where("meta_learning_meta", "key = ?", [key], limit=1)
    if not rows:
        return default
    v = rows[0].get("value", "")
    try:
        return int(v)
    except ValueError:
        return v
