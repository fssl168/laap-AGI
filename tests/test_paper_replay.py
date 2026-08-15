# -*- coding: utf-8 -*-
"""Step 2 paper 回放 + TradingSelf A/B 引擎测试。

覆盖:
  - ohlcv_from_closes 五元组结构
  - 无 TradingSelf 回放：totals/metrics/交易数
  - 有 TradingSelf 回放：verdicts 分布 + phantom_stats 结构
  - 保守人格预设 → 买单被弃权 → 幽灵仓被记录并平仓
  - 可复现性：同参数两次回放 totals 一致

全部用合成数据（确定性），不依赖真实 K 线/网络。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.paper_replay import PaperReplay, ohlcv_from_closes


@pytest.fixture
def synth_closes():
    # 震荡趋势 + 噪声（确定性），长度 200 —— 保证 RSI 非恒超买、buy 信号可触发
    import math
    return [100.0 + i * 0.25 + 6.0 * math.sin(i / 4.0) for i in range(200)]


@pytest.fixture
def memory():
    from laap.agi.unified_memory import UnifiedMemory
    return UnifiedMemory()


# ════════════════════════════════════════════════════════════
# 工具
# ════════════════════════════════════════════════════════════

def test_ohlcv_from_closes_structure(synth_closes):
    ohlcv = ohlcv_from_closes(synth_closes)
    assert len(ohlcv) == len(synth_closes)
    for o, c, h, l, v in ohlcv[:5]:
        assert h >= c >= l > 0
        assert v > 0


# ════════════════════════════════════════════════════════════
# Arm A：无 TradingSelf
# ════════════════════════════════════════════════════════════

def test_replay_no_self_returns_metrics(synth_closes, memory):
    r = PaperReplay(start_day=60).replay("T1", synth_closes, memory=memory)
    assert r["symbol"] == "T1"
    assert len(r["totals"]) == len(synth_closes) - 60
    assert "cumulative_return" in r["metrics"]
    assert "max_drawdown" in r["metrics"]
    assert r["n_trades"] >= 0
    assert r["verdicts"] == {"approve": 0, "abstain": 0, "reject": 0}


def test_replay_deterministic(synth_closes, memory):
    r1 = PaperReplay(start_day=60).replay("T1", synth_closes, memory=memory)
    r2 = PaperReplay(start_day=60).replay("T1", synth_closes, memory=memory)
    assert r1["totals"] == r2["totals"]
    assert r1["metrics"] == r2["metrics"]


# ════════════════════════════════════════════════════════════
# Arm B：有 TradingSelf
# ════════════════════════════════════════════════════════════

def test_replay_with_self_returns_verdicts(synth_closes, memory):
    from laap.paper_trading.trading_self import TradingSelf
    ts = TradingSelf(preset="aggressive", memory=memory)
    r = PaperReplay(start_day=60).replay("T1", synth_closes,
                                         trading_self=ts, memory=memory)
    assert set(r["verdicts"].keys()) == {"approve", "abstain", "reject"}
    assert sum(r["verdicts"].values()) >= r["n_trades"] // 2  # 至少覆盖部分信号
    assert "count" in r["phantom_stats"]
    assert r["phantom_stats"]["closed"] == r["phantom_stats"]["count"]


def test_conservative_abstains_buy_and_tracks_phantom(synth_closes, memory):
    """决策 3：极保守人格（仓位上限 0.42）→ 0.5 仓位买单被弃权 → 幽灵仓被记录并平仓。"""
    from laap.paper_trading.trading_self import PERSONA_PRESETS, TradingSelf
    assert PERSONA_PRESETS["conservative"]["loyalty"] == 0.9
    # 极保守人格：position_scale_max ≈ 0.42，恒低于策略 0.5 仓位 → 确定性弃权
    ts = TradingSelf(personality={"traits": {"curiosity": 0.0, "loyalty": 1.0,
                                             "playfulness": 0.0}}, memory=memory)
    ident = ts.trading_identity()
    assert ident["position_scale_max"] < 0.5

    r = PaperReplay(start_day=60).replay("T1", synth_closes,
                                         trading_self=ts, memory=memory)
    # 策略确实产生过买单（judge 被调用）
    assert sum(r["verdicts"].values()) > 0
    # 买单被弃权
    assert r["verdicts"]["abstain"] > 0
    # 幽灵仓存在且全部平仓
    assert r["phantom_stats"]["count"] > 0
    assert r["phantom_stats"]["closed"] == r["phantom_stats"]["count"]


# ════════════════════════════════════════════════════════════
# 人格预设（决策 3）
# ════════════════════════════════════════════════════════════

def test_persona_presets_affect_identity():
    from laap.paper_trading.trading_self import TradingSelf
    cons = TradingSelf(preset="conservative")
    aggr = TradingSelf(preset="aggressive")
    assert cons.trading_identity()["risk_appetite"] < \
        aggr.trading_identity()["risk_appetite"]
    assert cons.trading_identity()["position_scale_max"] < \
        aggr.trading_identity()["position_scale_max"]
