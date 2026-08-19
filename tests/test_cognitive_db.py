"""cognitive_db 存储层单元测试 (2026-08-18)。

覆盖 upsert / fetch_all / fetch_where / invalidate / truncate / set_meta /
get_meta 全链路。后端固定 SQLite、路径走 TMPDIR 保证可重复 / 可并行。

运行:
    TMPDIR=/tmp python -m pytest tests/test_cognitive_db.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 强制 SQLite 后端 + 临时路径,避免触碰 NAS PG / 已有 data/laap.db
os.environ.setdefault("COGNITIVE_DB_BACKEND", "sqlite")
_temp_dir = None


@pytest.fixture(scope="session", autouse=True)
def _setup_sqlite_backend(tmp_path_factory):
    global _temp_dir
    _temp_dir = str(tmp_path_factory.mktemp("cognitive_db_test"))
    os.environ["COGNITIVE_DB_PATH"] = os.path.join(_temp_dir, "laap.db")
    yield


# ─── 表结构初始化 ─────────────────────────────────────────────────────────────

def _init_table(conn, table: str, schema: list, pk: str | None = None):
    """轻量建表助手:按表名 + schema 构造 SQLite DDL,跳过已存在表。"""
    cols = ", ".join(f"{c} {' '.join(t)}" for c, t in schema)
    if pk:
        cols += f", PRIMARY KEY ({pk})"
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols})")
    conn.commit()


def _ensure_tables():
    import sqlite3
    conn = sqlite3.connect(os.environ["COGNITIVE_DB_PATH"])
    try:
        _init_table(conn, "rsi_params", [("name", "TEXT PRIMARY KEY"),
                                          ("current_value", "REAL"),
                                          ("step_size", "REAL")], pk="name")
        _init_table(conn, "meta_learning_meta", [("key", "TEXT PRIMARY KEY"),
                                                   ("value", "TEXT")], pk="key")
    finally:
        conn.close()


# ─── 1. upsert ────────────────────────────────────────────────────────────────

class TestUpsert:
    def test_insert_new_row(self):
        _ensure_tables()
        from laap.agi.cognitive_db import upsert
        upsert("rsi_params", {"name": "lr", "current_value": 0.1, "step_size": 0.01})
        from laap.agi.cognitive_db import fetch_where
        rows = fetch_where("rsi_params", "name = ?", ["lr"])
        assert len(rows) == 1
        assert rows[0]["current_value"] == pytest.approx(0.1)

    def test_update_existing_row(self):
        _ensure_tables()
        from laap.agi.cognitive_db import upsert
        upsert("rsi_params", {"name": "lr", "current_value": 0.1, "step_size": 0.01})
        upsert("rsi_params", {"name": "lr", "current_value": 0.2, "step_size": 0.01})
        from laap.agi.cognitive_db import fetch_where
        rows = fetch_where("rsi_params", "name = ?", ["lr"])
        assert len(rows) == 1
        assert rows[0]["current_value"] == pytest.approx(0.2)

    def test_json_serializes_collections(self):
        _ensure_tables()
        from laap.agi.cognitive_db import upsert
        upsert("meta_learning_meta", {"key": "attempt_log", "value": json.dumps([1, 2, 3])})
        from laap.agi.cognitive_db import fetch_where
        rows = fetch_where("meta_learning_meta", "key = ?", ["attempt_log"])
        assert len(rows) == 1
        # 存进去是字符串,fetch 回来仍是字符串
        assert rows[0]["value"] == "[1, 2, 3]"


# ─── 2. fetch_all / fetch_where ───────────────────────────────────────────────

class TestFetch:
    def test_fetch_all_returns_rows(self):
        _ensure_tables()
        from laap.agi.cognitive_db import upsert, fetch_all
        upsert("rsi_params", {"name": "a", "current_value": 1.0, "step_size": 0.1})
        upsert("rsi_params", {"name": "b", "current_value": 2.0, "step_size": 0.2})
        rows = fetch_all("rsi_params", limit=10)
        assert len(rows) >= 2

    def test_fetch_all_respects_limit(self):
        _ensure_tables()
        from laap.agi.cognitive_db import upsert, fetch_all
        for i in range(5):
            upsert("rsi_params", {"name": f"x{i}", "current_value": float(i),
                                   "step_size": 0.1})
        rows = fetch_all("rsi_params", limit=2)
        assert len(rows) <= 2

    def test_fetch_where(self):
        _ensure_tables()
        from laap.agi.cognitive_db import upsert, fetch_where
        upsert("meta_learning_meta", {"key": "k1", "value": "v1"})
        rows = fetch_where("meta_learning_meta", "key = ?", ["k1"])
        assert len(rows) == 1
        assert rows[0]["value"] == "v1"

    def test_fetch_where_empty(self):
        _ensure_tables()
        from laap.agi.cognitive_db import fetch_where
        rows = fetch_where("meta_learning_meta", "key = ?", ["no_such_key"])
        assert rows == []


# ─── 3. cache invalidation ───────────────────────────────────────────────────

class TestInvalidate:
    def test_invalidate_clears_cached_results(self):
        _ensure_tables()
        from laap.agi.cognitive_db import upsert, fetch_all, invalidate
        upsert("meta_learning_meta", {"key": "cnt", "value": "0"})
        r1 = fetch_all("meta_learning_meta", limit=10)
        # 仅看 cnt 那一行(允许其他测试残留行)
        cnt_rows = [r for r in r1 if r.get("key") == "cnt"]
        assert len(cnt_rows) == 1
        # upsert 对 int 值不做 json 序列化,存为 int
        assert cnt_rows[0]["value"] == 0
        # 改数据
        upsert("meta_learning_meta", {"key": "cnt", "value": 99})
        # invalidate 后再取应拿到新值
        invalidate("meta_learning_meta")
        r2 = fetch_all("meta_learning_meta", limit=10)
        cnt_rows2 = [r for r in r2 if r.get("key") == "cnt"]
        assert len(cnt_rows2) == 1
        assert cnt_rows2[0]["value"] == 99


# ─── 4. truncate ──────────────────────────────────────────────────────────────

class TestTruncate:
    def test_truncate_clears_table(self):
        _ensure_tables()
        from laap.agi.cognitive_db import upsert, truncate, fetch_all
        upsert("meta_learning_meta", {"key": "x", "value": "y"})
        truncate("meta_learning_meta")
        rows = fetch_all("meta_learning_meta", limit=10)
        assert rows == []


# ─── 5. set_meta / get_meta ───────────────────────────────────────────────────

class TestMeta:
    def test_set_get_roundtrip(self):
        _ensure_tables()
        from laap.agi.cognitive_db import set_meta, get_meta
        set_meta("version", 42)
        assert get_meta("version") == 42

    def test_get_meta_default_missing(self):
        _ensure_tables()
        from laap.agi.cognitive_db import get_meta
        assert get_meta("no_such_key", 7) == 7


# ─── 6. 不污染持久 DB ────────────────────────────────────────────────────────

def test_backend_is_sqlite():
    from laap.agi.cognitive_db import _BACKEND
    assert _BACKEND == "sqlite"
