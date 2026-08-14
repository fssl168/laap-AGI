# -*- coding: utf-8 -*-
"""Tests for _kline_chart.py（大盘 K 线图脚本）。"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "_kline_chart.py"

INDEX_JSON = {
    "code": 0,
    "data": {
        "sh000001": {
            "day": [
                ["2026-08-03", "3300.0", "3310.0", "3320.0", "3290.0", "400000000.0"],
                ["2026-08-04", "3312.0", "3305.0", "3330.0", "3295.0", "420000000.0"],
                ["2026-08-05", "3306.0", "3320.0", "3335.0", "3300.0", "410000000.0"],
                ["2026-08-06", "3322.0", "3340.0", "3350.0", "3310.0", "430000000.0"],
                ["2026-08-07", "3342.0", "3330.0", "3355.0", "3325.0", "440000000.0"],
            ]
        }
    },
}


@pytest.fixture(scope="module")
def chart_mod():
    spec = importlib.util.spec_from_file_location("kline_chart", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kline_chart"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_compiles():
    compile(SCRIPT.read_bytes(), str(SCRIPT), "exec")


def test_fetch_index_kline_parses(chart_mod):
    with patch("urllib.request.urlopen") as m:
        m.return_value.__enter__.return_value.read.return_value = json.dumps(INDEX_JSON).encode()
        rows = chart_mod.fetch_index_kline()

    assert len(rows) == 5
    assert rows[0][0] == "2026-08-03"
    assert float(rows[0][1]) == 3300.0  # open
    assert float(rows[0][2]) == 3310.0  # close


def test_last_week_range(chart_mod):
    lo, hi = chart_mod.last_week_range("2026-08-11")
    # 08-11 是周二，本周一 08-10，上周 = 08-03(周一) ~ 08-07(周五)
    assert lo == "2026-08-03"
    assert hi == "2026-08-07"


def test_last_week_range_monday(chart_mod):
    lo, hi = chart_mod.last_week_range("2026-08-10")  # 周一
    assert lo == "2026-08-03"
    assert hi == "2026-08-07"


def test_draw_candles_creates_image(chart_mod):
    """mock 数据绘制：输出 PNG 存在且非空。"""
    tmpdir = tempfile.mkdtemp(prefix="kline_chart_")
    out = str(Path(tmpdir) / "test_kline.png")
    rows = [
        ["2026-08-03", "3300.0", "3310.0", "3320.0", "3290.0", "400000000.0"],
        ["2026-08-04", "3312.0", "3305.0", "3330.0", "3295.0", "420000000.0"],
        ["2026-08-05", "3306.0", "3320.0", "3335.0", "3300.0", "410000000.0"],
    ]
    chart_mod.draw_candles(rows, "测试K线", out, show_ma=[3])

    assert Path(out).exists()
    assert Path(out).stat().st_size > 1000
    from PIL import Image

    img = Image.open(out)
    assert img.size[0] > 500


def test_persist_index_kline(chart_mod):
    """三年指数数据落盘（临时 db）。"""
    import watchlist_kline_store as store

    tmpdir = tempfile.mkdtemp(prefix="kline_persist_")
    tmp = Path(tmpdir) / "k.db"
    rows = [["2026-08-03", "3300.0", "3310.0", "3320.0", "3290.0", "400000000.0"],
            ["2026-08-04", "3312.0", "3305.0", "3330.0", "3295.0", "420000000.0"]]
    with patch.object(store, "DB_PATH", tmp):
        n = chart_mod.persist(rows)
        stats = store.db_stats()
        kline = store.get_kline("sh000001", days=10)

    assert n == 2
    assert stats["total_rows"] == 2
    assert kline[-1][2] == 3305.0
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(tmp) + suffix)
        if p.exists():
            p.unlink()
