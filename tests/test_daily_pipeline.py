"""T3 每日管线测试：QuantDailyPipeline + QuantDailyScheduler。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.daily_pipeline import (
    QuantDailyPipeline,
    QuantDailyScheduler,
)


class _FakeQE:
    def __init__(self, gate_ok=True):
        self._gate_ok = gate_ok

    def evolve_params(self, method="random", **kw):
        return {"method": method, "best_params": {"fast_ma": 6},
                "best_train": {"score": 0.7},
                "gate": {"ok": self._gate_ok, "reason": "ok"}}

    def apply_params_to_code(self, params, rationale="", method="",
                             self_review=True):
        return {"status": "awaiting_approval", "mutation_id": "m1"}


class _FakeLoop:
    def __init__(self):
        self.calls = []

    def run_daily_cycle(self, symbols, params, ohlcv_map=None, strategy=None):
        self.calls.append(params)
        return {"signals": [{"action": "hold"}],
                "net_value": {"total": 1_000_000.0},
                "data_quality": {"600519": {"source": "real",
                                            "used_fallback": False}}}


def test_pipeline_run_structure():
    qe, loop = _FakeQE(), _FakeLoop()
    pipe = QuantDailyPipeline(qe, loop, symbols=["600519"])
    result = pipe.run(method="random", n_samples=10)
    assert "evolve" in result and "apply" in result and "daily_cycle" in result
    assert result["summary"]["apply_status"] == "awaiting_approval"
    assert result["summary"]["params_source"] == "searched"
    assert result["daily_cycle"]["signals"][0]["action"] == "hold"


def test_pipeline_uses_searched_params():
    qe, loop = _FakeQE(), _FakeLoop()
    pipe = QuantDailyPipeline(qe, loop, symbols=["600519"])
    pipe.run()
    # apply 成功 → daily_cycle 用搜索参数（fast_ma=6）
    assert loop.calls[-1]["fast_ma"] == 6


def test_pipeline_no_search_result():
    class _NoSearch(_FakeQE):
        def evolve_params(self, method="random", **kw):
            return {"method": method, "best_params": None, "best_train": {},
                    "gate": {"ok": False, "reason": "no candidates"}}
    loop = _FakeLoop()
    pipe = QuantDailyPipeline(_NoSearch(), loop, symbols=["600519"])
    result = pipe.run()
    assert result["summary"]["apply_status"] == "no_search_result"
    assert result["summary"]["params_source"] == "current"
    # daily_cycle 仍跑（用当前 STRATEGY_PARAMS）
    assert loop.calls[-1].get("fast_ma")  # 有当前参数


def test_scheduler_start_stop():
    pipe = QuantDailyPipeline(_FakeQE(), _FakeLoop(), symbols=["600519"])
    s = QuantDailyScheduler(pipe, interval_seconds=3600)
    assert s.start() is True
    assert s.is_running is True
    assert s.start() is False  # 幂等
    assert s.stop() is True
    assert s.is_running is False
    assert s.stop() is False  # 幂等


def test_scheduler_tick():
    pipe = QuantDailyPipeline(_FakeQE(), _FakeLoop(), symbols=["600519"])
    s = QuantDailyScheduler(pipe, interval_seconds=3600)
    result = s.tick()
    assert s.run_count == 1
    assert result["summary"]["apply_status"] == "awaiting_approval"
    assert s.stats()["run_count"] == 1
