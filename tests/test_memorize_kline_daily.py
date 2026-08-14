# -*- coding: utf-8 -*-
"""Tests for _memorize_kline_daily.py (每日自选股 K 线记忆脚本)。"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "market" / "_memorize_kline_daily.py"

# 腾讯 K 线接口样例（2 根日 K）
KLINE_JSON = {
    "code": 0,
    "data": {
        "sh600326": {
            "qfqday": [
                ["2026-08-10", "7.550", "8.360", "8.360", "7.500", "1224570.000"],
                ["2026-08-11", "8.16", "7.83", "8.18", "7.81", "1173995"],
            ]
        }
    },
}


@pytest.fixture(scope="module")
def script_mod():
    spec = importlib.util.spec_from_file_location("memorize_kline_daily", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["memorize_kline_daily"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_compiles():
    compile(SCRIPT.read_bytes(), str(SCRIPT), "exec")


def test_prefixed_mapping(script_mod):
    assert script_mod._prefixed("600326") == "sh600326"
    assert script_mod._prefixed("002790") == "sz002790"
    assert script_mod._prefixed("601899") == "sh601899"


def test_fetch_kline_parses(script_mod):
    """mock K 线接口：解析出日 K 行。"""
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(KLINE_JSON).encode()
        rows = script_mod.fetch_kline("600326")

    assert len(rows) == 2
    assert rows[-1][0] == "2026-08-11"
    assert float(rows[-1][2]) == 7.83  # 收盘


def test_watchlist_has_42_codes(script_mod):
    assert len(script_mod.WATCHLIST.split()) == 42
    assert script_mod.WATCHLIST.split()[0] == "600326"
    assert script_mod.WATCHLIST.split()[-1] == "002347"


@pytest.mark.network
def test_real_fetch_one_stock(script_mod):
    """真实网络：单只股票 K 线可拉取。"""
    rows = script_mod.fetch_kline("600326")
    assert rows and rows[-1][0] >= "2026-08-01"


# ── 数据源抓取保护：并发 ≤2 + 随机冷却 ──────────────────────

def test_fetch_batch_concurrency_limited(script_mod):
    """并发不超过 MAX_CONCURRENCY（用 sleep 探针测量活动线程数）。"""
    import threading
    import time

    active = []
    peak = [0]
    lock = threading.Lock()

    def slow_fetch(code):
        with lock:
            active.append(1)
            peak[0] = max(peak[0], len(active))
        time.sleep(0.3)
        with lock:
            active.pop()
        return [["2026-08-11", "8.16", "7.83", "8.18", "7.81", "1173995"]]

    with patch.object(script_mod, "fetch_kline", side_effect=slow_fetch), \
         patch.object(script_mod.time, "sleep", return_value=None):  # 跳过真实冷却等待
        rows, fails = script_mod.fetch_batch_concurrent(["600326", "002790", "601238", "000975"])

    assert peak[0] <= script_mod.MAX_CONCURRENCY, f"peak concurrency {peak[0]} > {script_mod.MAX_CONCURRENCY}"
    assert len(rows) == 4
    assert not fails


def test_fetch_batch_cool_down_called(script_mod):
    """批间执行随机冷却（sleep 被调用且参数在冷却范围内）。"""
    import time as _time

    sleep_calls = []

    def fake_fetch(code):
        return [["2026-08-11", "8.16", "7.83", "8.18", "7.81", "1173995"]]

    def fake_sleep(secs):
        sleep_calls.append(secs)

    with patch.object(script_mod, "fetch_kline", side_effect=fake_fetch), \
         patch.object(script_mod.time, "sleep", side_effect=fake_sleep):
        script_mod.fetch_batch_concurrent(["600326", "002790", "601238", "000975", "600960", "002131"])

    # 6 只 / 每批 4 → 2 批 → 1 次批间冷却
    assert len(sleep_calls) == 1
    lo, hi = script_mod.COOL_DOWN_RANGE
    assert lo <= sleep_calls[0] <= hi, f"cool down {sleep_calls[0]} outside {COOL_DOWN_RANGE}"


def test_fetch_batch_records_failures(script_mod):
    """失败的股票记录到 failures，不影响成功结果。"""

    def fake_fetch(code):
        if code == "600326":
            raise OSError("timeout")
        return [["2026-08-11", "8.16", "7.83", "8.18", "7.81", "1173995"]]

    with patch.object(script_mod, "fetch_kline", side_effect=fake_fetch), \
         patch.object(script_mod.time, "sleep", return_value=None):
        rows, fails = script_mod.fetch_batch_concurrent(["600326", "002790"])

    assert "600326" in fails
    assert "002790" in rows
    assert len(rows) == 1


def test_persist_kline_writes_to_db(script_mod):
    """完整日 K 落盘 SQLite（用临时 db 隔离）。"""
    import tempfile
    from pathlib import Path
    from unittest.mock import patch as _patch

    tmpdir = tempfile.mkdtemp(prefix="kline_persist_")
    tmp = Path(tmpdir) / "kline.db"

    rows_by_code = {
        "600326": [["2026-08-10", "7.55", "8.36", "8.36", "7.50", "1224570.0"],
                   ["2026-08-11", "8.16", "7.83", "8.18", "7.81", "1173995"]],
        "002790": [["2026-08-11", "5.90", "5.83", "5.93", "5.79", "480000"]],
    }
    import watchlist_kline_store as store

    with _patch.object(store, "DB_PATH", tmp):
        n = script_mod.persist_kline(rows_by_code)
        stats = store.db_stats()
        kline = store.get_kline("sh600326", days=10)

    assert n == 3
    assert stats["total_rows"] == 3
    assert stats["codes"] == 2
    assert kline[-1][2] == 7.83
    # 清理
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suffix)
        if p.exists():
            p.unlink()
