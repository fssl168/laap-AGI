# -*- coding: utf-8 -*-
"""Tests for watchlist_kline_store.py（K 线 SQLite 存储层）。"""

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

STORE = Path(__file__).resolve().parents[1] / "watchlist_kline_store.py"


@pytest.fixture(scope="module")
def store_mod():
    spec = importlib.util.spec_from_file_location("watchlist_kline_store", STORE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["watchlist_kline_store"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def tmp_db(store_mod):
    """用临时 db 路径隔离测试数据。"""
    tmpdir = tempfile.mkdtemp(prefix="kline_test_")
    tmp = Path(tmpdir) / "kline.db"
    with patch.object(store_mod, "DB_PATH", tmp):
        yield tmp
    # 清理
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suffix)
        if p.exists():
            p.unlink()


SAMPLE_ROWS = [
    ("sh600326", "2026-08-07", 7.92, 7.60, 8.10, 7.43, 1363170.0),
    ("sh600326", "2026-08-10", 7.55, 8.36, 8.36, 7.50, 1224570.0),
    ("sh600326", "2026-08-11", 8.16, 7.83, 8.18, 7.81, 1173995.0),
    ("sz002790", "2026-08-10", 5.80, 5.90, 5.93, 5.79, 500000.0),
    ("sz002790", "2026-08-11", 5.90, 5.83, 5.93, 5.79, 480000.0),
]


def test_store_compiles():
    compile(STORE.read_bytes(), str(STORE), "exec")


def test_upsert_and_get_kline(store_mod, tmp_db):
    store_mod.upsert_kline(SAMPLE_ROWS)
    kline = store_mod.get_kline("sh600326", days=10)
    assert [r[0] for r in kline] == ["2026-08-07", "2026-08-10", "2026-08-11"]  # 升序
    assert kline[-1][2] == 7.83  # 最新收盘


def test_upsert_idempotent(store_mod, tmp_db):
    store_mod.upsert_kline(SAMPLE_ROWS)
    store_mod.upsert_kline(SAMPLE_ROWS)  # 重复落盘（INSERT OR REPLACE）
    assert store_mod.db_stats()["total_rows"] == 5


def test_get_ma(store_mod, tmp_db):
    store_mod.upsert_kline(SAMPLE_ROWS)
    ma = store_mod.get_ma("sh600326", days=10, window=3)
    # 3 日均线：3 根 → 1 个点 (7.60+8.36+7.83)/3
    assert len(ma) == 1
    date, close, m = ma[0]
    assert date == "2026-08-11"
    assert close == 7.83
    assert m == round((7.60 + 8.36 + 7.83) / 3, 2)


def test_get_day_overview_with_pct(store_mod, tmp_db):
    store_mod.upsert_kline(SAMPLE_ROWS)
    overview = store_mod.get_day_overview("2026-08-11", codes=["sh600326", "sz002790"])
    items = overview["items"]
    assert len(items) == 2
    # 600326: (7.83-8.36)/8.36 = -6.34%
    assert items["sh600326"]["pct"] == pytest.approx(-6.34, abs=0.01)
    # 002790: (5.83-5.90)/5.90 = -1.19%
    assert items["sz002790"]["pct"] == pytest.approx(-1.19, abs=0.01)


def test_get_latest_day(store_mod, tmp_db):
    store_mod.upsert_kline(SAMPLE_ROWS)
    assert store_mod.get_latest_day() == "2026-08-11"


def test_get_trading_days_desc(store_mod, tmp_db):
    store_mod.upsert_kline(SAMPLE_ROWS)
    days = store_mod.get_trading_days(limit=5)
    assert days[0] == "2026-08-11"  # 降序，最新在前
    assert days[1] == "2026-08-10"
    assert days[2] == "2026-08-07"


def test_db_stats(store_mod, tmp_db):
    store_mod.upsert_kline(SAMPLE_ROWS)
    s = store_mod.db_stats()
    assert s["total_rows"] == 5
    assert s["codes"] == 2
    assert s["days"] == 3


def test_stock_names_roundtrip(store_mod, tmp_db):
    store_mod.upsert_stock_names({"sh600326": "西藏天路", "sz002790": "瑞尔特"})
    names = store_mod.get_stock_names(["sh600326", "sz002790"])
    assert names == {"sh600326": "西藏天路", "sz002790": "瑞尔特"}
    all_names = store_mod.get_stock_names()
    assert len(all_names) == 2
    assert store_mod.db_stats()["names"] == 2


def test_get_day_overview_name_format(store_mod, tmp_db):
    """查询结果含名称+代码（下标展示用）。"""
    store_mod.upsert_kline(SAMPLE_ROWS)
    store_mod.upsert_stock_names({"sh600326": "西藏天路", "sz002790": "瑞尔特"})
    overview = store_mod.get_day_overview("2026-08-11", codes=["sh600326", "sz002790"])
    assert overview["items"]["sh600326"]["close"] == 7.83
    names = store_mod.get_stock_names(["sh600326"])
    assert names["sh600326"] == "西藏天路"
