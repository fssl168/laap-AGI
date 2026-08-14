# -*- coding: utf-8 -*-
"""Tests for _candidate_chart.py（候选股走势图脚本）。"""

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "_candidate_chart.py"

PICKS = [
    {"code": "600162", "name": "香江控股", "close": 4.29, "ret5": 32.41, "date": "2026-08-11"},
    {"code": "600172", "name": "黄河旋风", "close": 12.34, "ret5": 20.98, "date": "2026-08-11"},
]

KLINE = [
    ["2026-07-29", 6.81, 6.81, 6.90, 6.70, 800000.0],
    ["2026-07-30", 7.00, 7.18, 7.25, 6.95, 900000.0],
    ["2026-07-31", 7.10, 6.94, 7.20, 6.85, 850000.0],
    ["2026-08-03", 7.00, 7.00, 7.15, 6.90, 820000.0],
    ["2026-08-04", 7.05, 6.87, 7.10, 6.80, 800000.0],
    ["2026-08-05", 6.90, 6.84, 7.00, 6.75, 780000.0],
    ["2026-08-06", 6.95, 7.52, 7.60, 6.90, 1100000.0],
    ["2026-08-07", 7.60, 7.60, 7.70, 7.40, 1200000.0],
    ["2026-08-10", 7.55, 8.36, 8.40, 7.50, 1300000.0],
    ["2026-08-11", 8.16, 7.83, 8.18, 7.81, 1173995.0],
]


@pytest.fixture(scope="module")
def chart_mod():
    spec = importlib.util.spec_from_file_location("candidate_chart", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["candidate_chart"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_compiles():
    compile(SCRIPT.read_bytes(), str(SCRIPT), "exec")


def test_generates_image_with_mock_data(chart_mod):
    """mock 候选与 K 线：生成有效 PNG（输出到临时路径）。"""
    tmpdir = tempfile.mkdtemp(prefix="cand_chart_")
    tmp_out = Path(tmpdir) / "candidates_10d.png"

    def fake_get_kline(code, days=10):
        return KLINE if code in ("sh600162", "sh600172") else []

    with patch.object(chart_mod, "pick_analyze", return_value=PICKS), \
         patch.object(chart_mod, "get_kline", side_effect=fake_get_kline), \
         patch.object(chart_mod, "OUT", tmp_out), \
         patch.object(chart_mod, "TOPN", 2):
        chart_mod.main()

    assert tmp_out.exists()
    assert tmp_out.stat().st_size > 1000
    from PIL import Image

    img = Image.open(tmp_out)
    assert img.size[0] > 800


def test_no_candidates_exits(chart_mod):
    """无候选数据时正常退出（不抛异常、不生成图）。"""
    tmpdir = tempfile.mkdtemp(prefix="cand_none_")
    tmp_out = Path(tmpdir) / "none.png"

    with patch.object(chart_mod, "pick_analyze", return_value=[]), \
         patch.object(chart_mod, "OUT", tmp_out):
        with pytest.raises(SystemExit) as e:
            chart_mod.main()

    assert e.value.code == 1
    assert not tmp_out.exists()
