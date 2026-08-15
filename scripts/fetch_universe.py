# -*- coding: utf-8 -*-
"""Track ① 扩样本：拉取沪深300成分股（腾讯源）→ real_data/universe/。

目标：>=200 标的 × >=500 交易日（前复权 qfq），供大样本 walk-forward 验证。
源：akshare stock_zh_a_hist_tx（腾讯，已验证可用；东财 stock_zh_a_hist 在沙箱被断连）。
防限流：随机冷却 1~2s + 失败重试 ×2 + 进度落盘（可中断续跑）。

用法:
    python scripts/fetch_universe.py --n 200 --days 500 --out real_data/universe
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _prefix(code: str) -> str:
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def fetch_one(symbol: str, start: str, end: str,
              retries: int = 2) -> list:
    """拉单只前复权日 K → [(date_str, open, close, high, low, volume), ...]。"""
    import akshare as ak
    last_err = None
    for attempt in range(retries + 1):
        try:
            df = ak.stock_zh_a_hist_tx(
                symbol=symbol, start_date=start, end_date=end, adjust="qfq")
            if df is None or len(df) < 10:
                return []
            rows = []
            for _, r in df.iterrows():
                rows.append((str(r["date"]), float(r["open"]), float(r["close"]),
                             float(r["high"]), float(r["low"]), float(r["volume"])))
            return rows
        except Exception as e:
            last_err = e
            time.sleep(2 + attempt * 2)
    print(f"    [FAIL] {symbol}: {type(last_err).__name__} {str(last_err)[:60]}")
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="拉取沪深300成分股日K（腾讯源）")
    ap.add_argument("--n", type=int, default=200, help="拉取前 N 只成分股")
    ap.add_argument("--offset", type=int, default=0,
                    help="跳过前 offset 只（分批续拉，如 --offset 200 --n 100）")
    ap.add_argument("--days", type=int, default=500, help="目标交易日数")
    ap.add_argument("--out", default="real_data/universe")
    ap.add_argument("--cooldown-min", type=float, default=1.0)
    ap.add_argument("--cooldown-max", type=float, default=2.0)
    args = ap.parse_args()

    import akshare as ak
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 沪深300 成分股
    cons = ak.index_stock_cons(symbol="000300")
    code_col = [c for c in cons.columns if "代码" in c or "code" in c.lower()]
    if not code_col:
        print("[FAIL] 无法识别成分股代码列:", list(cons.columns))
        return 1
    codes = [str(c).zfill(6) for c in cons[code_col[0]].tolist()]
    codes = codes[args.offset:args.offset + args.n]
    print(f"成分股: {len(codes)} 只（offset={args.offset}）| 目标: >= {args.days} 交易日 | 源: 腾讯 qfq")

    end = datetime.now()
    start = end - timedelta(days=int(args.days * 1.6) + 40)  # 交易日≈自然日0.7，放宽余量
    start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    progress_path = out_dir / "_progress.json"
    done: dict = {}
    if progress_path.exists():
        try:
            done = json.loads(progress_path.read_text(encoding="utf-8"))
        except Exception:
            done = {}

    stats = {"ok": 0, "short": 0, "fail": 0}
    for idx, code in enumerate(codes):
        f = out_dir / f"{code}.json"
        if code in done:
            continue
        rows = fetch_one(_prefix(code), start_s, end_s)
        if len(rows) < args.days:
            stats["short" if rows else "fail"] += 1
            done[code] = len(rows)
        else:
            closes = [r[2] for r in rows]
            f.write_text(json.dumps(closes, ensure_ascii=False), encoding="utf-8")
            (out_dir / f"{code}.ohlcv.json").write_text(
                json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            stats["ok"] += 1
            done[code] = len(closes)
        if (idx + 1) % 10 == 0 or code in done:
            print(f"  [{idx+1}/{len(codes)}] ok={stats['ok']} short={stats['short']} "
                  f"fail={stats['fail']} (last={code}:{done[code]})")
            progress_path.write_text(json.dumps(done, ensure_ascii=False),
                                     encoding="utf-8")
        time.sleep(random.uniform(args.cooldown_min, args.cooldown_max))

    progress_path.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
    (out_dir / "_meta.json").write_text(json.dumps(
        {"n_target": len(codes), "start": start_s, "end": end_s,
         "source": "akshare stock_zh_a_hist_tx (tencent, qfq)",
         "stats": stats, "per_symbol": done}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n完成: {stats} | 目录: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
