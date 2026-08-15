# -*- coding: utf-8 -*-
"""
paper_trading 工具包 — LAAP内部实现

直接操作paper_trading.db数据库（零账户概念，单系统）。
"""

import sys, os, json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any
import sqlite3

# LAAP paper_trading路径
LAAP_ROOT = r"D:\laap-AGI"
DB_PATH = r"D:\laap-AGI\data\paper_trading.db"

if LAAP_ROOT not in sys.path:
    sys.path.insert(0, LAAP_ROOT)


def _db() -> sqlite3.Connection:
    """获取数据库连接。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _run(action: str, **kwargs) -> str:
    """统一入口。"""
    try:
        conn = _db()
        if action == "health":
            return _health(conn)
        elif action == "account_list":
            return "LAAP paper_trading无多账户概念，单系统运行"
        elif action == "positions":
            return _positions(conn)
        elif action == "strategies":
            return "策略信息在代码中定义，无独立策略表"
        elif action == "backtest":
            return _backtest(conn, kwargs.get("strategy", "default"))
        elif action == "risk_check":
            return _risk_check(conn)
        elif action == "performance":
            return _performance(conn)
        elif action == "signals":
            return _signals(conn)
        elif action == "orders":
            return _orders(conn)
        elif action == "trades":
            return _trades(conn)
        elif action == "evolutions":
            return _evolutions(conn)
        else:
            return f"未知操作: {action}"
    except Exception as e:
        return f"[错误] {e}"


def _health(conn) -> str:
    """系统健康检查。"""
    try:
        tables = ["signals", "orders", "trades", "net_values", "decisions", "outcomes", "evolutions"]
        result = "系统状态:\n"
        for t in tables:
            cursor = conn.execute(f"SELECT COUNT(*) FROM {t}")
            count = cursor.fetchone()[0]
            result += f"  {t}: {count}条记录\n"
        return result
    except Exception as e:
        return f"健康检查失败: {e}"


def _positions(conn) -> str:
    """查看持仓（通过trades未平仓记录）。"""
    try:
        # 检查未平仓的成交（exit_ts为空的trade）
        cursor = conn.execute("""
            SELECT symbol, SUM(quantity) as total_qty, AVG(entry_price) as avg_price
            FROM trades 
            WHERE exit_ts IS NULL
            GROUP BY symbol
        """)
        open_trades = cursor.fetchall()
        
        if not open_trades:
            return "暂无持仓（无未平仓记录）"
        
        result = "当前持仓:\n"
        for t in open_trades:
            result += f"  {t[0]}: {t[1]}股 @ {t[2]:.2f}\n"
        return result
    except Exception as e:
        return f"持仓查询失败: {e}"


def _backtest(conn, strategy: str) -> str:
    """运行回测。"""
    try:
        from laap.paper_trading.backtest_runner import run_backtest
        result = run_backtest(strategy)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except ImportError:
        return "回测模块未就绪，请检查配置"
    except Exception as e:
        return f"回测失败: {e}"


def _risk_check(conn) -> str:
    """风控检查。"""
    try:
        # 检查未平仓订单数量
        cursor = conn.execute("SELECT COUNT(*) FROM orders WHERE status='pending'")
        pending_count = cursor.fetchone()[0]
        
        # 检查今日交易数
        cursor = conn.execute("""
            SELECT COUNT(*) FROM trades 
            WHERE date(entry_ts, 'unixepoch') = date('now')
        """)
        today_trades = cursor.fetchone()[0]
        
        return f"风控状态:\n  未平仓订单: {pending_count}个\n  今日交易: {today_trades}笔"
    except Exception as e:
        return f"风控检查失败: {e}"


def _performance(conn) -> str:
    """绩效报告。"""
    try:
        # 净值曲线最后一条
        cursor = conn.execute("SELECT ts, total FROM net_values ORDER BY ts DESC LIMIT 1")
        last = cursor.fetchone()
        
        # 总交易数
        cursor = conn.execute("SELECT COUNT(*) FROM trades")
        total_trades = cursor.fetchone()[0]
        
        # 总盈亏
        cursor = conn.execute("SELECT SUM(pnl) FROM trades")
        total_pnl = cursor.fetchone()[0] or 0
        
        result = f"绩效摘要:\n"
        if last:
            from datetime import datetime
            ts = datetime.fromtimestamp(last[0]).strftime("%Y-%m-%d %H:%M")
            result += f"  最新净值: {last[1]:.2f} ({ts})\n"
        result += f"  总交易: {total_trades}笔\n"
        result += f"  累计盈亏: {total_pnl:+.2f}"
        return result
    except Exception as e:
        return f"绩效查询失败: {e}"


def _signals(conn) -> str:
    """信号列表。"""
    try:
        cursor = conn.execute("""
            SELECT symbol, action, quantity, trigger_price, ts, rationale
            FROM signals 
            ORDER BY ts DESC 
            LIMIT 10
        """)
        rows = cursor.fetchall()
        if not rows:
            return "暂无信号"
        result = "最近10条信号:\n"
        for r in rows:
            from datetime import datetime
            ts = datetime.fromtimestamp(r[4]).strftime("%H:%M:%S")
            result += f"  [{ts}] {r[0]} {r[1]} {r[2]}股 @ {r[3]:.2f}\n"
        return result
    except Exception as e:
        return f"信号查询失败: {e}"


def _orders(conn) -> str:
    """订单列表。"""
    try:
        cursor = conn.execute("""
            SELECT id, signal_id, status, fill_price, filled_ts
            FROM orders 
            ORDER BY filled_ts DESC 
            LIMIT 10
        """)
        rows = cursor.fetchall()
        if not rows:
            return "暂无订单"
        result = "最近10条订单:\n"
        for r in rows:
            ts = datetime.fromtimestamp(r[4]).strftime("%H:%M:%S") if r[4] else "pending"
            result += f"  {r[0]}: {r[2]} @ {r[3]:.2f} ({ts})\n"
        return result
    except Exception as e:
        return f"订单查询失败: {e}"


def _trades(conn) -> str:
    """成交列表。"""
    try:
        cursor = conn.execute("""
            SELECT symbol, side, quantity, entry_price, pnl, pnl_pct, hold_days
            FROM trades 
            ORDER BY entry_ts DESC 
            LIMIT 10
        """)
        rows = cursor.fetchall()
        if not rows:
            return "暂无成交"
        result = "最近10笔成交:\n"
        for r in rows:
            result += f"  {r[0]} {r[1]} {r[2]}股 @ {r[3]:.2f}, 盈亏{r[4]:+.2f} ({r[5]:.1f}%) 持仓{r[6] or 0}天\n"
        return result
    except Exception as e:
        return f"成交查询失败: {e}"


def _evolutions(conn) -> str:
    """演化记录。"""
    try:
        cursor = conn.execute("""
            SELECT mutation_id, decision, reason, ts
            FROM evolutions
            ORDER BY ts DESC
            LIMIT 5
        """)
        rows = cursor.fetchall()
        if not rows:
            return "暂无演化记录"
        result = "最近5条演化:\n"
        for r in rows:
            result += f"  {r[0]} {r[1]}: {r[2] or ''}\n"
        return result
    except Exception as e:
        return f"演化查询失败: {e}"


def _account_show(conn) -> str:
    """单账户详情（LAAP 单系统：返回账户概况 + 资金 + 持仓数）。"""
    try:
        result = "LAAP paper_trading 账户（单系统）:\n"
        # 资金：净值曲线最新值
        cursor = conn.execute("SELECT total FROM net_values ORDER BY ts DESC LIMIT 1")
        last = cursor.fetchone()
        result += f"  最新净值: {last[0]:.2f}\n" if last else "  净值: 暂无记录\n"
        # 持仓数
        cursor = conn.execute("SELECT COUNT(DISTINCT symbol) FROM trades WHERE exit_ts IS NULL")
        positions = cursor.fetchone()[0]
        result += f"  未平仓标的: {positions}个\n"
        # 总交易
        cursor = conn.execute("SELECT COUNT(*) FROM trades")
        result += f"  累计交易: {cursor.fetchone()[0]}笔"
        return result
    except Exception as e:
        return f"账户详情查询失败: {e}"


# ─── 工具注册表 ────────────────────────────────────────────

PAPER_TRADING_TOOLS: Dict[str, Dict[str, Any]] = {
    "pt_health": {"fn": lambda: _run("health"), "desc": "系统健康检查"},
    "pt_account_list": {"fn": lambda: _run("account_list"), "desc": "列出账户(LAAP单系统)"},
    # 命名统一（规则侧引用名 = 注册名，消除悬空引用）：
    #   pt_account_show  → 账户详情
    #   pt_account_positions / pt_positions → 持仓（别名）
    #   pt_strategy_list / pt_strategies → 策略（别名）
    "pt_account_show": {"fn": lambda: _account_show(_db()), "desc": "查看账户详情"},
    "pt_positions": {"fn": lambda: _run("positions"), "desc": "查看持仓"},
    "pt_account_positions": {"fn": lambda: _run("positions"), "desc": "查看持仓(规则名)"},
    "pt_strategies": {"fn": lambda: _run("strategies"), "desc": "查看策略"},
    "pt_strategy_list": {"fn": lambda: _run("strategies"), "desc": "查看策略(规则名)"},
    "pt_backtest_run": {"fn": lambda strategy: _run("backtest", strategy=strategy), "desc": "运行回测"},
    "pt_risk_check": {"fn": lambda: _run("risk_check"), "desc": "风控检查"},
    "pt_performance": {"fn": lambda: _run("performance"), "desc": "绩效报告"},
    "pt_signals": {"fn": lambda: _run("signals"), "desc": "信号列表"},
    "pt_orders": {"fn": lambda: _run("orders"), "desc": "订单列表"},
    "pt_trades": {"fn": lambda: _run("trades"), "desc": "成交列表"},
    "pt_evolve": {"fn": lambda: _run("evolutions"), "desc": "演化记录"},
}


def register_paper_trading_tools(registry) -> int:
    """注册工具到 ARIS 规则引擎。"""
    count = 0
    for name, info in PAPER_TRADING_TOOLS.items():
        registry.register(name, info["fn"], info["desc"])
        count += 1
    return count


if __name__ == "__main__":
    print("paper_trading_tools.py (LAAP版) 加载成功")
    print(f"共注册 {len(PAPER_TRADING_TOOLS)} 个工具\n")
    for name, info in sorted(PAPER_TRADING_TOOLS.items()):
        print(f"  {name}: {info['desc']}")
    
    print("\n===测试调用===")
    for name in ["pt_health", "pt_positions", "pt_performance", "pt_signals"]:
        fn = PAPER_TRADING_TOOLS[name]["fn"]
        print(f"\n{name}:")
        print(fn()[:200])
