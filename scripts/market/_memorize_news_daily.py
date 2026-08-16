# -*- coding: utf-8 -*-
"""每日新闻落库任务（收盘后 cron，对齐 LAAP_Memorize_Trading_Daily 模式）。

遍历自选股池（.env STOCK_LIST）→ NewsSignalPipeline.run(auto_order=False)
→ news_items（新闻原文）+ news_verdicts（判定）落库，加速两轨组合回放评估的数据积累。

安全设计：
  - auto_order=False：只落库+判定，**不下单**（fail-closed，本任务永不触发交易）
  - D1 去重：news_id=sha1(symbol|title|published_at) 幂等（INSERT OR IGNORE），
    已判定过的新闻跳过 LLM（_was_judged），重跑不重复、不烧 LLM 成本
  - LLM 判定优先（build_llm_call），失败自动降级 keyword 启发式（used_fallback=True 诚实标记）
  - 输出：每标的 抓取数/新增判定数/落库状态，供 cron 交付

用法：
  python scripts/market/_memorize_news_daily.py [--days N] [--symbols 600519,000001]
"""
import argparse
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
logging.getLogger("laap").setLevel(logging.WARNING)

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def load_stock_list() -> list:
    """读 .env STOCK_LIST（逗号分隔，兼容 '600511.SH' 写法）。"""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    raw = os.environ.get("STOCK_LIST", "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def build_pipeline():
    """装配 NewsSignalPipeline（独立于 api.py，避免导入副作用）。"""
    from laap.paper_trading.db import PaperDB
    from laap.paper_trading.market_source import StubMarketSource
    from laap.paper_trading.paper_service import PaperClosedLoop
    from laap.paper_trading.news_pipeline import NewsSignalPipeline
    from laap.paper_trading.llm_sources import build_llm_call
    from laap.paper_trading.quant_config import build_fee_model

    db = PaperDB(db_path=str(ROOT / "data" / "paper_trading.db"))
    loop = PaperClosedLoop(db=db, market=StubMarketSource(),
                           memory=None, initial_cash=1_000_000.0,
                           trading_self=None)
    return NewsSignalPipeline(loop=loop, db=db,
                              llm_call=build_llm_call(),
                              fee_model=build_fee_model())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="逗号分隔覆盖自选股池")
    ap.add_argument("--name-suffix", default="", help="名称后缀（调试用）")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] \
        or load_stock_list()
    if not symbols:
        print("[news_daily] STOCK_LIST 为空，无标的可扫描")
        sys.exit(1)

    print(f"[news_daily] {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"扫描 {len(symbols)} 标的落库（auto_order=False，不下单）")
    pipe = build_pipeline()
    total_news = 0
    total_verdicts = 0
    ok = 0
    for i, sym in enumerate(symbols, 1):
        t0 = time.time()
        try:
            r = pipe.run(sym, auto_order=False, name="")
            news_n = int(r.get("news_count", 0))
            vd_n = len(r.get("verdicts", []) or [])
            total_news += news_n
            total_verdicts += vd_n
            reason = str(r.get("reason", ""))[:60]
            print(f"  [{i}/{len(symbols)}] {sym}: news={news_n} new_verdicts={vd_n} "
                  f"({time.time()-t0:.1f}s) {reason}")
            ok += 1
        except Exception as e:
            print(f"  [{i}/{len(symbols)}] {sym}: ERROR {type(e).__name__}: {str(e)[:100]}")
        time.sleep(0.3)  # 限速，防接口限流

    print(f"\n[news_daily] 完成: {ok}/{len(symbols)} 标的成功 | "
          f"新闻 {total_news} 条落库 / 判定 {total_verdicts} 条")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
