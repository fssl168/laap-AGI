# -*- coding: utf-8 -*-
"""LAAP 全量数据源测试（7 域 × 各源候选）

逐域验证:
  MARKET   行情取价    tx → em → xq → stub (2026-08-17: akshare 移除, em 东财直连)
  KLINE    K线        db → tushare → akshare → synthetic
  NEWS     新闻        eastmoney → sina → cls → tushare → bocha → tavily → minimax
  PROFILE  公司画像    individual_info → em_profile → cninfo
  REPORT   研报        eastmoney → cls → sina
  CALENDAR 交易日历    external → cache → weekday
  LLM      LLM 链路    openai → anspire → urllib → ollama → local → cli

用法:
    python scripts/test_all_data_sources.py              # 全量
    python scripts/test_all_data_sources.py --domain NEWS # 单域
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_market(symbol: str = "600519") -> dict:
    """行情取价：按配置链 (MARKET_SOURCES) 逐个源尝试，报告各自结果。

    2026-08-17 修复: 原硬编码 LiveMarketSource(akshare) —— 与真实服务
    走 resolve_source 配置链不一致; akshare 被东财 WAF 拒后显示 stub 假象。
    现按 source_chain('MARKET') 逐源测试 + resolve_source 主链路验证。
    """
    from laap.paper_trading.data_sources import source_chain
    from laap.paper_trading.market_source import (
        LiveMarketSource, TxMarketSource, EmMarketSource, XqMarketSource, resolve_source)
    out = {}
    chain = source_chain("MARKET")
    out["源链配置"] = chain
    handlers = {
        "akshare": lambda: LiveMarketSource().get_price(symbol),
        "tx": lambda: TxMarketSource().get_price(symbol),
        "em": lambda: EmMarketSource().get_price(symbol),
        "xq": lambda: XqMarketSource().get_price(symbol),
    }
    for s in chain:
        fn = handlers.get(s)
        if fn is None:
            out[f"[{s}]"] = {"ok": False, "error": "未实现"}
            continue
        try:
            price, meta = fn()
            out[f"[{s}]"] = {"ok": True, "price": price, "meta": meta}
        except Exception as e:
            out[f"[{s}]"] = {"ok": False, "error": str(e)[:80]}
    # 主链路: resolve_source(配置链 Composite)
    src = resolve_source(prefer_live=True)
    out["resolve_source"] = type(src).__name__
    try:
        price, meta = src.get_price(symbol)
        out["主链路"] = {"ok": True, "price": price,
                         "meta": {"source": meta.get("source"),
                                  "used_fallback": meta.get("used_fallback")}}
    except Exception as e:
        out["主链路"] = {"ok": False, "error": str(e)[:80]}
    return out


def test_kline(symbol: str = "600519") -> dict:
    """K线：真实数据 + quality 标记。"""
    from laap.paper_trading.kline_source import load_ohlcv
    out = {}
    try:
        ohlcv, quality = load_ohlcv(symbol, days=120, fallback=False,
                                    with_quality=True)
        out["load_ohlcv"] = {
            "ok": len(ohlcv) > 0, "rows": len(ohlcv), "quality": quality,
        }
    except Exception as e:
        out["load_ohlcv"] = {"ok": False, "error": str(e)}
    # 带 fallback 的降级路径
    ohlcv2, q2 = load_ohlcv(symbol, days=120, fallback=True, with_quality=True)
    out["fallback路径"] = {"rows": len(ohlcv2), "quality": q2}
    return out


def test_news(symbol: str = "600519", name: str = "贵州茅台") -> dict:
    """新闻：逐源探测（eastmoney → sina → cls → bocha → tavily）。"""
    from laap.paper_trading.news_intel import fetch_stock_news
    out = {}
    try:
        items, meta = fetch_stock_news(symbol, name=name, max_results=10)
        out["fetch_stock_news"] = {
            "ok": len(items) > 0, "count": len(items), "meta": meta,
        }
        if items:
            first = items[0]
            out["样例"] = {
                "title": (getattr(first, "title", "") or "")[:60],
                "source": getattr(first, "source", "") or "",
            }
    except Exception as e:
        out["fetch_stock_news"] = {"ok": False, "error": str(e)[:200]}
    return out


def test_profile(symbol: str = "600519") -> dict:
    """公司画像。"""
    from laap.paper_trading.news_intel import fetch_stock_profile
    out = {}
    try:
        prof, meta = fetch_stock_profile(symbol)
        out["fetch_stock_profile"] = {
            "ok": prof is not None,
            "industry": getattr(prof, "industry", "") if prof else "",
            "meta": meta,
        }
    except Exception as e:
        out["fetch_stock_profile"] = {"ok": False, "error": str(e)[:200]}
    return out


def test_report(symbol: str = "600519") -> dict:
    """研报。"""
    from laap.paper_trading.news_intel import fetch_research_reports
    out = {}
    try:
        items, meta = fetch_research_reports(symbol, max_results=5)
        out["fetch_research_reports"] = {
            "ok": len(items) > 0, "count": len(items), "meta": meta,
        }
    except Exception as e:
        out["fetch_research_reports"] = {"ok": False, "error": str(e)[:200]}
    return out


def test_calendar() -> dict:
    """交易日历。"""
    from laap.paper_trading.daily_pipeline import QuantDailyScheduler as Q
    out = {}
    cal, source = Q._load_calendar()
    out["日历"] = {"days": len(cal), "source": source,
                   "range": (min(cal) if cal else None, max(cal) if cal else None)}
    out["今日判定"] = Q._is_trading_day(cal, source)
    return out


def test_llm() -> dict:
    """LLM 链路（不实际调用，只探测配置可用性）。"""
    out = {}
    try:
        from laap.paper_trading.quant_config import get
        llm_chain = get("LLM_SOURCES")
        out["LLM_SOURCES配置"] = llm_chain
    except Exception as e:
        out["LLM"] = {"error": str(e)[:200]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="", help="仅测指定域（MARKET/KLINE/NEWS/PROFILE/REPORT/CALENDAR/LLM）")
    ap.add_argument("--symbol", default="600519")
    ap.add_argument("--name", default="贵州茅台")
    args = ap.parse_args()

    tests = {
        "MARKET": ("行情取价", lambda: test_market(args.symbol)),
        "KLINE": ("K线", lambda: test_kline(args.symbol)),
        "NEWS": ("新闻", lambda: test_news(args.symbol, args.name)),
        "PROFILE": ("公司画像", lambda: test_profile(args.symbol)),
        "REPORT": ("研报", lambda: test_report(args.symbol)),
        "CALENDAR": ("交易日历", test_calendar),
        "LLM": ("LLM链路", test_llm),
    }

    domains = [args.domain.upper()] if args.domain else list(tests.keys())

    print("=" * 70)
    print("LAAP 全量数据源测试")
    print(f"  标的: {args.symbol} {args.name}")
    print("=" * 70)

    results = {}
    for dom in domains:
        if dom not in tests:
            print(f"[SKIP] 未知域 {dom}")
            continue
        label, fn = tests[dom]
        print(f"\n[{dom}] {label}")
        t0 = time.time()
        try:
            r = fn()
            results[dom] = r
            for k, v in r.items():
                ok = v.get("ok") if isinstance(v, dict) else None
                mark = "✅" if ok else ("ℹ️" if ok is None else "❌")
                print(f"  {mark} {k}: {v}")
        except Exception as e:
            results[dom] = {"error": str(e)}
            print(f"  ❌ {dom} 测试异常: {e}")
        print(f"  耗时 {time.time() - t0:.1f}s")

    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    for dom in domains:
        r = results.get(dom, {})
        status = "✅" if r else "❌"
        print(f"  {status} {dom}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
