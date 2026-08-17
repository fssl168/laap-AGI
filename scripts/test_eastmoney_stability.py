# -*- coding: utf-8 -*-
"""东财 API 稳定性定期测试（任务 2）

每源探活:
  - REPORT   研报 reportapi.eastmoney.com/report/list
  - NEWS     新闻 search-api-web.eastmoney.com/search/jsonp
  - PROFILE  资料 emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax

指标: HTTP 状态 / 响应时间 / 数据条数 / 重试次数
输出: data/eastmoney_stability.json（含历史对比，连续失败 N 次标记 degraded）

用法:
    python scripts/test_eastmoney_stability.py [--symbol 600519] [--max-fail 3]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUT_PATH = Path(r"D:\laap-AGI\data\eastmoney_stability.json")
HISTORY_LIMIT = 30  # 保留最近 N 次


def _probe(name: str, fn) -> dict:
    """探活单个源，返回指标。"""
    t0 = time.time()
    retries = 0
    try:
        result = fn()
        elapsed = round(time.time() - t0, 3)
        if isinstance(result, (list, dict)):
            n = len(result)
            return {"source": name, "ok": True, "status": 200,
                    "elapsed_ms": int(elapsed * 1000), "count": n,
                    "retries": retries, "ts": time.time()}
        return {"source": name, "ok": True, "status": 200,
                "elapsed_ms": int(elapsed * 1000), "count": 0,
                "retries": retries, "ts": time.time()}
    except Exception as e:
        return {"source": name, "ok": False, "status": 0,
                "elapsed_ms": int((time.time() - t0) * 1000), "count": 0,
                "retries": retries, "error": str(e)[:120], "ts": time.time()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="600519")
    ap.add_argument("--max-fail", type=int, default=3,
                    help="连续失败多少次标记 degraded")
    args = ap.parse_args()

    from laap.paper_trading.em_reports import fetch_reports
    from laap.paper_trading.em_sources import (
        fetch_news_direct, fetch_profile_direct)

    print("=" * 60)
    print("东财 API 稳定性测试")
    print(f"  标的: {args.symbol} | 连续失败阈值: {args.max_fail}")
    print("=" * 60)

    results = [
        _probe("REPORT", lambda: fetch_reports(args.symbol, max_results=5)),
        _probe("NEWS", lambda: fetch_news_direct(args.symbol, max_results=5)),
        _probe("PROFILE", lambda: fetch_profile_direct(args.symbol)),
    ]

    # 汇总
    ok_count = sum(1 for r in results if r["ok"])
    for r in results:
        mark = "✅" if r["ok"] else "❌"
        print(f"  {mark} {r['source']}: status={r['status']} "
              f"elapsed={r['elapsed_ms']}ms count={r['count']}"
              + (f" error={r.get('error', '')}" if not r["ok"] else ""))

    # 读历史 + 追加
    hist = []
    if OUT_PATH.exists():
        try:
            hist = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        except Exception:
            hist = []
    hist.append({
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": args.symbol,
        "ok": ok_count,
        "total": len(results),
        "sources": results,
    })
    hist = hist[-HISTORY_LIMIT:]

    # 连续失败检测（按 source 统计最近记录）
    degraded = []
    for src_name in ("REPORT", "NEWS", "PROFILE"):
        recent = [h for h in hist if h.get("ts") == hist[-1]["ts"]]
        # 从最新往前数连续失败
        streak = 0
        for h in reversed(hist):
            src = next((s for s in h["sources"] if s["source"] == src_name), None)
            if src and not src["ok"]:
                streak += 1
            else:
                break
        if streak >= args.max_fail:
            degraded.append(src_name)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(hist, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"\n报告已保存: {OUT_PATH} (历史 {len(hist)} 次)")
    if degraded:
        print(f"⚠️ 连续失败告警: {degraded} (≥{args.max_fail}次)")
    print(f"结果: {ok_count}/{len(results)} 源可用")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
