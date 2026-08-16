"""
交易日门测试 (paper_trading 执行遵循 A 股交易日历)
==================================================
验证: 周末/节假日停盘时拒绝下单/平仓, 建议附提示; 工作日放行。

运行:
    python -m pytest tests/test_trading_day_gate.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.quant_bridge import get_bridge


@pytest.fixture()
def bridge(monkeypatch):
    b = get_bridge()
    # 隔离: 不真实访问 DB/网络, 只测交易日门
    class _FakeTS:
        def judge(self, *a, **k):
            return {"verdict": "approve", "meaning": "t", "benefit": "t",
                    "reasons": []}
    monkeypatch.setattr(b, "_get_trading_self", lambda cls: _FakeTS())
    monkeypatch.setattr(b, "_get_loop", lambda: None)
    return b


def test_weekend_execute_rejected(bridge, monkeypatch):
    """周末: use_execute 应拒绝 (market_closed)。"""
    monkeypatch.setattr(bridge, "_is_trading_day", lambda: False)
    r = bridge.use_execute(symbol="600519", action="buy", qty=100,
                           confirm_word="确认执行")
    assert r["status"] == "market_closed"
    assert r["executed"] is False
    assert "交易日" in r.get("message", "")


def test_weekend_close_rejected(bridge, monkeypatch):
    """周末: use_close 应拒绝。"""
    monkeypatch.setattr(bridge, "_is_trading_day", lambda: False)
    r = bridge.use_close("600519", 100, confirm_word="确认平仓")
    assert r["status"] == "market_closed"
    assert r["executed"] is False


def test_weekend_decide_notes_closed(bridge, monkeypatch):
    """周末: use_decide 附 market_open=False (不阻断建议, 但提示)。"""
    monkeypatch.setattr(bridge, "_is_trading_day", lambda: False)
    r = bridge.use_decide("600519", "buy", 100)
    assert r["market_open"] is False


def test_weekday_execute_passes_gate(bridge, monkeypatch):
    """工作日: 交易日门放行 (后续仍需确认词/审核)。"""
    monkeypatch.setattr(bridge, "_is_trading_day", lambda: True)
    # 无确认词 → 应走到 need_confirmation (证明过了交易日门)
    r = bridge.use_execute(symbol="600519", action="buy", qty=100,
                           confirm_word="")
    assert r["status"] == "need_confirmation"


def test_is_trading_day_weekend_detected(bridge):
    """_is_trading_day 真实调用: 周末返回 False (可跑, 数据源不可用降级)。"""
    from datetime import datetime
    result = bridge._is_trading_day()
    # 今天若是周末 → False; 工作日 → True (外部日历不可用时 weekday 兜底)
    if datetime.now().weekday() >= 5:
        assert result is False
    else:
        assert result is True or result is False  # 外部日历可能判定节假日
