# -*- coding: utf-8 -*-
from pathlib import Path
"""每日 A 股大盘行情记忆（收盘后运行，动态日期版）。

拉取三大指数收盘数据 → 写入 LAAP 语义记忆 → 输出摘要（供 cron 交付）。
节假日运行时自动使用最近交易日数据（以行情接口返回日期为准）。
"""
import datetime
import json
import re
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

REFLECT = "http://localhost:11546/v1/reflect"
RECALL = "http://localhost:11546/v1/recall_memory"
INDICES = [("000001", "上证指数"), ("399001", "深证成指"), ("399006", "创业板指")]

_WEEKDAYS = "一二三四五六日"


def _fetch_url(url: str, timeout: int = 15) -> bytes:
    """标准库 urllib 拉取（2026-08-17：替换已不存在的 src.agent.tools.search_tools._fetch_url）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_market():
    """腾讯行情：拉三大指数，返回 (data_date, indices)。"""
    q = ",".join(("sh" + c if c.startswith("0") else "sz" + c) for c, _ in INDICES)
    raw = _fetch_url(f"http://qt.gtimg.cn/q={q}", timeout=15).decode("gbk", errors="ignore")
    rows = {}
    data_date = ""
    for m in re.finditer(r'v_\w+="([^"]+)"', raw):
        f = m.group(1).split("~")
        if len(f) < 38:
            continue
        data_date = f[30][:8]  # 20260811（行情数据日期）
        rows[f[2]] = {
            "name": f[1], "price": float(f[3]), "change": float(f[31]),
            "change_pct": float(f[32]),
            "open": float(f[5]), "high": float(f[33]), "low": float(f[34]),
            "volume": float(f[6]),
            "turnover_yi": round(float(f[37]) / 10000.0, 1),
        }
    indices = [rows[c] for c, _ in INDICES if c in rows]
    return data_date, indices


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    try:
        data_date, indices = fetch_market()
        if len(indices) != 3 or not data_date:
            print(f"行情获取失败: {len(indices)}/3 指数, 跳过本次记忆")
            sys.exit(1)

        dt = datetime.datetime.strptime(data_date, "%Y%m%d")
        date_str = f"{dt.year}-{dt.month:02d}-{dt.day:02d}(周{_WEEKDAYS[dt.weekday()]})"
        db_date = f"{dt.year}-{dt.month:02d}-{dt.day:02d}"  # 落盘统一 YYYY-MM-DD（与 K 线脚本一致）

        # 完整指数数据落盘 SQLite（趋势分析用）
        try:
            from watchlist_kline_store import upsert_kline

            rows = []
            for ix in indices:
                code = "sh000001" if ix["name"] == "上证指数" else (
                    "sz399001" if ix["name"] == "深证成指" else "sz399006")
                rows.append((code, db_date, ix.get("open", ix["price"]), ix["price"],
                             ix.get("high", ix["price"]), ix.get("low", ix["price"]),
                             ix.get("volume", 0)))
            n = upsert_kline(rows)
            print(f"[存储] 指数日K已落盘 {n} 条 -> data/watchlist_kline_store.db")
        except Exception as exc:  # noqa: BLE001
            print(f"[存储] 指数落盘失败: {exc}")

        parts = []
        for ix in indices:
            arrow = "涨" if ix["change"] >= 0 else "跌"
            parts.append(f"{ix['name']}{ix['price']}点({arrow}{abs(ix['change_pct']):.2f}%,成交{ix['turnover_yi']}亿)")
        # 两市合计 = 沪市 + 深市（创业板指属深市，不重复计入）
        total_yi = sum(ix["turnover_yi"] for ix in indices[:2])
        summary = (
            f"【大盘行情记忆】{date_str} A股收盘: " + ", ".join(parts)
            + f"; 两市合计成交约{total_yi / 10000.0:.2f}万亿; "
            "数据来源腾讯行情, 由LAAP工具链路自动记录."
        )

        post(REFLECT, {"output": summary})

        # 召回验证
        d = post(RECALL, {"query": "今天大盘 上证指数行情", "limit": 1})
        hit = (d.get("memories") or [{}])[0]
        ok = "大盘行情记忆" in (hit.get("text") or "")
        print(summary)
        print(f"[记忆验证] 召回{'成功' if ok else '失败'} score={hit.get('score', 0):.3f}")
        sys.exit(0 if ok else 1)
    except Exception as exc:  # noqa: BLE001
        print(f"[大盘记忆任务失败] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
