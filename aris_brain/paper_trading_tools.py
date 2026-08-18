# -*- coding: utf-8 -*-
"""
paper_trading 工具包 — LAAP内部实现

直接操作 laap_trading.db 数据库（零账户概念，单系统）。
"""

import sys, os, json, logging, time, hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, Any
import sqlite3

# LAAP paper_trading路径（从文件位置推导，跨平台；避免硬编码 Windows 盘符
# 路径在非 Windows 环境被当相对路径 → 项目根下生成 D:\laap-AGI 垃圾目录）
LAAP_ROOT = str(Path(__file__).resolve().parent.parent)
# 动态解析 laap_trading.db（2026-08-17）：优先 PAPER_TRADING_DB_PATH env，否则项目根 data/。
# 2026-08-18：非 Windows 平台忽略 Windows 盘符绝对路径（同 laap.paper_trading.db 守卫）。
# 测试可通过 monkeypatch DB_PATH 覆盖（如 tmp 路径）。
def _default_paper_db_path() -> str:
    from laap.paper_trading.db import default_db_path
    return default_db_path()


DB_PATH = _default_paper_db_path()
# 研报 md 源文件输出目录（YYYYMMDD_板块.md，量化按日期/板块读取）
REPORT_DIR = os.path.join(LAAP_ROOT, "report")

logger = logging.getLogger("aris_brain.paper_trading_tools")

if LAAP_ROOT not in sys.path:
    sys.path.insert(0, LAAP_ROOT)


def _db() -> sqlite3.Connection:
    """获取 laap_trading.db 连接（复用 PaperDB：幂等建 schema + 可注入 DB_PATH）。

    修复（2026-08-17）：
      - 用模块级 DB_PATH（测试可 monkeypatch 为 tmp 路径；默认 PaperDB 动态解析同源）。
      - 挂载盘（9p）/ 只读环境建表失败时降级为裸连接（尽力而为，工具容错返回），
        避免 schema 初始化失败把"可读查询"也打断。
    """
    from laap.paper_trading.db import PaperDB
    try:
        # 生产：不传 db_path → PaperDB 按 PAPER_TRADING_DB_BACKEND 走 PG（laap_trading），
        # PG 不可用自动回退 SQLite（laap_trading.db）。
        # 注意：显式传 db_path 会强制 SQLite（测试隔离纪律），生产必须省略。
        db = PaperDB()
        return db.conn()
    except Exception as e:
        logger.warning(f"paper_trading_tools._db schema init failed, raw connect: {e}")
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
        elif action == "news":
            return _news(kwargs.get("symbol", ""))
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


def _backtest(conn, strategy: str = "default") -> str:
    """运行回测（2026-08-18 修复：旧契约 run_backtest(strategy) 已失效）。

    新契约: 从 watchlist_kline_store 真库（PG 优先/SQLite 回退）加载标的 K 线
    close 序列 → BacktestRunner.run_backtest(price_series, STRATEGY_PARAMS) 全段回放
    → 返回指标 JSON。strategy 参数兼容：6 位数字视为股票代码，其余默认 600519。
    """
    try:
        from laap.paper_trading.backtest_runner import BacktestRunner
        from laap.paper_trading.strategy import STRATEGY_PARAMS
        import json as _json

        # 解析标的代码（兼容规则引擎传参）
        symbol = "600519"
        if isinstance(strategy, str) and strategy.strip().isdigit() \
                and len(strategy.strip()) == 6:
            symbol = strategy.strip()

        # 从 K 线真库加载 close 序列（watchlist_kline_store 模块，PG 优先）
        closes = []
        try:
            import sys as _sys
            _sys.path.insert(0, str(LAAP_ROOT))
            from watchlist_kline_store import get_kline
            code = ("sh" if symbol.startswith("6") else "sz") + symbol
            rows = get_kline(code, days=800)
            if rows:
                # rows: [(code, date, open, close, high, low, volume), ...] 或 dict
                if isinstance(rows[0], (dict, sqlite3.Row)):
                    closes = [float(r["close"]) for r in rows]
                else:
                    closes = [float(r[3]) for r in rows]
        except Exception as e:
            logger.warning(f"pt_backtest kline load fallback: {e}")

        # 回退：real_data 真实 K 线 JSON
        if not closes:
            import os
            rj = os.path.join(str(LAAP_ROOT), "real_data", f"kline_{symbol}.json")
            if os.path.exists(rj):
                import json as _json2
                closes = _json2.load(open(rj, encoding="utf-8"))
        if not closes:
            return f"回测失败: 未找到 {symbol} 的K线数据（真库与 real_data 均无）"

        runner = BacktestRunner()
        result = runner.run_backtest(closes, STRATEGY_PARAMS, split=None)
        out = {k: (round(v, 4) if isinstance(v, float) else v)
               for k, v in result.items()}
        out["symbol"] = symbol
        out["bars"] = len(closes)
        return _json.dumps(out, ensure_ascii=False, indent=2)
    except ImportError as e:
        return f"回测模块未就绪，请检查配置: {e}"
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
        import time as _time
        result = "📊 今日交易简报\n"
        # 今日零点时间戳（PG/SQLite 通用：不用 date() 函数，参数化比较）
        lt = _time.localtime()
        today_start = _time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                    0, 0, 0, 0, 0, -1))
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
        # 2. 今日盈亏 (按日期统计 trades, 参数化时间戳)
        cursor = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM trades "
            "WHERE entry_ts >= ? AND entry_ts < ?",
            (today_start, today_start + 86400))
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
        # 5. 风控事件 (今日, 参数化时间戳)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM risk_rejections "
            "WHERE ts >= ? AND ts < ?",
            (today_start, today_start + 86400))
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
            # 2026-08-16: 主营业务截断后附东财 F10 详情链接（点击查看完整内容）
            _code = f"SH{symbol}" if str(symbol).startswith(("6", "9")) else f"SZ{symbol}"
            lines.append(
                f"  🔗 详情: https://emweb.securities.eastmoney.com/PC_HSF10/"
                f"CompanySurvey/Index?type=web&code={_code}")
        if meta.get("used_fallback"):
            lines.append("  ⚠️ 数据源降级（stub/缓存）")
        return "\n".join(lines)
    except Exception as e:
        return f"个股资料查询失败: {e}"


def _sector_kw_tokens(kw: str) -> list:
    """复合板块关键词拆解：整词 + 2 字滑动窗口子词（去重保序）。

    例：'新能源材料' → ['新能源材料','新能','能源','源材','材料']。
    仅对长度 ≥4 的关键词展开（'白酒'/'机器人' 等短词保持整词精确匹配，
    避免过度放宽）。供板块研报在整词未命中时按相关子词聚合（如研报标题
    只含 '新能源' 或 '材料'）。
    """
    kw = str(kw or "").strip()
    tokens = [kw]
    if len(kw) >= 4:
        tokens += [kw[i:i + 2] for i in range(len(kw) - 1)]
    seen = []
    for t in tokens:
        if t and t not in seen:
            seen.append(t)
    return seen


# ── 板块→典型公司名映射（研报标题常不含板块词但含公司名，命中即视为板块）──
_SECTOR_COMPANIES = {
    "白酒": ["茅台", "五粮液", "泸州老窖", "洋河", "汾酒", "古井", "今世缘", "舍得", "酒鬼", "水井坊", "金种子", "迎驾"],
    "白酒股": ["茅台", "五粮液", "泸州老窖", "洋河", "汾酒", "古井", "今世缘", "舍得", "酒鬼", "水井坊"],
    "新能源": ["宁德", "比亚迪", "隆基", "阳光电源", "亿纬", "赣锋", "天齐", "通威", "晶澳"],
    "新能源车": ["比亚迪", "宁德", "理想", "蔚来", "小鹏", "赛力斯", "长安汽车"],
    "半导体": ["中芯", "韦尔", "北方华创", "中微", "兆易", "澜起", "寒武纪", "海光"],
    "人工智能": ["科大讯飞", "海康", "大华", "昆仑万维", "三六零", "商汤"],
    "医药": ["恒瑞", "药明", "迈瑞", "爱尔", "片仔癀", "云南白药", "复星医药"],
    "券商": ["中信证券", "东方财富", "华泰", "国泰君安", "招商证券"],
    "银行": ["招商银行", "工商银行", "建设银行", "农业银行", "兴业银行", "平安银行"],
    "军工": ["中航", "航天", "中国船舶", "航发"],
}

# 结构化研报进程内缓存（sector_kw → (ts, text)）
_SECTOR_REPORT_CACHE: Dict[str, tuple] = {}
_SECTOR_REPORT_TTL = 1800
# 研报输出字符上限（用户要求 ≤2000 字）
MAX_SECTOR_REPORT_CHARS = 2000


def _truncate_report(text: str, limit: int = MAX_SECTOR_REPORT_CHARS) -> str:
    """硬截断兜底：超限时保留前 limit 字符并标注。"""
    if len(text) <= limit:
        return text
    return text[:limit] + "…（超长已截断）"


def _clip(s: str, n: int) -> str:
    """按字符截断并加省略号（防截断处误导）。"""
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


# 研报落库 schema（sector_reports 表，report_hash=sha1(content) 主键，幂等）
_SECTOR_REPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_reports (
    report_hash TEXT PRIMARY KEY,
    sector TEXT NOT NULL,
    content TEXT NOT NULL,
    file_path TEXT DEFAULT '',
    char_count INTEGER DEFAULT 0,
    created_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sector_reports_sector ON sector_reports(sector);
CREATE INDEX IF NOT EXISTS idx_sector_reports_created ON sector_reports(created_ts);
"""


def _sector_report_filename(sector: str, report_hash: str = "") -> str:
    """研报源文件名：YYYYMMDD_板块_<hash8>.md（同日多版本保留，内容哈希前 8 位）。

    板块名做文件系统安全清洗（`新能源 材料/锂电` → `新能源_材料_锂电`），
    量化按日期前缀 + 板块名 + 哈希读取/归档；同内容同哈希 → 同名覆盖（去重），
    不同内容 → 不同哈希 → 同日多版本并存。
    """
    import re as _re
    safe = _re.sub(r'[\\/:*?"<>|\s]+', "_", str(sector or "").strip()) or "unknown"
    h = str(report_hash or "")[:8]
    suffix = f"_{h}" if h else ""
    return f"{datetime.now().strftime('%Y%m%d')}_{safe}{suffix}.md"


def _persist_sector_report(sector: str, content: str) -> tuple:
    """研报落库（sha1 哈希主键）+ md 源文件保存 report/。

    Returns: (report_hash, fname)；失败返回 (hash, "")（best-effort，不阻塞返回）。
    同内容哈希去重：内容不变不重复写盘/入库。
    """
    report_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()
    fname = _sector_report_filename(sector, report_hash)
    try:
        Path(REPORT_DIR).mkdir(parents=True, exist_ok=True)
        fpath = os.path.join(REPORT_DIR, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content + "\n")
        try:
            conn = _db()
            try:
                conn.executescript(_SECTOR_REPORT_SCHEMA)
                conn.execute(
                    "INSERT OR IGNORE INTO sector_reports "
                    "(report_hash, sector, content, file_path, char_count, created_ts) "
                    "VALUES (?,?,?,?,?,?)",
                    (report_hash, sector, content, fpath, len(content), time.time()))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"_persist_sector_report db failed: {e}")
        return report_hash, fname
    except Exception as e:
        logger.warning(f"_persist_sector_report failed: {e}")
        return report_hash, ""


def _collect_sector_evidence(sector_kw: str, tokens: list,
                             company_hits: list) -> Dict[str, Any]:
    """拉自选股池研报并按板块词/典型公司名过滤，返回证据字典（fail-closed）。"""
    from laap.paper_trading.news_intel import fetch_research_reports
    from dotenv import load_dotenv
    load_dotenv(os.path.join(LAAP_ROOT, ".env"))
    raw = os.environ.get("STOCK_LIST", "") or ""
    codes = [c.strip() for c in raw.split(",") if c.strip()]
    ev: Dict[str, Any] = {"sector_kw": sector_kw, "tokens": tokens,
                          "codes": codes, "matched": [],
                          "matched_tokens": set(),
                          "degraded_codes": [], "no_data_codes": []}
    for code in codes:
        try:
            reports, meta = fetch_research_reports(code, max_results=5)
            if meta.get("used_fallback") and not reports:
                # 区分「数据源降级」与「该股无研报」：no_data=True 表示源可用但无数据
                if meta.get("no_data"):
                    ev["no_data_codes"].append(code)
                else:
                    ev["degraded_codes"].append(code)
            for r in reports:
                blob = (r.title or "") + (r.org or "") + (r.rating or "")
                hit_tokens = [t for t in tokens if t in blob]
                hit_companies = [c for c in company_hits if c in blob]
                if hit_tokens or hit_companies:
                    ev["matched"].append({"symbol": code, "title": (r.title or ""),
                                          "org": r.org, "rating": r.rating,
                                          "date": r.date})
                    ev["matched_tokens"].update(hit_tokens)
                    ev["matched_tokens"].update(hit_companies)
        except Exception:
            continue  # 单只失败跳过, 不中断
    return ev


def _build_sector_llm_call():
    """板块研报 LLM 合成器（惰性构建）。

    `LAAP_SECTOR_REPORT_LLM=0` 关闭（确定性兜底）；构建失败/不可用 → None。
    """
    if os.environ.get("LAAP_SECTOR_REPORT_LLM", "1") == "0":
        return None
    try:
        from laap.paper_trading.llm_sources import build_llm_call
        return build_llm_call()
    except Exception as e:
        logger.warning(f"_build_sector_llm_call unavailable: {e}")
        return None


def _synthesize_sector_logic(sector_kw: str, ev: Dict[str, Any],
                             llm_call) -> str:
    """一、板块定位与核心驱动：LLM 合成；不可用 → 确定性兜底（fail-closed，不伪造）。"""
    try:
        if llm_call is None:
            raise RuntimeError("llm unavailable")
        syms = sorted({m["symbol"] for m in ev["matched"]})
        lines = [f"板块: {sector_kw}",
                 f"自选股池命中 {len(syms)} 只标的: {', '.join(syms)}"]
        for m in ev["matched"][:8]:
            lines.append(f"- {m['symbol']} {m['title'][:60]} ({m['org']})")
        prompt = (
            "你是A股行业研究员。基于以下自选股池研报证据，输出一段「板块定位与核心驱动」"
            "分析（产业逻辑、供需格局、驱动因素），**全段控制在 150 字以内**。"
            "只依据提供证据与通用行业认知，禁止编造具体数据或证据外的标的；"
            "若证据覆盖有限，须明确说明'自选股池覆盖有限'。\n\n"
            + "\n".join(lines))
        text = llm_call(prompt, system="只输出分析正文，不输出标题/序号/多余解释。",
                        max_tokens=600)
        text = (text or "").strip()
        if len(text) < 40:
            raise RuntimeError("llm output too short")
        return _truncate_report(text, 160)
    except Exception as e:
        logger.warning(f"_synthesize_sector_logic fallback: {e}")
        n = len(ev["matched"])
        syms = sorted({m["symbol"] for m in ev["matched"]})
        return (f"自选股池命中 {n} 条研报、{len(syms)} 只标的；产业逻辑自动梳理"
                f"暂不可用，以下为证据层与框架层梳理。")


def _format_sector_report_full(sector_kw: str, ev: Dict[str, Any],
                               llm_call=None) -> str:
    """四段式结构化研报：一、板块定位与核心驱动；二、关键细分方向梳理（证据）；
    三、选股/选赛道框架；四、风险提示。结果按 sector_kw 缓存 TTL。"""
    hit = _SECTOR_REPORT_CACHE.get(sector_kw)
    if hit and (time.time() - hit[0]) < _SECTOR_REPORT_TTL:
        return hit[1]

    matched = ev["matched"]
    # 证据按标的分组（公司/行业 best-effort 取自个股资料，失败不阻塞）
    from laap.paper_trading.news_intel import fetch_stock_profile
    by_symbol: Dict[str, Any] = {}
    for m in matched:
        sym = m["symbol"]
        g = by_symbol.setdefault(sym, {"reports": [], "company": "", "industry": ""})
        g["reports"].append(m)
        if not g["company"]:
            try:
                prof, _ = fetch_stock_profile(sym)
                if prof:
                    g["company"] = prof.company_name or ""
                    g["industry"] = prof.industry or ""
            except Exception:
                pass

    title = f"# 🤖「{sector_kw}」板块研报（自选股池聚合, {len(matched)} 条）"
    if set(ev["tokens"]) - {sector_kw}:
        hit_sub = sorted(t for t in ev["matched_tokens"] if t != sector_kw)
        if hit_sub:
            title += f"（关键词拆解命中：{'/'.join(hit_sub)}）"

    # 一、板块定位与核心驱动
    sec1 = _synthesize_sector_logic(sector_kw, ev, llm_call)

    # 二、关键细分方向梳理（自选股池研报证据，≤6 条控字数）
    sec2_lines = []
    budget = 6
    total_reports = len(matched)
    for sym, g in by_symbol.items():
        head = f"### {sym}" + (f" {_clip(g['company'], 12)}" if g["company"] else "")
        if g.get("industry"):
            head += f"（{_clip(g['industry'], 14)}）"
        sec2_lines.append(head)
        for r in g["reports"][:budget]:
            meta = " | ".join(x for x in (r.get("org"), r.get("rating"),
                                          str(r.get("date", ""))[:10]) if x)
            sec2_lines.append(f"- {_clip(r['title'], 28)}" + (f"　{meta}" if meta else ""))
            budget -= 1
        if budget <= 0:
            break
    if budget <= 0 and total_reports > 6:
        sec2_lines.append(f"（其余 {total_reports - 6} 条略）")
    if len(by_symbol) <= 1:
        sec2_lines.append("（⚠️ 自选股池内直接相关标单一，覆盖有限，需人工补源）")

    # 三、当前阶段的选股/选赛道框架
    sec3_lines = [
        "三层筛选：①供需格局（竞争格局好/出清尾声，证据内买入增持占比高者优先）；"
        "②盈利弹性（成本降、价格钝化的盈利修复环节）；③技术溢价（确定性技术打底 + 主题技术埋伏渗透率拐点）。",
    ]
    ratings = [m.get("rating", "") for m in matched if m.get("rating")]
    if ratings:
        bull = sum(1 for r in ratings if any(k in r for k in ("买入", "增持", "推荐", "买")))
        sec3_lines.append(f"（证据内评级口径：{bull}/{len(ratings)} 条偏多）")

    # 四、风险提示（编号连续）
    sec4_lines = [
        "终端需求（下游放量/宏观）不及预期；",
        "产能出清慢于预期、加工费继续下探；",
        "技术路线切换的资产减值风险；",
        "海外关税/贸易政策超预期收紧。",
    ]
    if ev["degraded_codes"]:
        sec4_lines.append(
            f"⚠️ 部分数据源降级/不可用（{len(ev['degraded_codes'])} 只标的，未纳入证据）。")
    if ev["no_data_codes"]:
        sec4_lines.append(
            f"ℹ️ {len(ev['no_data_codes'])} 只标的暂无研报（源可用但无数据）。")
    sec4 = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(sec4_lines))

    footer = ("\n> 数据来源：自选股池研报（东财源，实拉）+ 个股资料；产业逻辑由 LLM 合成"
              "（不可用时为确定性兜底）。本报告为研究参考，不构成投资建议。")
    out = "\n\n".join([
        title,
        "## 一、板块定位与核心驱动\n" + sec1,
        "## 二、关键细分方向梳理（自选股池研报证据）\n" + "\n".join(sec2_lines),
        "## 三、当前阶段的选股/选赛道框架\n" + "\n".join(sec3_lines),
        "## 四、风险提示\n" + sec4,
        footer,
    ])
    out = _truncate_report(out, MAX_SECTOR_REPORT_CHARS)  # 硬上限 ≤2000 字
    # 研报落库（sha1 哈希）+ md 源文件保存 report/（best-effort，不阻塞返回）
    try:
        _hash, _fname = _persist_sector_report(sector_kw, out)
        if _fname:
            out = _truncate_report(
                out + f"\n> 📁 已保存 {os.path.join('report', _fname)}（hash={_hash[:8]}）",
                MAX_SECTOR_REPORT_CHARS)
    except Exception as e:
        logger.warning(f"sector report persist failed: {e}")
    _SECTOR_REPORT_CACHE[sector_kw] = (time.time(), out)
    return out


def _sector_reports(sector: str = "") -> str:
    """行业/板块研报: 对自选股池拉研报, 按板块关键词过滤聚合，输出四段式结构化研报。

    一、板块定位与核心驱动（LLM 合成，不可用则确定性兜底）
    二、关键细分方向梳理（自选股池研报证据，按标的分组）
    三、当前阶段的选股/选赛道框架
    四、风险提示（含数据覆盖诚实标注）

    复合板块名（如 '新能源材料'）整词未命中时，按 2 字窗口子词 OR 匹配
    （'新能源'/'材料' 等）；典型公司名命中视为板块命中。沙箱/离线: 源失败 →
    used_fallback，返回提示 (fail-closed, 不伪造)。
    """
    try:
        if not sector or str(sector).strip() in ("{sector}", ""):
            return "请提供行业/板块名，如 '列出 白酒 行业板块研报'"
        sector_kw = str(sector).strip()
        # 短窗口缓存优先（避免重复拉研报 + LLM 合成）
        hit = _SECTOR_REPORT_CACHE.get(sector_kw)
        if hit and (time.time() - hit[0]) < _SECTOR_REPORT_TTL:
            return hit[1]
        tokens = _sector_kw_tokens(sector_kw)
        company_hits = _SECTOR_COMPANIES.get(sector_kw, [])
        ev = _collect_sector_evidence(sector_kw, tokens, company_hits)
        if not ev["matched"]:
            notes = []
            if ev["degraded_codes"]:
                notes.append(f"部分数据源降级/不可用（{len(ev['degraded_codes'])} 只标的）")
            if ev["no_data_codes"]:
                notes.append(f"{len(ev['no_data_codes'])} 只标的暂无研报")
            note = f"（{'；'.join(notes)}）" if notes else ""
            return f"自选股池未找到与「{sector_kw}」相关的研报{note}"
        llm_call = _build_sector_llm_call()
        return _format_sector_report_full(sector_kw, ev, llm_call)
    except Exception as e:
        return f"行业研报查询失败: {e}"


def _news(symbol: str = "", max_results: int = 5) -> str:
    """个股新闻查询：**数据库 news_items 优先**，实时源兜底 (fail-closed)。

    2026-08-16 修改：原实现只走 fetch_stock_news 多源链，实时源全挂时
    （sina 'list' bug / cls 404 / 各源无 key）返回"未获取到"——但数据库
    news_items 里明明有日终管线存的新闻。改为数据库优先：有数据直接回，
    无数据才尝试实时抓取。
    """
    try:
        if not symbol or str(symbol).strip() in ("{symbol}", ""):
            return "请提供股票代码，如 '查询 600519 个股新闻'"
        sym = str(symbol).strip()
        conn = _db()
        try:
            # 数据库优先：news_items 按 symbol 匹配（含标题/内容里的股票名）
            rows = conn.execute(
                """SELECT symbol, title, content, source, published_at
                   FROM news_items
                   WHERE symbol = ? OR title LIKE ? OR content LIKE ?
                   ORDER BY published_at DESC
                   LIMIT ?""",
                (sym, f"%{sym}%", f"%{sym}%", max_results),
            ).fetchall()
        finally:
            conn.close()
        if rows:
            lines = [f"📰 {sym} 最近新闻（{len(rows)} 条，数据库）:"]
            for i, r in enumerate(rows, 1):
                title = (r["title"] or "").strip()
                if not title:
                    continue
                ts = str(r["published_at"] or "")[:16].replace("T", " ")
                src = r["source"] or "未知"
                lines.append(f"  {i}. {title[:50]}")
                if ts:
                    lines.append(f"     [{src}] {ts}")
            return "\n".join(lines)

        # 数据库无数据 → 实时源兜底
        from laap.paper_trading.news_intel import fetch_stock_news
        items, meta = fetch_stock_news(sym, max_results=max_results)
        if not items:
            fallback = "（数据源降级/不可用）" if meta.get("used_fallback") else ""
            return f"未获取到 {sym} 的新闻{fallback}"
        lines = [f"📰 {sym} 最近新闻（{len(items)} 条）:"]
        for i, it in enumerate(items, 1):
            title = (it.title or "").strip()
            if not title:
                continue
            ts = (it.published_at or "")[:16].replace("T", " ")
            src = it.source or ""
            lines.append(f"  {i}. {title[:50]}")
            if ts or src:
                lines.append(f"     [{src or '未知'}] {ts}")
        if meta.get("used_fallback"):
            lines.append("  ⚠️ 部分数据源降级（stub/缓存）")
        return "\n".join(lines)
    except Exception as e:
        return f"个股新闻查询失败: {e}"


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
    "pt_news": {"fn": lambda symbol: _run("news", symbol=symbol), "desc": "个股新闻查询"},
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
