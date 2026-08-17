# -*- coding: utf-8 -*-
"""清理 paper_trading.db 中的演示 seed 交易（启动演示占位数据）。

识别规则（只删 seed，不碰真实交易）：
  trades 中 entry_price==exit_price AND pnl=0 AND hold_days=0
  AND 关联 signal.rationale LIKE '%启动演示%'

连带删除：outcomes / trades / orders / signals / decisions。

安全：默认 --dry-run 只预览；加 --apply 才真正删除。

用法:
    python scripts/cleanup_paper_seed.py            # 预览
    python scripts/cleanup_paper_seed.py --apply    # 执行删除
    python scripts/cleanup_paper_seed.py --db <path>
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _default_db_path() -> Path:
    from laap.paper_trading.db import _is_windows_drive_abs
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PAPER_TRADING_DB_PATH="):
                p = Path(line.split("=", 1)[1].strip())
                if os.name != "nt" and _is_windows_drive_abs(str(p)):
                    break  # Windows 盘符路径在非 Windows 无效 → 回退项目根相对路径
                if p.exists():
                    return p
                break
    return ROOT / "data" / "laap_trading.db"


def main() -> int:
    ap = argparse.ArgumentParser(description="清理 paper seed 演示交易")
    ap.add_argument("--db", default="", help="显式指定 SQLite 库路径（默认走 PG laap_trading）")
    ap.add_argument("--apply", action="store_true", help="真正删除（默认仅预览）")
    args = ap.parse_args()

    # 连接：显式 --db → SQLite；否则 PaperDB（PG 优先）
    if args.db:
        conn = sqlite3.connect(args.db)
    else:
        from laap.paper_trading.db import PaperDB
        conn = PaperDB().conn()
    cur = conn.cursor()

    # 1. 找出 seed trades（含"启动演示"标记）
    cur.execute("""
        SELECT t.id, t.order_id FROM trades t
        JOIN orders o ON o.id = t.order_id
        JOIN signals s ON s.id = o.signal_id
        WHERE t.entry_price = t.exit_price AND t.pnl = 0 AND t.hold_days = 0
          AND s.rationale LIKE '%启动演示%'
    """)
    seeds = cur.fetchall()
    if not seeds:
        print("未找到演示 seed 交易，无需清理。")
        conn.close()
        return 0

    trade_ids = [r[0] for r in seeds]
    order_ids = [r[1] for r in seeds]
    signal_ids, decision_ids = [], []
    for oid in order_ids:
        cur.execute("SELECT signal_id, client_request_id FROM orders WHERE id=?", (oid,))
        r = cur.fetchone()
        if r:
            if r[0]:
                signal_ids.append(r[0])
            if r[1]:
                decision_ids.append(r[1])

    print(f"待清理 seed 交易: {len(trade_ids)} 条")
    for tid in trade_ids:
        print(f"  trade {tid}")
    print(f"  关联 orders: {order_ids}")
    print(f"  关联 signals: {signal_ids}")
    print(f"  关联 decisions: {decision_ids}")
    cur.execute("SELECT COUNT(*) FROM outcomes WHERE trade_id IN (%s)"
                % ",".join("?" * len(trade_ids)), trade_ids)
    outcome_ids = [r[0] for r in cur.fetchall()]
    print(f"  关联 outcomes: {outcome_ids}")

    if not args.apply:
        print("\n[预览] 未删除。加 --apply 执行。")
        conn.close()
        return 0

    # 2. 删除（事务）
    try:
        cur.execute("BEGIN")
        ph = ",".join("?" * len(trade_ids))
        cur.execute(f"DELETE FROM outcomes WHERE trade_id IN ({ph})", trade_ids)
        cur.execute(f"DELETE FROM trades WHERE id IN ({ph})", trade_ids)
        for o in order_ids:
            cur.execute("DELETE FROM orders WHERE id=?", (o,))
        for s in signal_ids:
            cur.execute("DELETE FROM signals WHERE id=?", (s,))
        for d in decision_ids:
            cur.execute("DELETE FROM decisions WHERE decision_id=?", (d,))
        cur.execute("COMMIT")
        print("\n[完成] 已删除 seed 交易及其关联记录。")
    except Exception as e:
        cur.execute("ROLLBACK")
        print(f"\n[失败] 已回滚: {e}")
        conn.close()
        return 1

    # 3. 清理后统计
    for t in ("signals", "orders", "trades", "decisions", "outcomes"):
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  {t}: {cur.fetchone()[0]}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
