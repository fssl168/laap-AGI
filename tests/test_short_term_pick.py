# -*- coding: utf-8 -*-
"""Tests for _short_term_pick.py（短线激进选股评分脚本）。"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "_short_term_pick.py"


@pytest.fixture(scope="module")
def pick_mod():
    spec = importlib.util.spec_from_file_location("short_term_pick", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["short_term_pick"] = mod
    spec.loader.exec_module(mod)
    return mod


def _mk_kline(closes, vols=None, base="2026-07-01"):
    """构造日K数据：[[date, open, close, high, low, vol], ...] 升序（数值为 float，与 db 一致）。"""
    import datetime

    start = datetime.date.fromisoformat(base)
    out = []
    for i, c in enumerate(closes):
        d = (start + datetime.timedelta(days=i)).isoformat()
        high = c * 1.02
        low = c * 0.98
        vol = (vols or [1000000.0] * len(closes))[i]
        out.append([d, float(c), float(c), float(high), float(low), float(vol)])
    return out


def test_script_compiles():
    compile(SCRIPT.read_bytes(), str(SCRIPT), "exec")


def test_analyze_ranks_momentum_first(pick_mod):
    """强动量+多头+放量的股票应排第一。"""
    # 强势股：持续上涨（多头排列）+ 放量（13 根）
    strong = _mk_kline([10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 14.5, 15, 16, 17],
                       vols=[1000000] * 7 + [2000000] * 6)
    # 弱势股：横盘震荡（13 根）
    weak = _mk_kline([10, 10.1, 9.9, 10.2, 10, 10.1, 9.8, 10, 10.2, 10.1, 10, 10.2, 9.9])
    kline_map = {"sh600001": strong, "sh600002": weak}

    with patch.object(pick_mod, "CODES", "600001 600002"), \
         patch.object(pick_mod, "get_kline", side_effect=lambda code, days=20: kline_map.get(code, [])), \
         patch.object(pick_mod, "get_stock_names", return_value={"sh600001": "强势股", "sh600002": "弱势股"}):
        scores = pick_mod.analyze()

    assert len(scores) == 2
    assert scores[0]["code"] == "600001"  # 强势股排第一
    assert scores[0]["ret5"] > 0
    assert scores[0]["ma_bull"] is True
    assert scores[1]["code"] == "600002"
    assert scores[0]["score"] > scores[1]["score"]


def test_analyze_scores_positive_momentum(pick_mod):
    """上涨股的 5 日动量应为正，下跌股为负。"""
    up = _mk_kline([10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 14.5, 15, 15.5, 16])
    down = _mk_kline([16, 15.5, 15, 14.5, 14, 13.5, 13, 12.5, 12, 11.5, 11, 10.5, 10])

    with patch.object(pick_mod, "CODES", "600001 600002"), \
         patch.object(pick_mod, "get_kline",
                      side_effect=lambda code, days=20: {"sh600001": up, "sh600002": down}.get(code, [])), \
         patch.object(pick_mod, "get_stock_names",
                      return_value={"sh600001": "涨", "sh600002": "跌"}):
        scores = pick_mod.analyze()

    by_code = {s["code"]: s for s in scores}
    assert by_code["600001"]["ret5"] > 0
    assert by_code["600002"]["ret5"] < 0


def test_analyze_skips_insufficient_data(pick_mod):
    """数据不足（<12 根）的股票被跳过。"""
    short = _mk_kline([10, 11, 12])

    with patch.object(pick_mod, "get_kline",
                      side_effect=lambda code, days=20: {"sh600001": short}.get(code, [])), \
         patch.object(pick_mod, "get_stock_names", return_value={"sh600001": "短"}):
        scores = pick_mod.analyze()

    assert scores == []
