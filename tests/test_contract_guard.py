"""T-D 契约回归加固：锁定升级计划"不可破坏契约"红线。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


def test_strategy_params_keys_stable():
    """STRATEGY_PARAMS 键集固定（14 键，AST 提取/进化/序列化依赖）。"""
    from laap.paper_trading.strategy import STRATEGY_PARAMS
    from laap.paper_trading.param_evolver import PARAM_SPACE
    assert set(STRATEGY_PARAMS) == set(PARAM_SPACE)
    assert len(STRATEGY_PARAMS) == 14


def test_params_code_roundtrip_contract():
    """params_to_code → extract_strategy_params 完整往返（14 键不丢）。"""
    from laap.paper_trading.param_extractor import (
        params_to_code, extract_strategy_params)
    from laap.paper_trading.strategy import STRATEGY_PARAMS
    params = dict(STRATEGY_PARAMS, fast_ma=7, slow_ma=30, position_scale=0.4)
    code = params_to_code("HEADER\n", params)
    assert extract_strategy_params(code) == params


def test_evaluate_signal_default_multi_factor_unchanged():
    """evaluate_signal 不传 strategy == 显式 multi_factor（默认行为不变）。"""
    from laap.paper_trading.backtest_runner import BacktestRunner
    closes = [100.0 + i * 0.3 + ((i * 5) % 7 - 3) * 0.2 for i in range(60)]
    r = BacktestRunner()
    a1, _ = r.evaluate_signal(closes, {}, position_held=False)
    a2, _ = r.evaluate_signal(closes, {}, position_held=False,
                              strategy="multi_factor")
    assert a1 == a2


def test_run_daily_cycle_signature_backward_compatible(tmp_path):
    """run_daily_cycle(symbols, params, ohlcv_map) 三参旧调用不破坏（默认 multi_factor）。"""
    from laap.agi.unified_memory import UnifiedMemory
    from laap.paper_trading.db import PaperDB
    from laap.paper_trading.paper_service import PaperClosedLoop
    from laap.paper_trading import strategy

    class _LiveStub:
        def get_price(self, symbol, ts=None):
            return 100.0, {"source": "test", "used_fallback": False}

    loop = PaperClosedLoop(PaperDB(db_path=str(tmp_path / "pt.db")),
                           _LiveStub(), UnifiedMemory(),
                           initial_cash=1_000_000.0, enforce_t1=False)
    closes = [100.0 + i * 1.0 for i in range(20)] + \
             [120.0 - i * 1.5 for i in range(8)] + \
             [108.0 + i * 0.55 for i in range(15)]
    ohlcv = [(c - 0.1, c, c + 0.2, c - 0.2, 100_000.0) for c in closes]
    # 旧三参调用
    result = loop.run_daily_cycle(["600519"], dict(strategy.STRATEGY_PARAMS),
                                  ohlcv_map={"600519": ohlcv})
    assert "signals" in result and "net_value" in result
    assert "data_quality" in result  # T1 新增字段向后兼容


def test_strategy_templates_do_not_break_multi_factor():
    """模板路由不破坏默认 multi_factor（list_templates 首项为 multi_factor）。"""
    from laap.paper_trading.strategy_templates import (
        list_templates, evaluate_strategy, get_template)
    assert list_templates()[0] == "multi_factor"
    # 未知策略 → hold（fail-closed）
    action, reason = evaluate_strategy("nonexistent", [100.0] * 30)
    assert action == "hold"
    assert "unknown" in reason
    # 7 个模板可注册
    assert len(list_templates()) == 8  # multi_factor + 7


def test_list_strategy_meta_complete():
    """策略映射完整：multi_factor builtin + 7 模板，均含元数据字段。"""
    from laap.paper_trading.strategy_templates import list_strategy_meta
    meta = list_strategy_meta()
    assert len(meta) == 8
    names = {m["name"] for m in meta}
    assert "multi_factor" in names
    assert {"mean_reversion", "golden_cross", "rsi_reversal", "boll_breakout",
            "macd_momentum", "volume_breakout", "shrink_pullback"} <= names
    # 映射字段完整
    for m in meta:
        assert all(k in m for k in ("name", "display_name", "description", "type"))
    # multi_factor 是内建，模板是 template
    mf = next(m for m in meta if m["name"] == "multi_factor")
    assert mf["type"] == "builtin"
    tpl = next(m for m in meta if m["name"] == "golden_cross")
    assert tpl["type"] == "template"
    assert tpl["display_name"] and tpl["description"]
