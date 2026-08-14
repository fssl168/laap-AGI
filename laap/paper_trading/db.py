"""LAAP Paper Trading — SQLite 持久化层。

对标 DSA 的 stock_analysis.db，用标准库 sqlite3（零新依赖）。
默认库路径 <LAAP_ROOT>/data/paper_trading.db，可注入（测试用 tmp）。

注意（沙箱/挂载盘约束）: SQLite 在挂载盘（9p）会 disk I/O error，
测试必须 TMPDIR=/tmp 且 db_path 注入 tmp 路径。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

DEFAULT_DB_NAME = "paper_trading.db"


def _default_db_path() -> str:
    root = os.environ.get("LAAP_ROOT", str(Path.cwd()))
    return str(Path(root) / "data" / DEFAULT_DB_NAME)


# 幂等建表 schema（决策 #3 SQLite）
_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    trigger_price REAL NOT NULL DEFAULT 0.0,
    ts REAL NOT NULL,
    rationale TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    signal_id TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    fill_price REAL NOT NULL DEFAULT 0.0,
    filled_ts REAL,
    client_request_id TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    entry_price REAL NOT NULL DEFAULT 0.0,
    exit_price REAL,
    pnl REAL,
    pnl_pct REAL,
    hold_days INTEGER,
    entry_ts REAL NOT NULL,
    exit_ts REAL
);

CREATE TABLE IF NOT EXISTS net_values (
    ts REAL PRIMARY KEY,
    cash REAL NOT NULL DEFAULT 0.0,
    equity REAL NOT NULL DEFAULT 0.0,
    total REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    ts REAL NOT NULL,
    rationale TEXT DEFAULT '',
    basis_memories TEXT DEFAULT '[]',
    risk_note TEXT DEFAULT '',
    expected TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS outcomes (
    trade_id TEXT PRIMARY KEY,
    decision_id TEXT DEFAULT '',
    pnl_pct REAL NOT NULL DEFAULT 0.0,
    hold_days INTEGER NOT NULL DEFAULT 0,
    vs_expected TEXT DEFAULT '',
    lesson TEXT DEFAULT '',
    lesson_type TEXT DEFAULT '',
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS evolutions (
    mutation_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL,
    reason TEXT DEFAULT '',
    meta_json TEXT DEFAULT '{}',
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_signal ON orders(signal_id);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_outcomes_lesson_type ON outcomes(lesson_type);
"""


class PaperDB:
    """SQLite 连接管理 + schema 初始化。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        # 确保父目录存在（幂等）
        parent = Path(self.db_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        """幂等建表。"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.executescript(_SCHEMA)
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            # 挂载盘 9p 可能失败；向上抛，测试注入 tmp 路径规避
            raise sqlite3.Error(f"PaperDB schema init failed: {e}") from e

    def conn(self) -> sqlite3.Connection:
        """返回新连接（check_same_thread=False，row_factory=Row）。"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
