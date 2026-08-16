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
        elif action == "lessons":
            return _lessons(conn)
        elif action == "net_value":
            return _net_value(conn)
        elif action == "risk_events":
            return _risk_events(conn)
        elif action == "brief":
            return _brief(conn)
        elif action == "evolution_audit":
            return _evolution_audit()
        elif action == "watchlist":
            return _watchlist()
        elif action == "profile":
            return _profile(kwargs.get("symbol", ""))
        elif action == "sector_reports":
            return _sector_reports(kwargs.get("sector", ""))
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
        result = "最近10笔成交:\\n"
        for r in rows:
            # 2026-08-16: pnl/pnl_pct 对未平仓持仓为 NULL, format 会崩
            # (unsupported format string passed to NoneType.__format__)
            pnl = r[4] if r[4] is not None else 0.0
            pct = r[5] if r[5] is not None else 0.0
            days = r[6] if r[6] is not None else 0
            result += f"  {r[0]} {r[1]} {r[2]}股 @ {r[3]:.2f}, 盈亏{pnl:+.2f} ({pct:.1f}%) 持仓{days}天\n"
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


# ─── Phase 3 新增: 管理闭环工具 (方案 v2.0 §4.4) ────────────

def _brief(conn) -> str:
    """每日交易简报: 净值/盈亏/教训/明日关注 (结构化复盘)。"""
    try:
        from datetime import datetime
        result = "📊 今日交易简报\n"
        # 1. 净值
        cursor = conn.execute("SELECT ts, total FROM net_values ORDER BY ts DESC LIMIT 2")
        rows = cursor.fetchall()
        if rows:
            ts = datetime.fromtimestamp(rows[0][0]).strftime("%m-%d %H:%M")
            result += f"  最新净值: {rows[0][1]:.2f} ({ts})\n"
            if len(rows) > 1 and rows[1][1]:
                chg = (rows[0][1] - rows[1][1]) / rows[1][1] * 100
                result += f"  较上次: {chg:+.2f}%\n"
        else:
            result += "  净值: 暂无记录\n"
        # 2. 今日盈亏 (按日期统计 trades)
        cursor = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM trades "
            "WHERE date(entry_ts,'unixepoch','localtime') = date('now','localtime')")
        cnt, pnl = cursor.fetchone()
        result += f"  今日交易: {cnt}笔 | 盈亏: {pnl:+.2f}\n"
        # 3. 持仓
        cursor = conn.execute("SELECT COUNT(DISTINCT symbol) FROM trades WHERE exit_ts IS NULL")
        result += f"  未平仓标的: {cursor.fetchone()[0]}个\n"
        # 4. 教训 (最近2条)
        cursor = conn.execute(
            "SELECT o.lesson_type, o.lesson FROM outcomes o "
            "WHERE o.lesson IS NOT NULL AND o.lesson != '' "
            "ORDER BY o.trade_id DESC LIMIT 2")
        lessons = cursor.fetchall()
        if lessons:
            result += "  教训:\n"
            for lt, ls in lessons:
                result += f"    - [{lt or 'general'}] {ls[:50]}\n"
        # 5. 风控事件 (今日)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM risk_rejections "
            "WHERE date(ts,'unixepoch','localtime') = date('now','localtime')")
        rej = cursor.fetchone()[0]
        result += f"  风控拦截: {rej}次"
        return result
    except Exception as e:
        return f"简报生成失败: {e}"


def _evolution_audit() -> str:
    """进化治理: 列出近期进化提案 (读 EvolutionAuditLog, 与 /v1/quant/evolve/audit 同源)。"""
    try:
        from laap.agi.evolution_audit import EvolutionAuditLog
        audit = EvolutionAuditLog(repo_root=LAAP_ROOT)
        st = audit.stats()
        recent = audit.query(limit=10)
        result = f"🔬 进化治理 (共 {st['total_entries']} 条审计记录)\n"
        by = st.get("by_decision", {})
        result += f"  决策分布: {by}\n"
        if not recent:
            result += "  暂无进化提案"
            return result
        result += "  最近记录:\n"
        for e in recent:
            tgt = e.get("target", "")
            d = e.get("decision", "")
            result += f"    - [{d}] {tgt}: {e.get('reason','')[:40]}\n"
        return result
    except Exception as e:
        return f"进化审计查询失败: {e}"


def _watchlist() -> str:
    """列出自选股 (读取 .env 的 STOCK_LIST, 契约单源)。"""
    try:
        import os
        from dotenv import load_dotenv
        load_dotenv(os.path.join(LAAP_ROOT, ".env"))
        raw = os.environ.get("STOCK_LIST", "") or ""
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        if not codes:
            return "暂无自选股（.env 未配置 STOCK_LIST）"
        result = f"📋 自选股列表 ({len(codes)} 只):\n"
        for i, c in enumerate(codes, 1):
            result += f"  {i}. {c}\n"
        return result.rstrip()
    except Exception as e:
        return f"自选股查询失败: {e}"


def _profile(symbol: str = "") -> str:
    """个股资料查询 (复用 news_intel.fetch_stock_profile, fail-closed)。"""
    try:
        if not symbol or str(symbol).strip() in ("{symbol}", ""):
            return "请提供股票代码，如 '列出 600519 个股资料'"
        from laap.paper_trading.news_intel import fetch_stock_profile
        prof, meta = fetch_stock_profile(str(symbol).strip())
        if prof is None:
            fallback = "（数据源降级/不可用）" if meta.get("used_fallback") else ""
            return f"未获取到 {symbol} 个股资料{fallback}"
        p = prof.to_dict()
        lines = [f"📄 {p.get('company_name', symbol)} ({symbol}) 个股资料"]
        if p.get("industry"):
            lines.append(f"  行业: {p['industry']}")
        if p.get("list_date"):
            lines.append(f"  上市: {p['list_date']}")
        if p.get("total_mv"):
            lines.append(f"  总市值: {p['total_mv']}")
        if p.get("float_mv"):
            lines.append(f"  流通市值: {p['float_mv']}")
        if p.get("total_share"):
            lines.append(f"  总股本: {p['total_share']}")
        if p.get("float_share"):
            lines.append(f"  流通股本: {p['float_share']}")
        if p.get("registered_capital"):
            lines.append(f"  注册资本: {p['registered_capital']}")
        if p.get("main_business"):
            lines.append(f"  主营业务: {str(p['main_business'])[:80]}")
        if meta.get("used_fallback"):
            lines.append("  ⚠️ 数据源降级（stub/缓存）")
        return "\n".join(lines)
    except Exception as e:
        return f"个股资料查询失败: {e}"


def _sector_reports(sector: str = "") -> str:
    """行业/板块研报: 对自选股池拉研报, 按板块关键词过滤聚合。

    真实环境 (用户机器, akshare 可达): fetch_research_reports 多源链
    (eastmoney→cls→sina) 返回个股研报; 按标题/机构含板块关键词过滤。
    沙箱/离线: 源失败 → used_fallback, 返回提示 (fail-closed, 不伪造)。
    """
    try:
        if not sector or str(sector).strip() in ("{sector}", ""):
            return "请提供行业/板块名，如 '列出 白酒 行业板块研报'"
        sector_kw = str(sector).strip()
        from laap.paper_trading.news_intel import fetch_research_reports
        import os
        from dotenv import load_dotenv
        load_dotenv(os.path.join(LAAP_ROOT, ".env"))
        raw = os.environ.get("STOCK_LIST", "") or ""
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        if not codes:
            return "自选股池为空（.env 未配置 STOCK_LIST），无法聚合行业研报"

        matched = []
        fallback = False
        for code in codes:
            try:
                reports, meta = fetch_research_reports(code, max_results=5)
                if meta.get("used_fallback"):
                    fallback = True
                for r in reports:
                    title = (r.title or "")
                    org = (r.org or "")
                    if sector_kw in title or sector_kw in org or sector_kw in (r.rating or ""):
                        matched.append({"symbol": code, "title": title,
                                        "org": org, "rating": r.rating,
                                        "date": r.date})
            except Exception:
                continue  # 单只失败跳过, 不中断

        if not matched:
            note = "（数据源降级/不可用）" if fallback else ""
            return f"自选股池未找到与「{sector_kw}」相关的研报{note}"

        lines = [f"📑 「{sector_kw}」板块研报（自选股池聚合, {len(matched)} 条）:"]
        for m in matched[:10]:
            lines.append(f"  • {m['symbol']} {m['title'][:45]}")
            if m.get("org"):
                lines.append(f"    {m['org']}{' ' + m['rating'] if m['rating'] else ''}"
                             f"{' (' + str(m['date'])[:10] + ')' if m['date'] else ''}")
        if fallback:
            lines.append("  ⚠️ 部分数据源降级（stub/缓存）")
        return "\n".join(lines)
    except Exception as e:
        return f"行业研报查询失败: {e}"


# ─── Phase 1 新增: 学习/识别类只读工具 (方案 v2.0 §4.2.1) ─────

def _lessons(conn) -> str:
    """交易教训 (outcomes.lesson 非空条目, 关联 trades 取 symbol)。"""
    try:
        cursor = conn.execute("""
            SELECT o.trade_id, COALESCE(t.symbol, ''), o.lesson_type, o.lesson
            FROM outcomes o
            LEFT JOIN trades t ON t.id = o.trade_id
            WHERE o.lesson IS NOT NULL AND o.lesson != ''
            ORDER BY o.trade_id DESC
            LIMIT 10
        """)
        rows = cursor.fetchall()
        if not rows:
            return "暂无交易教训"
        result = "最近10条交易教训:\n"
        for r in rows:
            result += f"  [{r[0]}] {r[1] or '-'} ({r[2] or 'general'}): {r[3][:60]}\n"
        return result
    except Exception as e:
        return f"教训查询失败: {e}"


def _net_value(conn) -> str:
    """净值/盈亏摘要。"""
    try:
        cursor = conn.execute("SELECT ts, total FROM net_values ORDER BY ts DESC LIMIT 2")
        rows = cursor.fetchall()
        if not rows:
            return "暂无净值记录"
        latest = rows[0]
        from datetime import datetime
        ts = datetime.fromtimestamp(latest[0]).strftime("%Y-%m-%d %H:%M")
        line = f"最新净值: {latest[1]:.2f} ({ts})\n"
        if len(rows) > 1:
            change = latest[1] - rows[1][1]
            line += f"  较上次: {change:+.2f} ({(change/rows[1][1]*100) if rows[1][1] else 0:+.2f}%)\n"
        # 累计盈亏
        cursor = conn.execute("SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM trades")
        cnt, pnl = cursor.fetchone()
        line += f"  累计交易: {cnt}笔 | 累计盈亏: {pnl:+.2f}"
        return line
    except Exception as e:
        return f"净值查询失败: {e}"


def _risk_events(conn) -> str:
    """风控拒绝事件。"""
    try:
        cursor = conn.execute("""
            SELECT symbol, rule_id, reason, ts
            FROM risk_rejections
            ORDER BY ts DESC
            LIMIT 10
        """)
        rows = cursor.fetchall()
        if not rows:
            return "暂无风控拒绝记录"
        result = "最近10条风控拒绝:\n"
        for r in rows:
            from datetime import datetime
            ts = datetime.fromtimestamp(r[3]).strftime("%m-%d %H:%M") if r[3] else "-"
            result += f"  [{ts}] {r[0] or '-'} {r[1]}: {r[2][:50]}\n"
        return result
    except Exception as e:
        return f"风控事件查询失败: {e}"


# ─── Phase 2: 动作工具桥接 (方案 v2.0 §4.3) ────────────────
# 走 quant_bridge (TradingSelf.judge 审核 + 二次确认 + fail-closed),
# Aris 规则引擎只传 (symbol, action, qty, rationale), 不接触 DB。

def _quant_bridge():
    """懒加载 quant_bridge 单例 (paper_trading 本地资产)。"""
    from laap.paper_trading.quant_bridge import get_bridge
    return get_bridge()


def _quant_decide(symbol: str, action: str, qty: int = 0,
                  rationale: str = "") -> str:
    """交易决策建议 (审核, 不下单)。"""
    try:
        r = _quant_bridge().use_decide(symbol, action, qty, rationale)
        if r.get("decision") == "approve":
            head = "✅ 建议：可以"
        elif r.get("decision") == "abstain":
            head = "⚠️ 建议：观望（有顾虑）"
        else:
            head = "⛔ 建议：不执行"
        # 输出友好化: action 转中文, qty 安全转 int (占位符替换可能传字符串)
        action_cn = {"buy": "买入", "sell": "卖出"}.get(action, action)
        qty_int = _coerce_qty(qty)
        qty_txt = f"{qty_int}股" if qty_int else "按建议仓位"
        symbol_txt = symbol if symbol and str(symbol).strip() not in ("{symbol}", "") else "该标的"
        reasons = "；".join(r.get("reasons", [])) or "无"
        # 停盘提示: 非交易日时附注 (建议层不阻断, 但让用户知道今天不可执行)
        market_note = "\n  ⏸️ 今日非 A 股交易日（周末/节假日停盘），确认后也无法下单" \
            if r.get("market_open") is False else ""
        return f"{head} {symbol_txt} {action_cn} {qty_txt}{market_note}\n  依据: {r.get('meaning','')}\n  顾虑: {reasons}"
    except Exception as e:
        return f"[决策不可用] {e}"


def _quant_execute(symbol: str, action: str, qty: int,
                   confirm_word: str = "") -> str:
    """确认执行下单 (需二次确认 + judge 通过)。"""
    try:
        # 2026-08-16: RuleStep params 字符串模板会把 qty 传成 "100" 字符串,
        # use_execute 里 qty*price 报 "can't multiply sequence by non-int"。
        # 入口做 int 防御转换 (最小改动, 不动规则定义)。
        qty = _coerce_qty(qty)
        r = _quant_bridge().use_execute(
            symbol=symbol, action=action, qty=qty, confirm_word=confirm_word)
        if r.get("executed"):
            return f"✅ 已执行 {symbol} {action} {qty}股: {r.get('status','')}"
        if r.get("status") == "need_confirmation":
            return "🔒 未执行：需要明确确认词（如 '确认执行'）"
        if r.get("status") == "judge_blocked":
            return f"⛔ 未执行：审核未通过 ({r.get('decision','')})：{r.get('reasons',[])}"
        return f"⛔ 未执行：{r.get('status', r.get('error',''))}"
    except Exception as e:
        return f"[执行不可用] {e}"


def _quant_close(symbol: str, qty: int = 0, confirm_word: str = "") -> str:
    """平仓 (需审核 + 确认)。"""
    try:
        # 2026-08-16: 同 _quant_execute, RuleStep 字符串模板会把 qty 传成 str。
        qty = _coerce_qty(qty)
        r = _quant_bridge().use_close(symbol, qty, confirm_word)
        if r.get("executed"):
            return f"✅ 已平仓 {symbol}: {r.get('status','')}"
        if r.get("status") == "need_confirmation":
            return "🔒 未平仓：需要明确确认词（如 '确认平仓'）"
        if r.get("status") == "judge_blocked":
            return f"⛔ 未平仓：审核未通过 ({r.get('decision','')})：{r.get('reasons',[])}"
        return f"⛔ 未平仓：{r.get('status', r.get('error',''))}"
    except Exception as e:
        return f"[平仓不可用] {e}"


def _coerce_qty(qty) -> int:
    """把 RuleStep 模板传参 (可能是 '0'/'100'/None/'') 安全转为 int。"""
    try:
        if qty is None:
            return 0
        s = str(qty).strip()
        if s in ("", "None", "0"):
            return 0
        return int(s)
    except (ValueError, TypeError):
        return 0


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
    # Phase 1 新增 (方案 v2.0 §4.2.1): 学习/识别类只读工具
    "pt_lessons": {"fn": lambda: _run("lessons"), "desc": "交易教训"},
    "pt_net_value": {"fn": lambda: _run("net_value"), "desc": "净值/盈亏摘要"},
    "pt_risk_events": {"fn": lambda: _run("risk_events"), "desc": "风控拒绝事件"},
    # Phase 2 新增 (方案 v2.0 §4.3): 动作工具 — 走 quant_bridge (TradingSelf 审核)
    "pt_decide": {"fn": lambda symbol, action, qty=0, rationale="": _quant_decide(symbol, action, qty, rationale), "desc": "交易决策建议(审核,不下单)"},
    "pt_execute": {"fn": lambda symbol, action, qty, confirm_word="": _quant_execute(symbol, action, qty, confirm_word), "desc": "确认执行下单(需二次确认)"},
    "pt_close": {"fn": lambda symbol, qty=0, confirm_word="": _quant_close(symbol, qty, confirm_word), "desc": "平仓(需审核+确认)"},
    # Phase 3 新增 (方案 v2.0 §4.4): 管理闭环工具
    "pt_brief": {"fn": lambda: _run("brief"), "desc": "每日交易简报"},
    "pt_evolution_audit": {"fn": lambda: _run("evolution_audit"), "desc": "进化治理提案"},
    "pt_watchlist": {"fn": lambda: _run("watchlist"), "desc": "列出我的自选股"},
    "pt_profile": {"fn": lambda symbol: _run("profile", symbol=symbol), "desc": "个股资料查询"},
    "pt_sector_reports": {"fn": lambda sector: _run("sector_reports", sector=sector), "desc": "行业/板块研报"},
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
