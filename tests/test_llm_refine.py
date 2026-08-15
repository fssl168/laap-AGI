# -*- coding: utf-8 -*-
"""阶段 3.3 LLM 参数微调适配器测试。

覆盖:
  - build_refine_prompt / parse_params（普通/代码块/垃圾/空）
  - clamp_params（截断 + 整数取整 + 丢弃未知键）
  - build_llm_refine_fn（None 降级 / 正常微调 / 垃圾输出 / 抛异常容错）

全部用桩 llm_call，不依赖真实 Hermes/网络。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from laap.paper_trading.llm_refine import (
    build_llm_refine_fn,
    build_refine_prompt,
    clamp_params,
    parse_params,
)


# ════════════════════════════════════════════════════════════
# prompt / parse
# ════════════════════════════════════════════════════════════

def test_build_refine_prompt_contains_params():
    p = build_refine_prompt({"fast_ma": 5}, {"score": 0.5}, "ctx")
    assert "fast_ma" in p
    assert "score" in p
    assert "ctx" in p


def test_parse_params_plain_json():
    assert parse_params('{"a": 1}') == {"a": 1}


def test_parse_params_markdown_wrapped():
    assert parse_params('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_params_garbage():
    assert parse_params("not json at all") is None


def test_parse_params_empty():
    assert parse_params("") is None
    assert parse_params("   ") is None
    assert parse_params(None) is None


# ════════════════════════════════════════════════════════════
# clamp
# ════════════════════════════════════════════════════════════

def test_clamp_params_truncates_and_int():
    out = clamp_params({
        "fast_ma": 999,        # int [2,15] → 15
        "slow_ma": 3,          # int [10,60] → 10
        "position_scale": 5.0,  # float [0.1,1.0] → 1.0
        "rsi_period": 1.5,      # int [5,30] → 5
    })
    assert out["fast_ma"] == 15.0 and float(out["fast_ma"]).is_integer()
    assert out["slow_ma"] == 10.0
    assert out["position_scale"] == 1.0
    assert out["rsi_period"] == 5.0


def test_clamp_params_drops_unknown_keys():
    out = clamp_params({"fast_ma": 5, "not_a_param": 999})
    assert "not_a_param" not in out
    assert out["fast_ma"] == 5.0


# ════════════════════════════════════════════════════════════
# build_llm_refine_fn
# ════════════════════════════════════════════════════════════

def test_build_llm_refine_fn_none():
    assert build_llm_refine_fn(None) is None


def test_build_llm_refine_fn_calls_and_clamps():
    def fake_llm(prompt, system="", max_tokens=500):
        return {"text": '{"fast_ma": 7, "slow_ma": 40}'}

    fn = build_llm_refine_fn(fake_llm)
    out = fn({"fast_ma": 5, "slow_ma": 20}, {"score": 0.5}, "ctx")
    assert out == {"fast_ma": 7.0, "slow_ma": 40.0}


def test_build_llm_refine_fn_bad_output():
    def fake_llm(prompt, system="", max_tokens=500):
        return {"text": "抱歉，我无法提供参数"}

    fn = build_llm_refine_fn(fake_llm)
    assert fn({"fast_ma": 5}, {"score": 0.5}, "ctx") is None


def test_build_llm_refine_fn_raises():
    def fake_llm(*a, **k):
        raise RuntimeError("boom")

    fn = build_llm_refine_fn(fake_llm)
    assert fn({"fast_ma": 5}, {"score": 0.5}, "ctx") is None


def test_build_llm_refine_fn_str_result():
    def fake_llm(prompt, system="", max_tokens=500):
        return '{"fast_ma": 8}'

    fn = build_llm_refine_fn(fake_llm)
    out = fn({"fast_ma": 5}, {"score": 0.5}, "ctx")
    assert out == {"fast_ma": 8.0}
