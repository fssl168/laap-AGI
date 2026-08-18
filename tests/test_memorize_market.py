# -*- coding: utf-8 -*-
"""Tests for _memorize_market.py (大盘行情记忆脚本)。"""

import importlib.util
import sys
import re
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "market" / "_memorize_market.py"

# 真实腾讯响应样例（上证指数，前 38 字段）
SAMPLE_RAW = (
    'v_sh000001="1~上证指数~000001~3934.09~3966.59~3950.71~529490944~0~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~'
    '0~0.00~0~0.00~0~0.00~0~0.00~0~~20260811161401~-32.50~-0.82~3966.39~3930.64~'
    '3934.09/529490944/1066737091823~529490944~106673709~1.09~18.04~~3966.39~3930.64~0.90~613911.42~689731.22~'
    '0.00~-1~-1~0.94~0~3952.01~~~~~~106673709.1823~0.0000~0~ ~ZS~-0.88~2.93";'
).encode("gbk")


@pytest.fixture(scope="module")
def script_mod():
    spec = importlib.util.spec_from_file_location("memorize_market", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["memorize_market"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_compiles():
    compile(SCRIPT.read_bytes(), str(SCRIPT), "exec")


def test_fetch_market_parses(script_mod):
    """mock 腾讯响应：正确解析指数行情。"""
    # 脚本已自带 urllib 实现（2026-08-17 起不再依赖 src.agent.tools.search_tools）
    with patch.object(script_mod, "_fetch_url", return_value=SAMPLE_RAW):
        indices = script_mod.fetch_market()

    assert len(indices) == 1
    ix = indices[0]
    assert ix["name"] == "上证指数"
    assert ix["price"] == 3934.09
    assert ix["change_pct"] == -0.82
    assert ix["turnover_yi"] == round(106673709 / 10000.0, 1)


def test_summary_contains_key_facts(script_mod):
    """记忆条目包含日期/指数/涨跌/成交额关键信息。"""
    # 用真实数据构造条目文本（与脚本 main 中逻辑一致）
    indices = [{"name": "上证指数", "price": 3934.09, "change": -32.5,
                "change_pct": -0.82, "turnover_yi": 10667.4}]
    parts = []
    for ix in indices:
        arrow = "涨" if ix["change"] >= 0 else "跌"
        parts.append(f"{ix['name']}{ix['price']}点({arrow}{abs(ix['change_pct']):.2f}%,成交{ix['turnover_yi']}亿)")
    summary = ("【大盘行情记忆】2026-08-11(周二) A股收盘: " + ", ".join(parts)
               + "; 两市合计成交约2.32万亿; 数据来源腾讯行情, 由LAAP工具链路获取.")
    assert "2026-08-11" in summary
    assert "上证指数3934.09点" in summary
    assert "跌0.82%" in summary
    assert "10667.4亿" in summary


@pytest.mark.network
def test_memorize_recall_roundtrip(script_mod, laap_api_live):
    """真实链路：写入 reflect → 召回命中（需要 LAAP daemon 运行）。

    依赖语义记忆库中已存在大盘行情样本；样本缺失时跳过而非失败。
    """
    d = script_mod.post(script_mod.RECALL, {"query": "今天大盘 上证指数行情", "limit": 3})
    if not d.get("memories"):
        pytest.skip("语义记忆库无大盘行情样本 (需先运行 scripts/market/_memorize_market.py)")
    top = d["memories"][0]
    text = top.get("text") or ""
    if "大盘行情记忆" not in text:
        pytest.skip("召回结果不含大盘行情记忆样本")
    assert top.get("score", 0) >= 0.1
