# -*- coding: utf-8 -*-
"""真实全管线 E2E：新闻 → LLM+RSI 判定 → 研报策略 → 风控门 → 自动下单（Paper）→ 留痕。

用户环境（联网 + DEEPSEEK_API_KEY）执行：
    python scripts/e2e_news_pipeline_real.py [--symbol 600519] [--auto-order] [--force]

默认 auto_order=False（只出计划不下单，安全）；--auto-order 时走完整自动下单闭环，
下单目标为临时目录的 paper 账本（PaperDB tmp），不会触碰真实资金。

输出：profile / news / verdicts / aggregated / plan / order / decision_id，
并验证闭环：signals / orders / trades / decisions 各表落库 + decision_id 追溯。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="真实全管线 E2E")
    ap.add_argument("--symbol", default="600519")
    ap.add_argument("--name", default="")
    ap.add_argument("--auto-order", action="store_true", help="走自动下单闭环（paper）")
    ap.add_argument("--force", action="store_true", help="强制重判（忽略 D1 去重）")
    ap.add_argument("--news-limit", type=int, default=3, help="参与判定的新闻条数上限")
    ap.add_argument("--inject-bullish", action="store_true",
                    help="注入一条真实利好新闻+买入研报（验证真利好→自动下单闭环；"
                         "新闻/研报为构造样例，LLM 判定与下单为真实路径）")
    ap.add_argument("--force-now", action="store_true",
                    help="把真实技术状态 RSI 覆写为 40（命中 now-buy 分支，验证立即下单；"
                         "仅当真实标的 RSI>50 时用于补验 dispatch 路径）")
    ap.add_argument("--cash", type=float, default=1_000_000.0,
                    help="paper 初始资金（高价股+单票10%上限时需调大以命中整手）")
    ap.add_argument("--fake-market", type=float, default=0.0,
                    help="注入固定价非降级行情源（实时行情不可用时验证 dispatch 路径；"
                         "0=用真实行情源，降级时 fail-closed 不下单）")
    args = ap.parse_args()

    from laap.paper_trading.db import PaperDB
    from laap.paper_trading.market_source import resolve_source
    from laap.paper_trading.paper_service import PaperClosedLoop
    from laap.paper_trading.news_pipeline import NewsSignalPipeline
    from laap.paper_trading.llm_sources import build_llm_call
    import laap.paper_trading.news_pipeline as np_mod

    tmp = tempfile.mkdtemp(prefix="e2e_news_")
    print(f"[E2E] tmp db: {tmp}")

    # 真实 loop（paper 账本 + 真实行情源 + UnifiedMemory）
    from laap.agi.unified_memory import UnifiedMemory
    db = PaperDB(db_path=str(Path(tmp) / "pt.db"))
    if args.fake_market > 0:
        class _FakeMarket:
            def get_price(self, symbol, ts=None):
                return args.fake_market, {"source": "fake", "used_fallback": False}
        loop = PaperClosedLoop(db=db, market=_FakeMarket(),
                               memory=UnifiedMemory(), initial_cash=args.cash)
        print(f"[E2E] loop built: market=fake@{args.fake_market}, cash={loop.ledger.cash}")
    else:
        loop = PaperClosedLoop(db=db, market=resolve_source(prefer_live=True),
                               memory=UnifiedMemory(), initial_cash=args.cash)
        print(f"[E2E] loop built: market={type(loop.market).__name__}, cash={loop.ledger.cash}")

    # 真实 LLM 链（openai → anspire → urllib → ollama → local → cli）
    llm_call = build_llm_call()

    # 限制新闻条数（控 LLM 调用成本）
    if args.inject_bullish:
        from laap.paper_trading.news_intel import NewsItem, ResearchReport
        bull_news = [NewsItem(
            args.symbol, "贵州茅台核心产品出厂价上调10%",
            "公司公告自8月16日起上调核心产品出厂价10%，机构测算将显著增厚利润，"
            "白酒行业景气度回升，需求端强劲。",
            source="公告", published_at="2026-08-15")]
        bull_reports = [ResearchReport(
            args.symbol, title="上调目标价", rating="买入", org="中信证券",
            target_price=2000.0, eps=60.0, pe=28.0, date="2026-08-15",
            source="stub")]
        np_mod.fetch_stock_news = lambda symbol, **kw: (bull_news, {
            "source": "injected", "used_fallback": False})
        np_mod.fetch_research_reports = lambda symbol, **kw: (bull_reports, {
            "source": "injected", "used_fallback": False})
        np_mod.fetch_stock_profile = lambda symbol, **kw: (None, {
            "source": "injected", "used_fallback": True})
        print("[E2E] injected bullish news + buy-rated report (LLM/下单仍真实)")
    elif args.news_limit > 0:
        _orig = np_mod.fetch_stock_news
        def _bounded(symbol, **kw):
            items, meta = _orig(symbol, **kw)
            return items[:args.news_limit], meta
        np_mod.fetch_stock_news = _bounded

    if args.force_now:
        import copy
        _real_build = np_mod.build_trade_plan
        def _force_now(symbol, profile, reports, tech_state, **kw):
            ts = copy.copy(tech_state)
            ts.rsi = 40.0          # 覆写为 ≤50，命中 now-buy 分支
            ts.limit_up = False
            return _real_build(symbol, profile, reports, ts, **kw)
        np_mod.build_trade_plan = _force_now
        print("[E2E] force-now: tech_state.rsi -> 40（补验立即下单路径）")

    pipe = NewsSignalPipeline(loop=loop, db=db, llm_call=llm_call)
    r = pipe.run(args.symbol, auto_order=args.auto_order,
                 name=args.name or args.symbol, force=args.force)

    print("\n" + "=" * 60)
    print(f"symbol={r.get('symbol')} news_count={r.get('news_count')} "
          f"silent={r.get('silent')} dispatched={r.get('dispatched')}")
    print(f"reason={r.get('reason')}")
    print(f"data_meta={json.dumps(r.get('data_meta'), ensure_ascii=False)}")
    if r.get("profile"):
        prof = r["profile"]
        print(f"profile: industry={prof.get('industry')} "
              f"total_mv={prof.get('total_mv')} source={prof.get('source')}")
    for i, v in enumerate(r.get("verdicts", [])):
        print(f"  verdict[{i}]: {v.get('verdict')} conf={v.get('confidence')} "
              f"rsi={v.get('rsi')} trade_action={v.get('trade_action')} "
              f"reasons={v.get('reasons')}")
    agg = r.get("aggregated") or {}
    print(f"aggregated: dispatch={agg.get('dispatch')} "
          f"confidence={agg.get('confidence')} top_news_ids={agg.get('top_news_ids')}")
    if r.get("plan"):
        print(f"plan: {json.dumps(r['plan'], ensure_ascii=False)}")
    if r.get("order"):
        print(f"order: {json.dumps(r['order'], ensure_ascii=False)}")
    print(f"decision_id={r.get('decision_id')}")

    # 闭环验证：signals / orders / trades / decisions
    conn = db.conn()
    for tbl in ("signals", "orders", "trades", "decisions", "news_items",
                "news_verdicts", "risk_rejections"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        except Exception as e:
            n = f"ERR({e})"
        print(f"  db[{tbl}] = {n}")
    conn.close()
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
