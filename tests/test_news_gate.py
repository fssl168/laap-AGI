"""新闻×量价两轨组合门测试（news_gate）。

确定性验证 fail-closed 语义：
  1. bearish/fake_news 否决量价 buy
  2. genuine_bullish 高置信确认 buy（标注 confirmed，不放大）
  3. neutral / 无新闻 / 降级 → 不干扰量价
  4. sell/hold 不受 news 门影响
  5. confirm_required 模式：buy 需利好确认
"""

from __future__ import annotations

import pytest

from laap.paper_trading.news_gate import (
    apply_news_gate, GATE_CONFIRMED, GATE_VETOED, GATE_NEUTRAL,
    GATE_NOT_APPLICABLE, filter_news_before,
)


def _v(verdict, confidence=0.8):
    return {"verdict": verdict, "confidence": confidence}


class TestVetoOnly:
    def test_bearish_vetoes_buy(self):
        action, reason, gate = apply_news_gate("buy", [_v("genuine_bullish", 0.8), _v("bearish", 0.9)])
        assert action == "hold" and gate == GATE_VETOED
        assert "VETO" in reason

    def test_fake_news_vetoes_buy(self):
        action, reason, gate = apply_news_gate("buy", [_v("fake_news", 0.7)])
        assert action == "hold" and gate == GATE_VETOED

    def test_bullish_confirms_buy(self):
        action, reason, gate = apply_news_gate("buy", [_v("genuine_bullish", 0.85)])
        assert action == "buy" and gate == GATE_CONFIRMED
        assert "CONFIRMED" in reason

    def test_low_confidence_bullish_neutral(self):
        action, _r, gate = apply_news_gate("buy", [_v("genuine_bullish", 0.4)])
        assert action == "buy" and gate == GATE_NEUTRAL  # 低置信不确认也不否决

    def test_neutral_does_not_interfere(self):
        action, _r, gate = apply_news_gate("buy", [_v("neutral", 0.5)])
        assert action == "buy" and gate == GATE_NEUTRAL

    def test_no_news_does_not_interfere(self):
        action, _r, gate = apply_news_gate("buy", None)
        assert action == "buy" and gate == GATE_NEUTRAL

    def test_empty_verdicts_does_not_interfere(self):
        action, _r, gate = apply_news_gate("buy", [])
        assert action == "buy" and gate == GATE_NEUTRAL


class TestNonBuy:
    def test_sell_not_affected(self):
        action, _r, gate = apply_news_gate("sell", [_v("bearish", 0.9)])
        assert action == "sell" and gate == GATE_NOT_APPLICABLE

    def test_hold_not_affected(self):
        action, _r, gate = apply_news_gate("hold", [_v("bearish", 0.9)])
        assert action == "hold" and gate == GATE_NOT_APPLICABLE


class TestConfirmRequired:
    def test_buy_requires_confirmation(self):
        action, _r, gate = apply_news_gate(
            "buy", [_v("neutral", 0.5)], mode="confirm_required")
        assert action == "hold" and gate == GATE_NEUTRAL

    def test_buy_confirmed_by_high_confidence(self):
        action, _r, gate = apply_news_gate(
            "buy", [_v("genuine_bullish", 0.85)], mode="confirm_required")
        assert action == "buy" and gate == GATE_CONFIRMED

    def test_bearish_still_vetoes(self):
        action, _r, gate = apply_news_gate(
            "buy", [_v("bearish", 0.9)], mode="confirm_required")
        assert action == "hold" and gate == GATE_VETOED

    def test_custom_min_confidence(self):
        # 阈值提高后 0.7 不再确认
        action, _r, gate = apply_news_gate(
            "buy", [_v("genuine_bullish", 0.7)], mode="confirm_required",
            min_confidence=0.8)
        assert action == "hold" and gate == GATE_NEUTRAL


class TestPublishTimeAlignment:
    """防未来函数：回测必须按 publish_time 对齐，禁止用抓取/当前时间。"""

    def test_keeps_news_published_before_cutoff(self):
        news = [
            {"verdict": "bearish", "confidence": 0.9,
             "published_at": "2026-08-10 09:30:00"},
            {"verdict": "neutral", "confidence": 0.5,
             "published_at": "2026-08-12 15:00:00"},
        ]
        out = filter_news_before(news, "2026-08-13")
        assert len(out) == 2

    def test_excludes_news_published_after_cutoff(self):
        # 未来才发布的新闻不能用于过去 bar 的信号（防未来函数核心）
        news = [
            {"verdict": "bearish", "confidence": 0.9,
             "published_at": "2026-08-20 09:30:00"},
            {"verdict": "neutral", "confidence": 0.5,
             "published_at": "2026-08-15 10:00:00"},
        ]
        out = filter_news_before(news, "2026-08-14")
        assert len(out) == 0  # 两条都晚于 cutoff

    def test_excludes_missing_or_unparseable_time(self):
        # 时间缺失/无法解析 → fail-closed 排除（不能用于回测）
        news = [
            {"verdict": "bearish", "confidence": 0.9, "published_at": ""},
            {"verdict": "bearish", "confidence": 0.9, "published_at": "not-a-date"},
            {"verdict": "neutral", "confidence": 0.5,
             "published_at": "2026-08-10 09:30:00"},
        ]
        out = filter_news_before(news, "2026-08-14")
        assert len(out) == 1  # 仅保留时间有效且在 cutoff 前的一条

    def test_numeric_timestamp_cutoff(self):
        news = [
            {"verdict": "bearish", "confidence": 0.9,
             "published_at": "2026-08-10 00:00:00"},
        ]
        # 2026-08-11 00:00:00 的 unix 时间戳
        import time
        from datetime import datetime
        ts = datetime(2026, 8, 11).timestamp()
        out = filter_news_before(news, ts)
        assert len(out) == 1

    def test_verdict_carries_published_at_from_item(self):
        # verify_news 构造的 NewsVerdict 携带 published_at（端到端契约）
        from laap.paper_trading.news_intel import NewsItem
        from laap.paper_trading.news_verifier import verify_news, TechState
        item = NewsItem(symbol="600519", title="利好公告", content="业绩大增",
                        source="test", published_at="2026-08-10 09:00:00")
        v = verify_news(item, profile=None,
                        tech_state=TechState(symbol="600519", close=100.0))
        assert v.published_at == "2026-08-10 09:00:00"
        # 回测时间轴：cutoff=当日零点，09:00 发布的新闻当日零点尚不可用 → 排除
        assert len(filter_news_before([v], "2026-08-09")) == 0
        assert len(filter_news_before([v], "2026-08-10")) == 0
        # cutoff=次日 → 可用
        assert len(filter_news_before([v], "2026-08-11")) == 1


class TestIntegration:
    """与 run_daily_cycle 的 news_gate 可选参数集成。"""

    def _loop(self):
        import tempfile, os
        from laap.paper_trading.paper_service import PaperClosedLoop
        from laap.paper_trading.db import PaperDB
        from laap.paper_trading.market_source import StubMarketSource
        tmp = os.path.join(tempfile.gettempdir(), "pt_news_gate.db")
        if os.path.exists(tmp):
            os.remove(tmp)
        db = PaperDB(db_path=tmp)
        return PaperClosedLoop(db=db, market=StubMarketSource(), memory=None,
                               initial_cash=1_000_000.0, enforce_t1=False), tmp

    def test_bearish_gate_vetoes_buy(self):
        loop, tmp = self._loop()
        try:
            # 金叉触发 buy，但新闻 bearish → 否决
            closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.8, 11.0,
                      11.4, 11.9, 12.5, 13.2]
            ohlcv = [(closes[i-1] if i else c, c, c*1.01, c*0.99, 1000.0)
                     for i, c in enumerate(closes)]
            gate = lambda sym: [{"verdict": "bearish", "confidence": 0.9}]
            r = loop.run_daily_cycle(
                ["600519"], {"fast_ma": 5, "slow_ma": 10}, ohlcv_map={"600519": ohlcv},
                strategy="golden_cross", news_gate=gate)
            sig = r["signals"][0]
            assert sig["action"] == "hold"
            assert sig["news_gate"] == "vetoed"
        finally:
            import os
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_bullish_gate_confirms_buy(self):
        loop, tmp = self._loop()
        try:
            closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.8, 11.0,
                      11.4, 11.9, 12.5, 13.2]
            ohlcv = [(closes[i-1] if i else c, c, c*1.01, c*0.99, 1000.0)
                     for i, c in enumerate(closes)]
            gate = lambda sym: [{"verdict": "genuine_bullish", "confidence": 0.9}]
            r = loop.run_daily_cycle(
                ["600519"], {"fast_ma": 5, "slow_ma": 10}, ohlcv_map={"600519": ohlcv},
                strategy="golden_cross", news_gate=gate)
            sig = r["signals"][0]
            assert sig["action"] == "buy"
            assert sig["news_gate"] == "confirmed"
        finally:
            import os
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_no_gate_default_unchanged(self):
        loop, tmp = self._loop()
        try:
            closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.8, 11.0,
                      11.4, 11.9, 12.5, 13.2]
            ohlcv = [(closes[i-1] if i else c, c, c*1.01, c*0.99, 1000.0)
                     for i, c in enumerate(closes)]
            # 不传 news_gate：行为不变（buy 照常，无 news_gate 字段）
            r = loop.run_daily_cycle(
                ["600519"], {"fast_ma": 5, "slow_ma": 10}, ohlcv_map={"600519": ohlcv},
                strategy="golden_cross")
            sig = r["signals"][0]
            assert sig["action"] == "buy"
            assert "news_gate" not in sig
        finally:
            import os
            if os.path.exists(tmp):
                os.remove(tmp)
