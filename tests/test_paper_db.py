"""P0 PaperDB SQLite 持久化层测试。

验证: 建库幂等 / 表结构 / 连接可用 / 可注入 tmp 路径。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.db import PaperDB


@pytest.fixture()
def db(tmp_path):
    """注入 tmp 路径，避免挂载盘 SQLite 9p disk I/O error。"""
    return PaperDB(db_path=str(tmp_path / "paper_trading.db"))


def test_db_creates_file(db, tmp_path):
    assert Path(db.db_path).exists()


def test_schema_tables_exist(db):
    conn = db.conn()
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    for t in ("signals", "orders", "trades", "net_values",
              "decisions", "outcomes", "evolutions"):
        assert t in tables, f"missing table: {t}"


def test_init_idempotent(db):
    """重复初始化不报错、不破坏已有数据。"""
    conn = db.conn()
    conn.execute("INSERT INTO signals (id, symbol, action, quantity, trigger_price, ts) "
                 "VALUES ('s1', '600519', 'buy', 100, 1355.0, 1.0)")
    conn.commit()
    conn.close()
    # 重新初始化
    db2 = PaperDB(db_path=db.db_path)
    conn = db2.conn()
    n = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    conn.close()
    assert n == 1


def test_conn_returns_rows(db):
    conn = db.conn()
    conn.execute("INSERT INTO signals (id, symbol, action, quantity, trigger_price, ts) "
                 "VALUES ('s2', '000001', 'sell', 50, 10.5, 2.0)")
    conn.commit()
    row = conn.execute("SELECT * FROM signals WHERE id='s2'").fetchone()
    conn.close()
    assert row["symbol"] == "000001"
    assert row["action"] == "sell"
    assert row["quantity"] == 50


def test_orders_client_request_id_unique(db):
    conn = db.conn()
    conn.execute("INSERT INTO orders (id, client_request_id) VALUES ('o1', 'req-1')")
    conn.commit()
    # 重复 client_request_id 应违反 UNIQUE 约束
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO orders (id, client_request_id) VALUES ('o2', 'req-1')")
        conn.commit()
    conn.close()
