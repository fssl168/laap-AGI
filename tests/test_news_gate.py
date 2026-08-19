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


class _LiveStub:
    """used_fallback=False 的行情源（测试成交用，绕过 fail-closed 降级拦截）。"""
    def get_price(self, symbol, ts=None):
        return 100.0, {"source": "test", "used_fallback": False}


class TestIntegration:
    """与 run_daily_cycle 的 news_gate 可选参数集成。"""

    def _loop(self):
        import tempfile, os
        from laap.paper_trading.paper_service import PaperClosedLoop
        from laap.paper_trading.db import PaperDB
        tmp = os.path.join(tempfile.gettempdir(), "pt_news_gate.db")
        if os.path.exists(tmp):
            os.remove(tmp)
        db = PaperDB(db_path=tmp)
        return PaperClosedLoop(db=db, market=_LiveStub(), memory=None,
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
                ["600519"], {"fast_ma": 5, "slow_ma": 10, "position_scale": 0.05},
                ohlcv_map={"600519": ohlcv},
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
            # 注：position_scale 设小以兼容 R2 单票≤10%（默认 0.5 会被 R2 拒，
            #     此处测的是 news_gate 逻辑而非风控门）
            r = loop.run_daily_cycle(
                ["600519"], {"fast_ma": 5, "slow_ma": 10, "position_scale": 0.05},
                ohlcv_map={"600519": ohlcv},
                strategy="golden_cross")
            sig = r["signals"][0]
            assert sig["action"] == "buy"
            assert "news_gate" not in sig
        finally:
            import os
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_news_does_not_block_sell(self, monkeypatch):
        """文档契约：卖出/风控不被新闻拖延（news 只作用 buy）。"""
        # 交易时段桩：run_daily_cycle 内层 decide_and_trade 有时间门，
        # 非交易时段（如 16:xx 沙箱时间）下单会被拒——打 14:00 桩。
        import laap.paper_trading.paper_service as ps
        import datetime as _dt
        class _N:
            hour = 14
            minute = 0
            def strftime(self, f):
                return "14:00"
        class _FakeDT:
            @staticmethod
            def now():
                return _N()
        monkeypatch.setattr(ps, "datetime", _FakeDT)
        loop, tmp = self._loop()
        try:
            from laap.paper_trading import strategy
            params = dict(strategy.STRATEGY_PARAMS)
            params["position_scale"] = 0.05  # 兼容 R2 单票≤10%（默认 0.5 会被拒）
            # 温和上涨触发 multi_factor buy
            closes_up = [100.0 + i * 1.0 for i in range(20)] + \
                        [120.0 - i * 1.5 for i in range(8)] + \
                        [108.0 + i * 0.55 for i in range(15)]
            ohlcv_up = [(c - 0.1, c, c + 0.2, c - 0.2,
                         300_000.0 if i == len(closes_up) - 1 else 100_000.0)
                        for i, c in enumerate(closes_up)]
            r_buy = loop.run_daily_cycle(
                ["600519"], params, ohlcv_map={"600519": ohlcv_up})
            assert r_buy["signals"][0]["action"] == "buy"
            assert len(loop.ledger.open_positions()) == 1

            # 单调下跌（trend_down）+ bearish news → sell 照常平仓（news 不阻碍）
            closes_down = [200.0 - i * 2.0 for i in range(30)]
            ohlcv_down = [(c - 0.5, c, c + 0.5, c - 0.8, 100_000.0)
                          for c in closes_down]
            gate = lambda sym: [{"verdict": "bearish", "confidence": 0.9}]
            r_sell = loop.run_daily_cycle(
                ["600519"], params, ohlcv_map={"600519": ohlcv_down},
                news_gate=gate)
            assert r_sell["signals"][0]["action"] == "sell"
            assert len(loop.ledger.open_positions()) == 0
        finally:
            import os
            if os.path.exists(tmp):
                os.remove(tmp)


class TestGetVerdictsForSymbol:
    """get_verdicts_for_symbol：news_gate 的 DB 输入源（2026-08-18 补）。

    回归保护：daily_pipeline/api 的 _news_gate_fn 依赖此函数；此前缺失导致
    news_gate 永远拿到空列表（ImportError 被吞），新闻门形同虚设。
    """

    def test_returns_verdicts_for_symbol(self, tmp_path, monkeypatch):
        from laap.paper_trading.db import PaperDB
        import laap.paper_trading.news_verifier as nv
        import time as _t

        db = PaperDB(db_path=str(tmp_path / "test_gate.db"))
        conn = db.conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS news_verdicts (
                news_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                verdict TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.0,
                reasons_json TEXT DEFAULT '[]',
                impact TEXT DEFAULT '',
                rsi REAL,
                trade_action TEXT DEFAULT '',
                dispatched INTEGER NOT NULL DEFAULT 0,
                decision_id TEXT DEFAULT '',
                used_fallback INTEGER NOT NULL DEFAULT 0,
                ts REAL NOT NULL,
                PRIMARY KEY (news_id, ts)
            );
        """)
        now = _t.time()
        conn.execute(
            "INSERT INTO news_verdicts (news_id, symbol, verdict, confidence,"
            " reasons_json, ts) VALUES (?,?,?,?,?,?)",
            ("n1", "000410", "bearish", 0.95, '[\"亏损\"]', now))
        conn.execute(
            "INSERT INTO news_verdicts (news_id, symbol, verdict, confidence,"
            " reasons_json, ts) VALUES (?,?,?,?,?,?)",
            ("n2", "000410.SZ", "genuine_bullish", 0.9, '[\"补助\"]', now))
        conn.execute(
            "INSERT INTO news_verdicts (news_id, symbol, verdict, confidence,"
            " reasons_json, ts) VALUES (?,?,?,?,?,?)",
            ("n3", "600519", "neutral", 0.5, '[]', now))
        conn.commit()
        conn.close()

        # 000410 应匹配 000410 + 000410.SZ（不含 600519）
        verdicts = nv.get_verdicts_for_symbol("000410", db=db)
        symbols = {v.news_id for v in verdicts}
        assert "n1" in symbols and "n2" in symbols
        assert "n3" not in symbols

        # 反向：传 000410.SZ 同样匹配
        verdicts2 = nv.get_verdicts_for_symbol("000410.SZ", db=db)
        assert {v.news_id for v in verdicts2} == {"n1", "n2"}

        # news_gate 集成：bearish 存在 → 否决 buy
        from laap.paper_trading.news_gate import apply_news_gate, GATE_VETOED
        action, _reason, gate = apply_news_gate("buy", verdicts)
        assert action == "hold" and gate == GATE_VETOED

    def test_db_failure_returns_empty(self, monkeypatch):
        """查询失败 → []（fail-closed：不因新闻源故障瘫痪量价）。"""
        import laap.paper_trading.news_verifier as nv

        class _BadDB:
            def conn(self):
                raise RuntimeError("db down")

        verdicts = nv.get_verdicts_for_symbol("000410", db=_BadDB())
        assert verdicts == []
