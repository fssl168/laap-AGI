# -*- coding: utf-8 -*-
"""Tests for _read_and_memorize.py (LAAP 论文读取与记忆脚本)。"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "_read_and_memorize.py"


@pytest.fixture(scope="module")
def script_mod():
    """加载 _read_and_memorize.py 为模块（不执行 main）。"""
    spec = importlib.util.spec_from_file_location("read_and_memorize", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["read_and_memorize"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_compiles():
    compile(SCRIPT.read_bytes(), str(SCRIPT), "exec")


def test_paper_summary_contains_key_facts(script_mod):
    s = script_mod.PAPER_SUMMARY
    assert "Mapping the Mind of a Large Language Model" in s
    assert "dictionary learning" in s
    assert "Claude 3 Sonnet" in s
    assert "Anthropic" in s


def test_read_paper_tool_routes(script_mod):
    """read_paper 工具被「读论文」类消息触发。"""
    from laap.agi.tool_router import build_tool_calls

    msg = "帮我读一下 Mapping the Mind of a Large Language Model 这篇论文的全文"
    routed = build_tool_calls(msg, script_mod.READ_TOOLS)
    assert routed is not None
    assert routed["tool_calls"][0]["function"]["name"] == "read_paper"


def test_read_paper_not_triggered_by_stock_question(script_mod):
    from laap.agi.tool_router import build_tool_calls

    assert build_tool_calls("查一下茅台股价", script_mod.READ_TOOLS) is None


def test_fulltext_file_readable(script_mod):
    """脚本读取的论文全文文件存在且可读。"""
    fulltext = open(script_mod.MD_PATH, encoding="utf-8").read()
    assert len(fulltext) > 5000
    assert "Mapping the Mind" in fulltext
