# -*- coding: utf-8 -*-
"""watchlist_kline_store 存储层测试（SQLite 隔离，不依赖 NAS PG）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 测试隔离：强制 SQLite（不连 NAS PG16 laap_kline）
os.environ["KLINE_DB_BACKEND"] = "sqlite"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import watchlist_kline_store as ws


@pytest.fixture(autouse=True)
def _tmp_kline_db(tmp_path, monkeypatch):
    """用 tmp 目录的 kline.db（避免污染真实 data/）。"""
    monkeypatch.setattr(ws, "DB_PATH", tmp_path / "kline.db")
    ws._pg_available = None
    yield


def test_upsert_and_get_kline():
    ws.upsert_kline([
        ("sh600519", "2026-08-14", 1355.0, 1341.99, 1359.0, 1338.14, 29853.0),
        ("sh600519", "2026-08-13", 1340.0, 1355.29, 1360.0, 1335.0, 30000.0),
        ("sh000001", "2026-08-14", 3400.0, 3420.0, 3430.0, 3390.0, 100000.0),
    ])
    rows = ws.get_kline("sh600519", days=5)
    assert len(rows) == 2
    # 升序
    assert rows[0][0] == "2026-08-13"
    assert rows[1][0] == "2026-08-14"
    assert rows[1][2] == 1341.99  # close


def test_upsert_replace_same_key():
    ws.upsert_kline([("sh600519", "2026-08-14", 1.0, 2.0, 3.0, 0.5, 100.0)])
    ws.upsert_kline([("sh600519", "2026-08-14", 10.0, 20.0, 30.0, 5.0, 200.0)])
    rows = ws.get_kline("sh600519", days=5)
    assert len(rows) == 1
    assert rows[0][2] == 20.0  # 覆盖后的 close


def test_get_ma():
    ws.upsert_kline([(f"sh600519", f"2026-08-{i:02d}", 0, 100 + i, 0, 0, 0)
                     for i in range(10, 15)])  # close: 110,111,112,113,114
    ma = ws.get_ma("sh600519", days=10, window=3)
    assert len(ma) == 3  # 3 条 3 日均线
    # 最后一条 = (112+113+114)/3
    assert ma[-1][2] == pytest.approx((112 + 113 + 114) / 3)


def test_latest_day_and_trading_days():
    ws.upsert_kline([
        ("sh600519", "2026-08-12", 1, 1, 1, 1, 1),
        ("sh600519", "2026-08-14", 1, 1, 1, 1, 1),
        ("sh000001", "2026-08-13", 1, 1, 1, 1, 1),
    ])
    assert ws.get_latest_day() == "2026-08-14"
    days = ws.get_trading_days(3)
    assert days[0] == "2026-08-14"
    assert days[1] == "2026-08-13"


def test_stock_names_roundtrip():
    ws.upsert_stock_names({"sh600519": "贵州茅台", "sh000001": "上证指数"})
    names = ws.get_stock_names(["sh600519"])
    assert names == {"sh600519": "贵州茅台"}
    all_names = ws.get_stock_names()
    assert len(all_names) == 2


def test_db_stats():
    ws.upsert_kline([(f"sh{i:06d}", "2026-08-14", 1, 1, 1, 1, 1)
                     for i in range(600519, 600522)])
    ws.upsert_stock_names({"sh600519": "a", "sh600520": "b", "sh600521": "c"})
    stats = ws.db_stats()
    assert stats["total_rows"] == 3
    assert stats["codes"] == 3
    assert stats["backend"] == "sqlite"  # 测试隔离
