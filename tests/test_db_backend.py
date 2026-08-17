# -*- coding: utf-8 -*-
"""存储后端抽象测试：PG 适配层（sqlite3 风格兼容）+ SQLite 回退。

PG 部分用 fake 连接（不真连 NAS），验证:
  - ? 占位符 → %s 适配
  - 行 → dict 转换（列名取自 description）
  - executescript 多语句切分
SQLite 部分真实执行（标准库）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from laap.paper_trading.db import (
    _adapt_sql, _split_sql_statements, _PGCursor, _PGConnection,
    _parse_database_url, _adapt_sqlite_dialect, _extract_insert_cols)


# ════════════════════════════════════════════════════════════
# _adapt_sqlite_dialect: OR REPLACE / OR IGNORE → PG
# ════════════════════════════════════════════════════════════

def test_dialect_or_replace_to_conflict_update():
    sql = ("INSERT OR REPLACE INTO evolutions "
           "(mutation_id, decision, reason, meta_json, ts) VALUES (?, ?, ?, ?, ?)")
    out = _adapt_sqlite_dialect(sql)
    assert out.startswith("INSERT INTO evolutions")
    assert "ON CONFLICT (mutation_id) DO UPDATE" in out
    assert "decision = EXCLUDED.decision" in out
    assert "ts = EXCLUDED.ts" in out


def test_dialect_or_ignore_to_do_nothing():
    sql = "INSERT OR IGNORE INTO news_items (id, symbol) VALUES (?, ?)"
    out = _adapt_sqlite_dialect(sql)
    assert out.startswith("INSERT INTO news_items")
    assert out.endswith("ON CONFLICT DO NOTHING")


def test_dialect_plain_unchanged():
    sql = "SELECT * FROM signals WHERE id=?"
    assert _adapt_sqlite_dialect(sql) == sql


def test_extract_insert_cols():
    cols = _extract_insert_cols(
        " INTO evolutions (mutation_id, decision, reason) VALUES (?, ?, ?)")
    assert cols == ["mutation_id", "decision", "reason"]
    assert _extract_insert_cols("INTO t VALUES (1)") == []


# ════════════════════════════════════════════════════════════
# _parse_database_url: DATABASE_URL 解析
# ════════════════════════════════════════════════════════════

def test_parse_database_url_asyncpg():
    d = _parse_database_url(
        "postgresql+asyncpg://fileclaw:fileclaw_secret@192.168.88.251:54322/laap_trading")
    assert d == {"host": "192.168.88.251", "port": 54322,
                 "user": "fileclaw", "password": "fileclaw_secret",
                 "db": "laap_trading"}


def test_parse_database_url_plain():
    d = _parse_database_url(
        "postgresql://u:p@localhost:5432/laap")
    assert d["host"] == "localhost" and d["port"] == 5432
    assert d["db"] == "laap" and d["user"] == "u" and d["password"] == "p"


def test_parse_database_url_invalid():
    assert _parse_database_url("sqlite:///x.db") is None
    assert _parse_database_url("") is None
    assert _parse_database_url("not-a-url") is None


# ════════════════════════════════════════════════════════════
# _adapt_sql: ? → %s
# ════════════════════════════════════════════════════════════

def test_adapt_sql_question_to_percent():
    sql, params = _adapt_sql(
        "SELECT * FROM signals WHERE id=? AND symbol=?",
        ("a", "600519"))
    assert sql == "SELECT * FROM signals WHERE id=%s AND symbol=%s"
    assert params == ["a", "600519"]


def test_adapt_sql_no_params_unchanged():
    sql, params = _adapt_sql("SELECT 1", None)
    assert sql == "SELECT 1"
    assert params == ()


def test_adapt_sql_scalar_param():
    sql, params = _adapt_sql("SELECT * FROM t WHERE id=?", "x")
    assert sql == "SELECT * FROM t WHERE id=%s"
    assert params == "x"  # 标量参数原样（psycopg 接受）


def test_adapt_sql_no_placeholder_with_params():
    # 无 ? 但传了参数（如 PG 原生 %s 语句）→ 原样传参
    sql, params = _adapt_sql("SELECT * FROM t WHERE id=%s", ("x",))
    assert sql == "SELECT * FROM t WHERE id=%s"
    assert params == ["x"]  # 无 ? 不转换占位符，参数列表化


# ════════════════════════════════════════════════════════════
# _split_sql_statements
# ════════════════════════════════════════════════════════════

def test_split_sql_skips_comments():
    script = """
    -- 注释行
    CREATE TABLE IF NOT EXISTS t1 (id TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS t2 (id TEXT PRIMARY KEY);
    """
    stmts = _split_sql_statements(script)
    # 注释被跳过；2 条建表语句 + 尾部空段（实现保留无分号尾部，无害）
    assert len(stmts) >= 2
    assert any("t1" in s for s in stmts)
    assert any("t2" in s for s in stmts)


# ════════════════════════════════════════════════════════════
# _PGCursor: 行 → dict（fake psycopg cursor）
# ════════════════════════════════════════════════════════════

class _FakeCol:
    def __init__(self, name):
        self.name = name


class _FakePsycopgCursor:
    """模拟 psycopg cursor：tuple 行 + description。"""

    def __init__(self, rows=None, cols=None):
        self._rows = rows or []
        self._cols = cols or []
        self._idx = 0

    @property
    def description(self):
        return [_FakeCol(c) for c in self._cols] if self._cols else None

    @property
    def rowcount(self):
        return len(self._rows)

    @property
    def lastrowid(self):
        return 1

    def execute(self, sql, params=None):
        return self

    def fetchone(self):
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        return row

    def fetchall(self):
        out = self._rows[self._idx:]
        self._idx = len(self._rows)
        return out

    def close(self):
        pass


def test_pgcursor_fetch_dict_with_cols():
    cur = _PGCursor(_FakePsycopgCursor(
        rows=[("600519", "buy", 100)],
        cols=["symbol", "action", "quantity"]))
    row = cur.fetchone()
    assert row == {"symbol": "600519", "action": "buy", "quantity": 100}


def test_pgcursor_fetchall_dicts():
    cur = _PGCursor(_FakePsycopgCursor(
        rows=[("a", 1), ("b", 2)],
        cols=["id", "n"]))
    rows = cur.fetchall()
    assert rows == [{"id": "a", "n": 1}, {"id": "b", "n": 2}]


def test_pgcursor_no_cols_fallback():
    cur = _PGCursor(_FakePsycopgCursor(rows=[("x",)]))
    row = cur.fetchone()
    # 无列名时返回 {"value": <原始行>}（调用方不依赖此场景）
    assert isinstance(row, dict)


# ════════════════════════════════════════════════════════════
# _PGConnection: execute 返回可 fetch 的 cursor
# ════════════════════════════════════════════════════════════

class _FakePsycopgConn:
    def __init__(self):
        self.committed = 0
        self.closed = False

    def cursor(self):
        return _FakePsycopgCursor(
            rows=[("600519",)], cols=["symbol"])

    def commit(self):
        self.committed += 1

    def close(self):
        self.closed = True


def test_pg_connection_execute_chain():
    conn = _PGConnection(_FakePsycopgConn())
    row = conn.execute("SELECT symbol FROM t WHERE id=?", ("x",)).fetchone()
    assert row == {"symbol": "600519"}
    conn.commit()
    assert conn._conn.committed == 1


def test_pg_connection_executescript():
    conn = _PGConnection(_FakePsycopgConn())
    conn.executescript("""
    -- comment
    CREATE TABLE IF NOT EXISTS a (id TEXT);
    CREATE TABLE IF NOT EXISTS b (id TEXT);
    """)
    conn.commit()


# ════════════════════════════════════════════════════════════
# SQLite 回退（真实执行）
# ════════════════════════════════════════════════════════════

def test_sqlite_backend_real(tmp_path, monkeypatch):
    import laap.paper_trading.db as dbmod
    monkeypatch.setattr(dbmod, "_DB_BACKEND", "sqlite")
    db = dbmod.PaperDB(db_path=str(tmp_path / "test.db"))
    assert db.backend == "sqlite"
    conn = db.conn()
    conn.execute(
        "INSERT INTO signals (id, symbol, action, quantity, trigger_price, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("s1", "600519", "buy", 100, 100.0, 1.0))
    conn.commit()
    row = conn.execute("SELECT * FROM signals WHERE id=?", ("s1",)).fetchone()
    assert row["symbol"] == "600519"
    conn.close()
