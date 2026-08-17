# -*- coding: utf-8 -*-
"""每日交易认知快照（收盘后 15:30 cron，方案 v2.0 §4.4.3）。

拉取 paper_trading 当日状态（净值/盈亏/教训/风控）→ 写入 LAAP 语义记忆
【交易日报 YYYY-MM-DD】→ 输出摘要（供 cron 交付）。

Aris 下次对话自动感知跨日复盘（"昨天亏了，今天谨慎些"）。
与教训双写共用语义记忆写锁（memory_bridge._semantic_memory_lock），防并发写 JSON。

数据库: PaperDB（PG16 laap_trading 优先，SQLite laap_trading.db 回退）。
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def _connect():
    """PaperDB 连接（PG 优先，SQLite 回退），兼容 sqlite3 风格。"""
    from laap.paper_trading.db import PaperDB
    db = PaperDB()
    return db.conn()


def _today_filter(ts_col: str) -> str:
    """按天过滤表达式（PG/SQLite 兼容）。"""
    try:
        from laap.paper_trading.db import pg_available
        if pg_available():
            return f"to_timestamp({ts_col})::date = current_date"
    except Exception:
        pass
    return f"date({ts_col},'unixepoch','localtime') = date('now','localtime')"


def fetch_trading_status(conn) -> dict:
    """汇总当日交易状态。"""
    from datetime import datetime as dt
    today = dt.now().strftime("%Y-%m-%d")
    status = {"date": today}

    # 净值
    cur = conn.execute("SELECT ts, total FROM net_values ORDER BY ts DESC LIMIT 1")
    row = cur.fetchone()
    if row:
        status["net_value"] = round(row[1], 2)
        status["net_value_ts"] = dt.fromtimestamp(row[0]).strftime("%H:%M")
    else:
        status["net_value"] = None

    # 今日交易
    cur = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM trades "
        f"WHERE {_today_filter('entry_ts')}")
    cnt, pnl = cur.fetchone()
    status["trades_today"] = cnt
    status["pnl_today"] = round(pnl, 2) if pnl else 0.0

    # 持仓
    cur = conn.execute("SELECT COUNT(DISTINCT symbol) FROM trades WHERE exit_ts IS NULL")
    status["open_positions"] = cur.fetchone()[0]

    # 教训
    cur = conn.execute(
        "SELECT lesson_type, lesson FROM outcomes "
        "WHERE lesson IS NOT NULL AND lesson != '' "
        "ORDER BY trade_id DESC LIMIT 3")
    lessons = [{"type": r[0] or "general", "text": r[1][:60]} for r in cur.fetchall()]
    status["lessons"] = lessons

    # 风控
    cur = conn.execute(
        f"SELECT COUNT(*) FROM risk_rejections "
        f"WHERE {_today_filter('ts')}")
    status["risk_rejections"] = cur.fetchone()[0]
    return status


def build_summary(status: dict) -> str:
    """构造【交易日报】文本。"""
    lines = [f"【交易日报 {status['date']}】"]
    nv = status.get("net_value")
    lines.append(f"最新净值: {nv if nv is not None else '暂无'}")
    lines.append(f"今日交易: {status['trades_today']}笔 | 盈亏: {status['pnl_today']:+.2f}")
    lines.append(f"未平仓: {status['open_positions']}个 | 风控拦截: {status['risk_rejections']}次")
    if status.get("lessons"):
        lines.append("教训:")
        for ls in status["lessons"]:
            lines.append(f"  - [{ls['type']}] {ls['text']}")
    else:
        lines.append("教训: 今日无")
    return "\n".join(lines)


def write_to_semantic_memory(summary: str) -> bool:
    """写入语义记忆（带锁，与教训双写共用）。"""
    try:
        from aris_brain.laap_semantic_memory import get_memory
        from laap.paper_trading.memory_bridge import _semantic_memory_lock
        mem = get_memory()
        with _semantic_memory_lock:
            mem.add(summary, meta={"source": "trading_daily_cron", "date": summary.split()[1][:10] if len(summary.split()) > 1 else ""})
        return True
    except Exception as e:
        print(f"[警告] 语义记忆写入失败: {e}")
        return False


def main():
    try:
        conn = _connect()
        try:
            status = fetch_trading_status(conn)
        finally:
            conn.close()
    except Exception as e:
        print(f"[错误] 读取交易状态失败: {e}")
        sys.exit(1)

    summary = build_summary(status)
    ok = write_to_semantic_memory(summary)
    print(summary)
    print(f"\n[memorize_trading_daily] 语义记忆写入: {'成功' if ok else '失败'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
