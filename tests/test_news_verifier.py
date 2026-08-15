# -*- coding: utf-8 -*-
"""news_verifier.py 判定层测试。"""
import pytest

from laap.paper_trading.news_verifier import (
    NewsItem, StockProfile, TechState, NewsVerdict, AggregatedNewsDecision,
    compute_tech_state, build_verify_prompt, parse_verdict, verify_news,
    aggregate_verdicts)


def _tech(rsi=50.0, limit_up=False, limit_down=False, suspended=False):
    return TechState(symbol="600519", rsi=rsi, close=1400.0,
                     ma20=1350.0, prev_close=1390.0, change_pct=0.007,
                     limit_up=limit_up, limit_down=limit_down, suspended=suspended)


def _item(title="茅台提价", content="核心产品提价 10%，业绩预增"):
    return NewsItem("600519", title, content, source="证券时报",
                    published_at="2026-08-15 09:00")


def _stub_llm(verdict="genuine_bullish", confidence=0.8, **extra):
    def _llm(prompt, system="", max_tokens=800):
        d = {"verdict": verdict, "confidence": confidence,
             "reasons": ["看涨"], "impact": "短期利好"}
        d.update(extra)
        return d
    return _llm


def test_verify_news_genuine_bullish():
    v = verify_news(_item(), None, _tech(), llm_call=_stub_llm())
    assert v.verdict == "genuine_bullish"
    assert v.confidence == pytest.approx(0.8)
    assert v.trade_action == "buy"
    assert v.used_fallback is False


def test_verify_news_low_confidence_not_dispatch():
    v = verify_news(_item(), None, _tech(),
                    llm_call=_stub_llm(confidence=0.5))
    agg = aggregate_verdicts([v])
    assert v.confidence == pytest.approx(0.5)
    assert agg.dispatch is False  # 低于 0.7 → 静默


def test_verify_news_rsi_overbought_discount():
    v = verify_news(_item(), None, _tech(rsi=75.0), llm_call=_stub_llm(confidence=0.8))
    assert v.confidence == pytest.approx(0.64)  # 0.8*0.8
    assert "追高" in "".join(v.reasons)


def test_verify_news_rsi_oversold_bonus():
    v = verify_news(_item(), None, _tech(rsi=25.0), llm_call=_stub_llm(confidence=0.7))
    assert v.confidence == pytest.approx(0.75)  # 0.7+0.05
    assert "超卖" in "".join(v.reasons)


def test_verify_news_limit_up_wait():
    v = verify_news(_item(), None, _tech(limit_up=True),
                    llm_call=_stub_llm(confidence=0.9))
    assert v.verdict == "genuine_bullish"
    assert v.trade_action == "wait"
    agg = aggregate_verdicts([v])
    assert agg.dispatch is False  # wait 不开新仓


def test_verify_news_llm_failure_heuristic_fallback():
    def _boom(prompt, system="", max_tokens=800):
        raise RuntimeError("llm down")
    v = verify_news(_item(), None, _tech(), llm_call=_boom)
    assert v.used_fallback is True
    assert v.confidence <= 0.5  # 不自动放行


def test_verify_news_markdown_wrapped_json():
    def _llm(prompt, system="", max_tokens=800):
        return "```json\n{\"verdict\": \"neutral\", \"confidence\": 0.4, \"reasons\": [], \"impact\": \"\"}\n```"
    v = verify_news(_item(), None, _tech(), llm_call=_llm)
    assert v.verdict == "neutral"
    assert v.confidence == pytest.approx(0.4)


def test_parse_verdict_bad_input():
    assert parse_verdict(None) is None
    assert parse_verdict("not json") is None
    assert parse_verdict({"verdict": "invalid"}) is None
    assert parse_verdict({"verdict": "neutral", "confidence": "0.5",
                          "reasons": [], "impact": ""}) is not None


def test_aggregate_verdicts_no_bullish():
    v = NewsVerdict("n1", verdict="neutral", confidence=0.6)
    agg = aggregate_verdicts([v])
    assert agg.dispatch is False


def test_aggregate_verdicts_best_bullish():
    v1 = NewsVerdict("n1", verdict="genuine_bullish", confidence=0.6,
                     trade_action="buy")
    v2 = NewsVerdict("n2", verdict="genuine_bullish", confidence=0.9,
                     trade_action="buy")
    agg = aggregate_verdicts([v1, v2])
    assert agg.dispatch is True
    assert agg.top_news_ids == ["n2"]


def test_compute_tech_state_from_ohlcv():
    # 温和上升 OHLCV → rsi/ma20 就绪
    ohlcv = []
    c = 100.0
    for i in range(60):
        c = c * 1.002
        ohlcv.append((c * 0.999, c, c * 1.001, c * 0.998, 1_000_000.0 + i))
    ts = compute_tech_state("600519", ohlcv=ohlcv)
    assert ts.close == pytest.approx(c)
    assert ts.rsi is not None
    assert ts.ma20 is not None
    assert ts.change_pct is not None
