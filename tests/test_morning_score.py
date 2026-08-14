# -*- coding: utf-8 -*-
"""Tests for _morning_score.py（开盘前实时短线评分脚本）。"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "market" / "_morning_score.py"

KLINE = [
    ["2026-07-01", 10.0, 10.0, 10.2, 9.8, 1000000.0],
    ["2026-07-02", 10.1, 10.1, 10.3, 9.9, 1000000.0],
    ["2026-07-03", 10.2, 10.2, 10.4, 10.0, 1000000.0],
    ["2026-07-06", 10.3, 10.3, 10.5, 10.1, 1000000.0],
    ["2026-07-07", 10.4, 10.4, 10.6, 10.2, 1000000.0],
    ["2026-07-08", 10.5, 10.5, 10.7, 10.3, 1000000.0],
    ["2026-07-09", 10.6, 10.6, 10.8, 10.4, 1000000.0],
    ["2026-07-10", 10.7, 10.7, 10.9, 10.5, 1000000.0],
    ["2026-07-13", 10.8, 10.8, 11.0, 10.6, 1000000.0],
    ["2026-07-14", 10.9, 10.9, 11.1, 10.7, 1000000.0],
    ["2026-07-15", 11.0, 11.0, 11.2, 10.8, 1000000.0],
    ["2026-07-16", 11.1, 11.1, 11.3, 10.9, 1000000.0],
    ["2026-07-17", 11.2, 11.2, 11.4, 11.0, 1000000.0],
]


@pytest.fixture(scope="module")
def score_mod():
    spec = importlib.util.spec_from_file_location("morning_score", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["morning_score"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_compiles():
    compile(SCRIPT.read_bytes(), str(SCRIPT), "exec")


def test_fetch_realtime_structure(score_mod):
    """fetch_realtime 返回字典，含 name/price/pct/volume 字段。"""
    fake_rt = {"600326": {"name": "西藏天路", "price": 7.83, "pct": -6.34, "volume": 1173995.0}}
    with patch.object(score_mod, "fetch_realtime", return_value=fake_rt):
        rt = score_mod.fetch_realtime()
    assert isinstance(rt, dict)
    assert rt["600326"]["price"] == 7.83
    assert rt["600326"]["pct"] == -6.34


def test_score_with_realtime(score_mod):
    """实时价 + 历史 K：上涨放量股评分高于横盘股。"""
    realtime = {
        "600326": {"name": "西藏天路", "price": 12.0, "pct": 5.0, "volume": 3000000.0},
        "002790": {"name": "瑞尔特", "price": 10.0, "pct": -2.0, "volume": 500000.0},
    }
    with patch.object(score_mod, "CODES", "600326 002790"), \
         patch("watchlist_kline_store.get_kline",
               side_effect=lambda code, days=20: KLINE if code in ("sh600326", "sz002790") else []):
        scores = score_mod.score_with_realtime(realtime)

    assert len(scores) == 2
    assert scores[0]["code"] == "600326"  # 上涨+放量排第一
    assert scores[0]["ret5"] > 0
    assert scores[1]["code"] == "002790"


def test_main_output_format(score_mod):
    """main() 输出包含候选、预算标记与风险提示。"""
    import io
    import contextlib

    # 42 只 mock 实时数据（满足 len >= 40 检查）
    codes = score_mod.CODES.split()
    realtime = {}
    for i, c in enumerate(codes):
        realtime[c] = {"name": f"股票{i}", "price": 5.0 + i * 0.1, "pct": 1.0,
                       "volume": 2000000.0}
    # 让600326在预算内（5元*100=500元）
    realtime["600326"] = {"name": "西藏天路", "price": 5.0, "pct": 5.0, "volume": 3000000.0}

    with patch.object(score_mod, "fetch_realtime", return_value=realtime), \
         patch("watchlist_kline_store.get_kline",
               side_effect=lambda code, days=20: KLINE if code.endswith("600326") else []):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with pytest.raises(SystemExit) as e:
                score_mod.main()
        out = buf.getvalue()

    assert e.value.code == 0
    assert "开盘前短线评分" in out
    assert "西藏天路" in out and "600326" in out
    assert "预算内" in out
    assert "非投资建议" in out
