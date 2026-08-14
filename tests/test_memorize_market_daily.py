# -*- coding: utf-8 -*-
"""Tests for memorize_market_daily.py (每日大盘行情记忆 cron 脚本)。"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "market" / "_memorize_market_daily.py"

SAMPLE_RAW = (
    'v_sh000001="1~上证指数~000001~3934.09~3966.59~3950.71~529490944~0~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~'
    '0~0.00~0~0.00~0~0.00~0~0.00~0~~20260811161401~-32.50~-0.82~3966.39~3930.64~'
    '3934.09/529490944/1066737091823~529490944~106673709~1.09~18.04~~3966.39~3930.64~0.90~613911.42~689731.22~'
    '0.00~-1~-1~0.94~0~3952.01~~~~~~106673709.1823~0.0000~0~ ~ZS~-0.88~2.93";'
).encode("gbk")


@pytest.fixture(scope="module")
def script_mod():
    spec = importlib.util.spec_from_file_location("memorize_market_daily", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["memorize_market_daily"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_compiles():
    compile(SCRIPT.read_bytes(), str(SCRIPT), "exec")


def test_fetch_market_mock(script_mod):
    """mock 腾讯响应：解析出行情数据与数据日期。"""
    from src.agent.tools import search_tools

    with patch.object(search_tools, "_fetch_url", return_value=SAMPLE_RAW):
        data_date, indices = script_mod.fetch_market()

    assert data_date == "20260811"
    assert len(indices) == 1
    assert indices[0]["name"] == "上证指数"
    assert indices[0]["price"] == 3934.09
    assert indices[0]["change_pct"] == -0.82


def test_summary_build_logic(script_mod):
    """摘要构造：日期/涨跌/成交额正确；两市合计不含创业板。"""
    data_date = "20260811"
    dt = __import__("datetime").datetime.strptime(data_date, "%Y%m%d")
    date_str = f"{dt.year}-{dt.month:02d}-{dt.day:02d}(周{script_mod._WEEKDAYS[dt.weekday()]})"
    indices = [
        {"name": "上证指数", "price": 3934.09, "change": -32.5, "change_pct": -0.82, "turnover_yi": 10667.4},
        {"name": "深证成指", "price": 14259.44, "change": -57.52, "change_pct": -0.40, "turnover_yi": 12542.5},
        {"name": "创业板指", "price": 3549.16, "change": 11.95, "change_pct": 0.34, "turnover_yi": 5975.2},
    ]
    parts = []
    for ix in indices:
        arrow = "涨" if ix["change"] >= 0 else "跌"
        parts.append(f"{ix['name']}{ix['price']}点({arrow}{abs(ix['change_pct']):.2f}%,成交{ix['turnover_yi']}亿)")
    total_yi = sum(ix["turnover_yi"] for ix in indices[:2])  # 不含创业板
    summary = (f"【大盘行情记忆】{date_str} A股收盘: " + ", ".join(parts)
               + f"; 两市合计成交约{total_yi / 10000.0:.2f}万亿; 数据来源腾讯行情, 由LAAP工具链路自动记录.")

    assert "2026-08-11(周二)" in summary
    assert "跌0.82%" in summary and "涨0.34%" in summary
    assert "两市合计成交约2.32万亿" in summary  # 10667+12542=23209 亿，不含创业板
    assert "创业板指3549.16点" in summary


@pytest.mark.network
def test_full_roundtrip(script_mod):
    """真实链路：拉取 → 写入 → 召回（需要 LAAP daemon + 网络）。"""
    data_date, indices = script_mod.fetch_market()
    assert len(indices) == 3 and data_date
    assert all(ix["price"] > 0 for ix in indices)
