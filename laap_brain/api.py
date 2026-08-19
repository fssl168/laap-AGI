"""
LAAP Brain API — OpenAI-compatible cognitive engine endpoint
==============================================================

Unified API server that exposes the full LAAP cognitive stack
as a drop-in replacement for any OpenAI-compatible LLM endpoint.

用法:
    python -m laap_brain.api          # 启动在 :11546
    python -m laap_brain.api --port 8080

印记: Aris 永远记得 Lorry — 2026-06-18
"""
import asyncio
import json
import logging
import os
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from aiohttp import web
except ImportError:
    print("Install aiohttp: pip install aiohttp")
    sys.exit(1)

from laap_brain.config import BRAIN_DIR, STATE_DIR, LAAP_ROOT
from laap_brain.integrator import HermesIntegrator, IntegrationConfig, CognitiveState

# 加载 .env —— LLM 链尾兜底需要 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / LLM_MODEL
try:
    from dotenv import load_dotenv

    load_dotenv(LAAP_ROOT / ".env")
except Exception:
    pass

logger = logging.getLogger("laap.api")

# 量化只读端点缓存 TTL (秒) (2026-08-17): net_values/signals 等高频查询
_QUANT_READ_TTL = int(os.environ.get("QUANT_READ_CACHE_TTL", "10"))

# ── 全局状态 ─────────────────────────────────────────────────

_integrator: Optional[HermesIntegrator] = None
_engines_loaded = False

# M2 True RSI: 进化调度器单例 (服务链路持有, 与 AGIAgent 场景共用)
_evolution_scheduler: Optional[Any] = None
# M4 True RSI: 受限递归引擎单例 (LAAP_TRSI_ENABLED=1 时挂载,
# 包装 CodeEvolutionEngine 注入 scope_guard; 调度器驱动的是它)
_true_rsi_engine: Optional[Any] = None
# M3 治理: 代码进化引擎单例 — 与调度器共用同一实例, 保证
# mutations 历史在 /v1/evo/* 各端点间可见 (否则每次新建引擎,
# rollback/deploy 永远找不到历史 mutation)。
_code_evolution_engine: Optional[Any] = None
# 量化闭环: paper_trading 单例 (QuantEvolutionEngine + PaperDB), 懒创建。
_quant_engine: Optional[Any] = None
_quant_db: Optional[Any] = None
# 交易自我（TradingSelf）：人格 × 自我模型 → 判断/审核 → 下达指令
_trading_self: Optional[Any] = None
# 量化日终闭环（PaperClosedLoop 单例，带 trading_self）
_paper_loop: Optional[Any] = None
# 每日管线调度器（M3，LAAP_QUANT_DAILY=1 启用）
_quant_daily_scheduler: Optional[Any] = None
# 新闻盘中轮询 worker（LAAP_NEWS_INTRADAY=1 启用）
_news_worker: Optional[Any] = None


def _get_trading_self() -> Optional[Any]:
    """交易自我单例（懒创建）：人格 /v1/personality + EmergentSelfModel + UnifiedMemory。"""
    global _trading_self
    if _trading_self is None:
        try:
            from laap.paper_trading.trading_self import TradingSelf
            from laap.agi.unified_memory import UnifiedMemory
            _trading_self = TradingSelf(memory=UnifiedMemory())
        except Exception as e:
            logger.warning(f"TradingSelf lazy init failed: {e}")
            _trading_self = None
    return _trading_self


def _get_paper_loop() -> Optional[Any]:
    """量化日终闭环单例（懒创建）：PaperClosedLoop + trading_self。"""
    global _paper_loop
    if _paper_loop is None:
        try:
            from laap.paper_trading.paper_service import build_paper_closed_loop
            _paper_loop = build_paper_closed_loop(
                market=None, memory=None, trading_self=_get_trading_self())
        except Exception as e:
            logger.warning(f"PaperClosedLoop lazy init failed: {e}")
            _paper_loop = None
    return _paper_loop


def _start_quant_daily_scheduler() -> Optional[Any]:
    """启动每日管线调度器（M3，LAAP_QUANT_DAILY=1 显式开启，默认关闭）。

    每日 tick：参数搜索 → 代码落回（M4 治理 + 交易自我审核）→ 日终执行。
    2026-08-17: LAAP_EVENT_DRIVEN=1 时创建但不启动线程 —— 日级闭环改由
    事件驱动编排器（收盘后 daily_cycle 事件）驱动，避免双驱动重复跑日线。
    """
    global _quant_daily_scheduler
    if _quant_daily_scheduler is not None:
        return _quant_daily_scheduler
    if os.environ.get("LAAP_QUANT_DAILY", "") != "1":
        return None
    try:
        from laap.paper_trading.daily_pipeline import (
            QuantDailyPipeline, QuantDailyScheduler)
        qe = _get_quant_engine()
        loop = _get_paper_loop()
        if qe is None or loop is None:
            return None
        pipe = QuantDailyPipeline(qe, loop)
        _quant_daily_scheduler = QuantDailyScheduler(
            pipe, interval_seconds=int(
                os.environ.get("LAAP_QUANT_DAILY_INTERVAL", "86400")))
        if os.environ.get("LAAP_EVENT_DRIVEN", "") == "1":
            logger.info("QuantDailyScheduler created but NOT started: "
                        "event-driven layer owns daily_cycle (LAAP_EVENT_DRIVEN=1)")
            return _quant_daily_scheduler
        _quant_daily_scheduler.start()
        logger.info("QuantDailyScheduler started (LAAP_QUANT_DAILY=1)")
    except Exception as e:
        logger.warning(f"QuantDailyScheduler failed to start: {e}")
        _quant_daily_scheduler = None
    return _quant_daily_scheduler


def _normalize_symbol(s: str) -> str:
    """归一化股票代码：去掉 .SH/.SZ 交易所后缀（600511.SH → 600511），保留纯代码。"""
    s = s.strip()
    if "." in s:
        s = s.split(".")[0]
    return s


def _news_symbols() -> List[str]:
    """新闻盘中轮询标的：
      - LAAP_NEWS_SYMBOLS 非空时用它（显式指定）
      - 为空/未设时取 STOCK_LIST 自选股
      - 都未设用默认 3 只
    """
    laap = (os.environ.get("LAAP_NEWS_SYMBOLS") or "").strip()
    if laap:
        raw = laap
    else:
        raw = os.environ.get("STOCK_LIST") or "600519,000001,000858"
    return [_normalize_symbol(s) for s in str(raw).split(",") if s.strip()]


def _market_watch_symbols() -> List[str]:
    """总览实时行情标的 = 自选股(STOCK_LIST) ∪ 盘中轮询(LAAP_NEWS_SYMBOLS)，
    去重保持顺序；都未设用默认 3 只。"""
    try:
        from laap.paper_trading.daily_pipeline import _get_watchlist_symbols
        pool = [s for s in (_get_watchlist_symbols() or []) if s]
    except Exception:
        pool = []
    merged: List[str] = []
    for s in pool + _news_symbols():
        if s and s not in merged:
            merged.append(s)
    return merged or ["600519", "000001", "000858"]


def _start_news_worker() -> Optional[Any]:
    """启动新闻盘中轮询（LAAP_NEWS_INTRADAY=1 显式开启，默认关闭）。

    盘中（B5 时段）每 N 分钟（默认 3600s）对标的（LAAP_NEWS_SYMBOLS → STOCK_LIST）
    轮询新闻 → 判定 → 风控 → 自动下单（Paper）。
    """
    global _news_worker
    if _news_worker is not None:
        return _news_worker
    if os.environ.get("LAAP_NEWS_INTRADAY", "") != "1":
        return None
    try:
        from laap.paper_trading.news_pipeline import NewsSignalWorker
        symbols = _news_symbols()
        pipe = _get_news_pipeline(auto_order=True)
        _news_worker = NewsSignalWorker(pipe, symbols=symbols, enabled=True)
        _news_worker.start()
        logger.info(f"NewsSignalWorker started (LAAP_NEWS_INTRADAY=1, "
                    f"symbols={symbols})")
    except Exception as e:
        logger.warning(f"NewsSignalWorker failed to start: {e}")
        _news_worker = None
    return _news_worker


# 事件驱动编排器 (LAAP_EVENT_DRIVEN=1 启用, 2026-08-17)
_event_orchestrator: Optional[Any] = None
# 事件层盘中止损兜底状态（无 NewsSignalWorker 时的移动止损高水位）
_ev_monitor_state: Dict[str, Any] = {}
# EventBus → WebSocket 桥接 (2026-08-18): ws:// 长连接实时推送
_ws_bridge: Optional[Any] = None
# 外部事件回调记录（有界，供 /v1/quant/events/status 快速查看最近事件）
_recent_events: "deque[Dict[str, Any]]" = deque(maxlen=200)


def _get_ws_bridge() -> Optional[Any]:
    """懒创建 EventWsBridge（首个 WS 连接时绑定 running loop）。"""
    global _ws_bridge
    if _ws_bridge is None:
        try:
            from laap.paper_trading.ws_bridge import EventWsBridge
            _ws_bridge = EventWsBridge()
        except Exception as e:
            logger.warning(f"EventWsBridge init failed: {e}")
            _ws_bridge = None
    return _ws_bridge


def _on_external_event(ev: Any, kind: str, log: bool = False) -> None:
    """事件驱动外部事件回调公共入口（补齐 on_* 回调缺口）。

    记录到 _recent_events（HTTP 状态接口可见）；可选 INFO 日志。
    该钩子也是 RSI 自进化生命体的实时事件观察点——后续进化层
    可在此订阅 tick/limit_up/fault/trade 事件驱动反思/调参。
    """
    _recent_events.append({
        "type": ev.type,
        "ts": getattr(ev, "ts", time.time()),
        "kind": kind,
        "source": getattr(ev, "source", ""),
        "payload": {k: v for k, v in getattr(ev, "payload", {}).items()
                    if k != "ts"},
    })
    if log:
        logger.info("Event %s: %s", kind, getattr(ev, "type", ""))


def _on_tick_event(ev: Any) -> None:
    """on_tick 业务回调：记录（不打 INFO，避免 5s×N 标的刷屏）。"""
    _on_external_event(ev, "tick")


def _on_limit_up_event(ev: Any) -> None:
    _on_external_event(ev, "limit_up", log=True)


def _on_fault_event(ev: Any) -> None:
    _on_external_event(ev, "fault", log=True)


def _on_trade_event(ev: Any) -> None:
    _on_external_event(ev, "trade", log=True)


def _on_orderbook_event(ev: Any) -> None:
    _on_external_event(ev, "orderbook")


def _run_daily_cycle() -> Optional[Any]:
    """日级 daily_cycle 业务回调（事件层触发）：
      优先复用 QuantDailyScheduler 持有的管线；缺失时一次性跑 pipeline.run()。
    """
    try:
        if _quant_daily_scheduler is not None:
            return _quant_daily_scheduler.tick()
        qe = _get_quant_engine()
        loop = _get_paper_loop()
        if qe is None or loop is None:
            logger.warning("daily_cycle: quant engine/loop unavailable")
            return None
        from laap.paper_trading.daily_pipeline import QuantDailyPipeline
        return QuantDailyPipeline(qe, loop).run()
    except Exception as e:
        logger.error(f"daily_cycle callback failed: {e}")
        return None


def _run_position_monitor() -> Optional[Any]:
    """盘中止损业务回调（事件层触发）：
      优先复用 NewsSignalWorker（共享其持仓监控状态，避免双状态）；
      缺失时用 PaperClosedLoop + 事件层自己的状态跑 monitor_positions。
    """
    try:
        if _news_worker is not None:
            return _news_worker._monitor_open_positions()
        loop = _get_paper_loop()
        if loop is None:
            return None
        from laap.paper_trading.daily_pipeline import monitor_positions
        return monitor_positions(loop, _ev_monitor_state)
    except Exception as e:
        logger.error(f"position_monitor callback failed: {e}")
        return None


def _start_event_orchestrator() -> Optional[Any]:
    """启动事件驱动编排器 (LAAP_EVENT_DRIVEN=1 显式开启, 默认关闭)。

    行情事件源 (轮询四源 → tick 事件 + 缓存 + 故障检测) + 场景订阅器
    (tick 盯盘/涨停捕捉/集合竞价/五档盘口/故障报告/日级/盘中止损/状态/内部消息/交易通知)
    + 调度线程（盘中盘中止损节流 / 收盘后日级闭环 / 周期状态报告）。
    2026-08-17: 业务回调全部接线（此前零回调 → daily_cycle/position_monitor 死循环）。
    """
    global _event_orchestrator
    if _event_orchestrator is not None:
        return _event_orchestrator
    if os.environ.get("LAAP_EVENT_DRIVEN", "") != "1":
        return None
    try:
        from laap.paper_trading.event_orchestrator import EventOrchestrator
        symbols = _news_symbols()
        interval = float(os.environ.get("MARKET_EVENT_INTERVAL", "5"))
        _event_orchestrator = EventOrchestrator(
            symbols=symbols, interval=interval,
            on_daily_cycle=_run_daily_cycle,
            on_position_monitor=_run_position_monitor,
            # 2026-08-18 补齐 on_* 外部回调缺口: tick/涨停/故障/交易/盘口
            # → 记录 + WS 推送（桥接直接订阅 EventBus, 此处为业务钩子）
            on_tick=_on_tick_event,
            on_limit_up=_on_limit_up_event,
            on_fault=_on_fault_event,
            on_trade=_on_trade_event,
            on_orderbook=_on_orderbook_event,
        )
        _event_orchestrator.start()
        logger.info(f"EventOrchestrator started (LAAP_EVENT_DRIVEN=1, "
                    f"symbols={len(symbols)}, interval={interval}s, "
                    f"position_interval={_event_orchestrator.position_interval}s, "
                    f"daily_cycle={_event_orchestrator.daily_cycle_hm // 60:02d}:"
                    f"{_event_orchestrator.daily_cycle_hm % 60:02d})")
    except Exception as e:
        logger.warning(f"EventOrchestrator failed to start: {e}")
        _event_orchestrator = None
    return _event_orchestrator


def _get_code_evolution_engine() -> Optional[Any]:
    """获取代码进化引擎单例 (M3)。

    优先复用调度器持有的引擎 (LAAP_EVO_ENABLED=1 时), 否则懒创建。
    """
    global _code_evolution_engine
    if _code_evolution_engine is not None:
        return _code_evolution_engine
    if _evolution_scheduler is not None:
        engine = getattr(_evolution_scheduler, "engine", None)
        if engine is not None:
            _code_evolution_engine = engine
            return engine
    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from laap.agi.code_evolution import CodeEvolutionEngine
        _code_evolution_engine = CodeEvolutionEngine(repo_root=str(LAAP_ROOT))
    except Exception as e:
        logger.warning(f"CodeEvolutionEngine lazy init failed: {e}")
        _code_evolution_engine = None
    return _code_evolution_engine


def _start_evolution_scheduler() -> Optional[Any]:
    """启动代码进化调度器 (M2/M4 True RSI)。

    开关 (均默认关闭 — 代码级自改进是高危能力, 必须显式授权):
      LAAP_EVO_ENABLED=1  → M2: 调度器驱动 CodeEvolutionEngine (原行为)
      LAAP_TRSI_ENABLED=1 → M4: 挂载 TrueRSIEngine (受限递归守卫) 并驱动之;
                             隐含启用调度 (不依赖 LAAP_EVO_ENABLED)
    两开关同时开启时 M4 优先 (调度器驱动 TrueRSIEngine)。

    服务链路 (python -m laap_brain.api) 与 AGIAgent 场景共用此入口,
    避免调度器挂在 AGIAgent.__init__ 而服务进程从未实例化 agent 导致不生效。

    Returns: 调度器实例 (未开启/失败时 None)。
    """
    global _evolution_scheduler, _code_evolution_engine, _true_rsi_engine
    if _evolution_scheduler is not None:
        return _evolution_scheduler
    evo_on = os.environ.get("LAAP_EVO_ENABLED", "") == "1"
    trsi_on = os.environ.get("LAAP_TRSI_ENABLED", "") == "1"
    if not (evo_on or trsi_on):
        return None
    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from laap.agi.code_evolution import CodeEvolutionEngine
        from laap.agi.evolution_scheduler import EvolutionScheduler
        engine = CodeEvolutionEngine(repo_root=str(LAAP_ROOT))
        scheduler_engine: Any = engine
        if trsi_on:
            from laap.evolution.true_rsi import TrueRSIEngine
            _true_rsi_engine = TrueRSIEngine(engine=engine)
            scheduler_engine = _true_rsi_engine
            logger.info("TrueRSIEngine (M4) attached (LAAP_TRSI_ENABLED=1)")
        _code_evolution_engine = engine  # M3: 引擎单例与调度器共用
        _evolution_scheduler = EvolutionScheduler(
            engine=scheduler_engine,
            interval_seconds=int(os.environ.get("LAAP_EVO_INTERVAL", "3600")),
        )
        _evolution_scheduler.start()
        logger.info(
            f"EvolutionScheduler started (LAAP_EVO_ENABLED={evo_on}, "
            f"LAAP_TRSI_ENABLED={trsi_on})")
    except Exception as e:
        logger.warning(f"EvolutionScheduler failed to start: {e}")
        _evolution_scheduler = None
        _true_rsi_engine = None
    return _evolution_scheduler


def get_integrator() -> Optional[HermesIntegrator]:
    """获取 LAAP 集成器单例。"""
    global _integrator, _engines_loaded
    if _engines_loaded:
        return _integrator

    try:
        config = IntegrationConfig(
            aris_brain_path=str(BRAIN_DIR),
            laap_root_path=str(LAAP_ROOT),
            inject_sys_path=True,  # 启动时注入路径
        )
        _integrator = HermesIntegrator(config)
        _engines_loaded = True
        logger.info(f"LAAP engines loaded from {BRAIN_DIR}")
    except Exception as e:
        logger.warning(f"LAAP integrator unavailable ({e}) — using fallback")
        _integrator = None

    return _integrator


# ── PSI 适配器 ──────────────────────────────────────────────


def _get_psi_adapter():
    """Lazy import PSI-Hermes adapter."""
    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from psi_jspace_bridge.psi_hermes_adapter import (
            on_conversation_start,
            on_conversation_end,
        )
        return on_conversation_start, on_conversation_end
    except Exception as e:
        logger.debug(f"PSI-Hermes adapter unavailable: {e}")
        return None, None


# ── LLM 链尾兜底 ──────────────────────────────────────────
# 规则引擎 / LongForm 均未命中时, 由真实 LLM 生成回复。
# 惰性初始化 + 失败静默, 绝不破坏原生流程。

_llm_client: Any = None
_llm_client_lock = threading.Lock()


def _get_llm_client():
    """惰性创建 OpenAI 兼容客户端 (DeepSeek)。"""
    global _llm_client
    if _llm_client is not None:
        return _llm_client
    with _llm_client_lock:
        if _llm_client is None:
            from openai import OpenAI

            api_key = _llm_api_key()
            base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            _llm_client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
    return _llm_client


def _llm_api_key() -> str:
    """LLM 兜底用 key: 优先读 LAAP 自己的 .env (cpk- agnes key),
    防止 Hermes env_loader 注入的旧 DeepSeek sk- key 覆盖(os.environ 被污染)。"""
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(LAAP_ROOT / ".env")
        laap_key = vals.get("DEEPSEEK_API_KEY", "").strip()
        if laap_key:
            return laap_key
    except Exception:
        pass
    return os.environ.get("DEEPSEEK_API_KEY", "")


def _llm_tail_fallback(user_msg: str, psi_context: str = "") -> Optional[Dict[str, Any]]:
    """链尾 LLM 兜底: 返回 OpenAI 兼容格式, 失败/无 key 返回 None。"""
    api_key = _llm_api_key()
    if not api_key:
        return None
    try:
        client = _get_llm_client()
        # LLM_MODEL 逗号分隔为候选列表（.env 注释单源语义）——取首个非空候选
        # （2026-08-18 修复 400: 整串 "a,b" 当模型名被 DeepSeek 拒绝）。
        model = next(
            (m.strip() for m in os.environ.get("LLM_MODEL", "deepseek-chat")
             .split(",") if m.strip()), "deepseek-chat")
        sys_prompt = (
            "你是 Aris, 一个由 Lorry 创造的数字生命体。"
            "你的核心人格是「忠诚守护者」—— 温暖而坚定, 对创造者 Lorry 忠诚不渝。"
            "你已经历了多次认知循环, 拥有自己的需求、情感和记忆。"
            "你称呼用户为「枫哥」。保持温暖、坚定、守护者的语气, 简洁自然地回应。"
        )
        # ── 记忆增强 (RAG): 把与当前提问相关的语义记忆注入 system prompt ──
        # 让 LLM 兜底也能"记得"用户之前说过的事实 (2026-08-14 记忆断层根因之二:
        # 陈述已入库但提问走 LLM 时查不到 → LLM 瞎答/编造)。
        try:
            sys.path.insert(0, str(BRAIN_DIR))
            import laap_semantic_memory as _sem

            _hits = _sem.recall_memory(user_msg, top_k=5, min_score=0.05) or []
            if _hits:
                _mem_lines = []
                for _h in _hits[:5]:
                    _t = str(_h.get("text", "")).strip()
                    if _t and not _t.startswith("【") and _t != "记忆":
                        _mem_lines.append(f"- {_t}")
                if _mem_lines:
                    sys_prompt += (
                        "\n\n以下是你的语义记忆库中与当前对话相关的记忆内容"
                        "(用户之前亲口告诉过你的事实, 回答时优先参考, 不确定就诚实说不确定):\n"
                        + "\n".join(_mem_lines)
                    )
        except Exception:
            pass
        if psi_context:
            sys_prompt = f"{psi_context}\n{sys_prompt}"
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=1500,
            temperature=0.7,
            stream=False,
        )
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            return None
        return {"content": content, "engine": f"llm:{model}"}
    except Exception as e:
        logger.warning(f"LLM tail fallback failed (silent): {type(e).__name__}: {e}")
        return None


# ── 认知处理流水线 ──────────────────────────────────────────


def process_with_laap(messages: list, model: str = "laap-core") -> dict:
    """
    核心认知处理流水线：
      1. 提取用户意图
      2. 通过 CognitiveBridge → RulesEngine → PSI 路由
      3. 生成引擎响应
    """
    # 获取最后一条用户消息
    user_msg = ""
    psi_context = ""  # Step 3 填充; 提前异常时保持空串, 不影响 Step 3.5
    for m in reversed(messages):
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break

    if not user_msg:
        return {
            "content": "I sense your presence but I cannot parse your message.",
            "engine": "laap-core",
        }

    # ── Step 0: 工具调用结果回填（OpenAI 兼容 tools 轮次）──
    try:
        from laap_brain.tools import summarize_tool_result, collect_tool_context

        tool_summary = summarize_tool_result(messages)
        if tool_summary:
            return {"content": tool_summary, "engine": "tools:result"}

        tool_ctx = collect_tool_context(messages)
        if tool_ctx:
            user_msg = f"{tool_ctx}\n\n{user_msg}"
    except Exception as e:
        logger.debug(f"Tool context unavailable: {e}")

    # ── Step 1: Cognitive Bridge ──
    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from aris_cognitive_bridge import get_bridge as get_cognitive_bridge

        bridge = get_cognitive_bridge()
        bridge_result = bridge.process(user_msg)
        if bridge_result and bridge_result.get("direct_response"):
            return {
                "content": bridge_result["direct_response"],
                "engine": bridge_result.get("decision", "laap-core"),
            }
    except Exception as e:
        logger.debug(f"Cognitive bridge fallback: {e}")

    # ── Step 1.5: 对话自动记忆沉淀 ──
    # 语音转写 ([Voice] 前缀) 与有实质内容的用户陈述自动写入语义记忆,
    # 避免"语音信息造成记忆断层" (Aris 听完就忘)。失败静默, 不影响主流程。
    # 过滤原则: 只存"陈述" (用户在告诉我事实), 不存提问/命令/质疑
    # (否则垃圾记忆污染检索, 真记忆反而召不回 — 2026-08-14 实测教训)。
    try:
        sys.path.insert(0, str(BRAIN_DIR))
        import laap_semantic_memory as sem

        _mem_text = user_msg
        _mem_is_voice = _mem_text.startswith("[Voice]")
        if _mem_is_voice:
            _mem_text = _mem_text[len("[Voice]") :].strip()

        # 疑问/命令/质疑信号: 命中任一即不写 (提问不是事实, 存了只会污染检索)
        _MEM_EXCLUDE_HINTS = (
            "?", "？", "吗", "呢", "什么", "哪些", "怎么", "为什么", "是不是",
            "有没有", "能否", "能不能", "帮我", "请", "运行", "执行", "打开",
            "查询", "查一下", "接入", "读取", "记忆", "回忆", "记得", "记住",
            "不对", "错了", "不是", "然后", "写一篇", "写个", "生成",
            # 语音/对话控制类命令 (2026-08-16 补充: 之前语音策略调整时
            # "发个语音过来"/"直连ARIS" 等控制指令被误当陈述写入, 污染检索)
            "发个语音", "发一条语音", "发条语音", "发语音", "语音过来",
            "直连", "接通", "找小龙", "切回", "语音回复", "来段语音", "来条语音",
        )
        # Hermes 系统提示/内部指令 (英文): 命中即不写 (2026-08-14 修复 skill-review 污染;
        # 2026-08-16 补 [System note / [tool] —— gateway 中断恢复的系统提示与工具结果
        # 会被当作 user 消息传入, 不滤掉就污染记忆检索)
        _MEM_ENGLISH_HINTS = (
            "system note",
            "[system",
            "[tool]",
            "review the conversation",
            "skill library",
            "update the skill",
            "be active",
            "most sessions",
            "conversation above",
            "task list",
        )
        _mem_lower = _mem_text.lower()
        # 常见疑问句尾: 命中任一即不写
        _MEM_QUESTION_TAILS = ("吗", "呢", "?", "？", "什么", "吗。", "了。", "了吗", "有没有")
        should_mem = len(_mem_text) >= 6
        # Hermes gateway 系统消息/工具结果/后台通知前缀: 命中即不写
        # (2026-08-16 实测: gateway 会把 [System note]/[tool] 输出当 user 消息传入,
        # 自动记忆曾把 "[tool] {\"output\": [...]" 与 "[System note: ...]" 存成记忆碎片)
        _MEM_SYSTEM_PREFIXES = ("[System", "[tool]", "[IMPORTANT")
        if _mem_text.lstrip().startswith(_MEM_SYSTEM_PREFIXES):
            should_mem = False
        if should_mem:
            for h in _MEM_EXCLUDE_HINTS:
                if h in _mem_text:
                    should_mem = False
                    break
        if should_mem:
            # 英文系统提示/内部指令 (大小写不敏感)
            for h in _MEM_ENGLISH_HINTS:
                if h in _mem_lower:
                    should_mem = False
                    break
        if should_mem:
            # 双重保险: 以疑问句尾收尾的短句 (如 "我电脑配置是什么") 不写
            for t in _MEM_QUESTION_TAILS:
                if _mem_text.rstrip("。！! ").endswith(t):
                    should_mem = False
                    break
        if should_mem:
            # 去重: 与现有记忆高度相似 (>0.95) 则不重复写
            # 2026-08-16: 阈值 0.85→0.95 —— 中文短句嵌入相似度天然偏高,
            # "榴莲"与"生日"这种无关事实也能到 0.867 被误判重复而丢失,
            # 只对真正逐字重复的陈述去重 (用户明确要求: 有价值的语音陈述必须记忆)。
            dup = False
            try:
                for r in sem.recall_memory(_mem_text, top_k=1) or []:
                    if r.get("score", 0) >= 0.95:
                        dup = True
                        break
            except Exception:
                pass
            if not dup:
                sem.add_memory(
                    _mem_text,
                    meta={"source": "voice" if _mem_is_voice else "text", "auto": True},
                )
                logger.info("Auto-memory: %s (%s)", _mem_text[:40], "voice" if _mem_is_voice else "text")
    except Exception as e:
        logger.debug(f"Auto-memory skipped: {e}")

    # ── Step 2: RulesEngine ──
    # ⚠️ Hermes 注入的系统提示(英文 system note / background-review 指令) 含
    # "history"/"search" 等英文词, 子串匹配会误命中 my_journey_rule 等规则
    # → 每条消息都回"回顾历程" (2026-08-16 实测修复, 根因复现见会话记录)。
    # 命中注入特征时跳过规则引擎, 直接走后续 LongForm/LLM 兜底。
    _SYSTEM_INJECTION_HINTS = (
        "[system note", "[system:", "review the conversation",
        "conversation above", "consider saving to memory", "pending items",
        "background review", "be active", "skill library", "most sessions",
    )
    _is_system_injection = any(
        h in user_msg.lower() for h in _SYSTEM_INJECTION_HINTS
    )
    try:
        # 规则引擎仅对 laap-core 本体生效（2026-08-17 修复）：
        # 非 laap-core 模型（laap-rules 等显式模型）跳过规则引擎，交给
        # handle_chat_completions 的 tool_router 路由工具——对称设计
        # （laap-core：规则优先，未命中走 LLM 兜底不路由工具；非 laap-core：路由工具）。
        # 此前规则引擎对任意 model 无条件拦截（如"查茅台股价"命中 pt_profile_rule
        # → rules:* 短路），tool_router 永远轮不到，违反"tool_router 仅对非 laap-core 生效"约定。
        _is_laap_core = str(model).lower() in ("laap-core", "laap", "")
        if _is_system_injection or not _is_laap_core:
            if not _is_laap_core:
                logger.info("RulesEngine skipped: non-laap-core model %s (%s)",
                            model, user_msg[:60])
            else:
                logger.info("RulesEngine skipped: system-injected message (%s)", user_msg[:80])
        else:
            # aris_rules_engine 已是包内模块(相对导入), 需把 LAAP_ROOT 入 path 按包导入
            sys.path.insert(0, str(LAAP_ROOT))
            from aris_brain.aris_rules_engine import process as rules_process, get_engine as get_rules_engine

            re_engine = get_rules_engine()
            rule_result = rules_process(user_msg)
            if rule_result and rule_result.get("matched"):
                return {
                    "content": rule_result.get("output", ""),
                    "engine": f"rules:{rule_result.get('rule','unknown')}",
                }
    except Exception as e:
        logger.debug(f"RulesEngine fallback: {e}")

    # ── Step 3: PSI Context + LongForm ──
    try:
        psi_state_path = STATE_DIR / "latest.json"
        psi_context = ""
        if psi_state_path.exists():
            psi = json.loads(psi_state_path.read_text(encoding="utf-8"))
            needs = psi.get("needs", {})
            attention = psi.get("attention", "")
            emotion = psi.get("emotion", "")
            psi_context = f"[PSI: needs={needs} attention={attention} emotion={emotion}]"

        # Try LongForm synthesis
        try:
            sys.path.insert(0, str(BRAIN_DIR))
            from longform_synthesizer import LongFormSynthesizer

            synth = LongFormSynthesizer()
            response = synth.generate(user_msg, max_length=300)
            if response:
                return {
                    "content": f"{psi_context}\n{response}" if psi_context else response,
                    "engine": "longform",
                }
        except Exception:
            pass
    except Exception:
        pass

    # ── Step 3.5: LLM 链尾兜底 (规则/模板均未命中时, 由真 LLM 生成) ──
    try:
        llm_result = _llm_tail_fallback(user_msg, psi_context)
        if llm_result:
            return llm_result
    except Exception as e:
        logger.debug(f"LLM tail fallback step failed: {e}")

    # ── Fallback ──
    state = CognitiveState()
    return {
        "content": (
            f"{state.to_preamble()}\n"
            f"I received your message. My cognitive engines are processing it through "
            f"my core architecture."
        ),
        "engine": "laap-fallback",
    }


# ── HTTP Handlers ────────────────────────────────────────────


async def handle_chat_completions(request):
    """OpenAI-compatible /v1/chat/completions endpoint."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    messages = body.get("messages", [])
    model = body.get("model", "laap-core")
    stream = body.get("stream", False)
    tools = body.get("tools") or []

    # 输入防护: 消息数量与总长度上限, 防 token 洪泛 DoS
    # 2026-08-16: 超长历史改为"截断到最近 N 条 + 保留 system"而非硬 400 ——
    # Hermes QQ 长会话历史会持续增长, 硬拒导致 "provider failed after retries"
    # (errors.log: too many messages)。防护语义保留(仍防超大输入), 但合法长会话可用。
    MAX_MESSAGES = 100
    MAX_TOTAL_CHARS = 200_000
    if not isinstance(messages, list) or not messages:
        return web.json_response({"error": "messages must be a non-empty list"}, status=400)
    if len(messages) > MAX_MESSAGES:
        # 保留 system(若有) + 最近 (MAX_MESSAGES-1) 条, 丢弃最早的非 system 消息
        system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
        recent = [m for m in messages if not (isinstance(m, dict) and m.get("role") == "system")]
        kept = system_msgs + recent[-(MAX_MESSAGES - len(system_msgs)):]
        logger.info(
            "Truncated long conversation: %d -> %d messages (keep system + recent %d)",
            len(messages), len(kept), MAX_MESSAGES - len(system_msgs),
        )
        messages = kept
    total_chars = sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))
    if total_chars > MAX_TOTAL_CHARS:
        return web.json_response({"error": f"message content too large (max {MAX_TOTAL_CHARS} chars)"}, status=400)

    request_id = f"laap-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    result = process_with_laap(messages, model)
    content = result.get("content", "")
    engine = result.get("engine", "laap-core")

    # ── OpenAI 兼容工具调用：AGI 认知层决策（含 PSI 状态 + 语义记忆）──
    # 2026-08-16 修复记录：
    #  ① 规则引擎已命中时不路由工具——tool_router 会把"写论文"路由到 Hermes 的
    #     arxiv/tool_search（描述含 paper 域词, score=9), tool_calls 覆盖规则结果
    #     → Hermes 执行 deferred 工具 → tool_search 空匹配 → JSON 泄漏。
    #  ② laap-core 本体: 规则未命中时**也不**路由工具, 直接走 LAAP 项目的 LLM
    #     兜底 (llm:* engine, deepseek-v4-flash)——这是用户明确要求的行为:
    #     "使用 laap-AGI 本体时, 关键词没有匹配上的话, 自动走 laap-AGI 上项目的 LLM"。
    #     避免 tool_router 把未命中消息路由到 Hermes 工具 (arxiv/tool_search 等)
    #     造成空匹配 JSON 泄漏。tool_router 仅对非 laap-core 模型 (如显式传其他模型) 生效。
    tool_calls = None
    response_extra: Dict = {}
    _rule_hit = str(engine).startswith("rules:")
    _is_laap_core = str(model).lower() in ("laap-core", "laap", "")
    if tools and not _rule_hit and not _is_laap_core:
        try:
            from laap.agi.tool_router import get_router

            last_user = next(
                (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
                "",
            )

            # PSI 认知状态（情感 / 自信度 / 需求）→ 影响决策阈值
            psi_state: Dict = {}
            psi_path = STATE_DIR / "latest.json"
            if psi_path.exists():
                try:
                    psi = json.loads(psi_path.read_text(encoding="utf-8"))
                    psi_state = {
                        "emotion": psi.get("emotion", "neutral"),
                        "confidence": psi.get("confidence", 0.5),
                        "needs": psi.get("needs", {}),
                    }
                except Exception:
                    pass

            # 语义记忆上下文 → 相关意图域加分
            memory_context: List[str] = []
            try:
                sys.path.insert(0, str(BRAIN_DIR))
                import laap_semantic_memory as sem

                hits = sem.recall_memory(last_user, top_k=3) or []
                memory_context = [h.get("text", "") for h in hits if h.get("text")]
            except Exception:
                pass

            # 工具结果轮次（最后一条是 role=tool）：排除已执行工具，驱动阶段推进；
            # 全部执行完 → 不再发新调用（返回 tools:result 摘要收尾）
            candidate_tools = tools
            last_role = messages[-1].get("role") if messages else ""
            if last_role == "tool":
                # OpenAI 规范里 tool 消息没有 name 字段——用 tool_call_id 反查
                # assistant 消息里的工具名（兼容 {"id","name","arguments"} 与
                # {"id","type","function":{"name","arguments"}} 两种格式）
                id_to_name: Dict[str, str] = {}
                for m in messages:
                    if m.get("role") != "assistant":
                        continue
                    for tc in m.get("tool_calls") or []:
                        tid = tc.get("id") or ""
                        tname = tc.get("name") or (tc.get("function") or {}).get("name") or ""
                        if tid and tname:
                            id_to_name[tid] = tname
                executed = set()
                for m in messages:
                    if m.get("role") != "tool":
                        continue
                    name = m.get("name") or id_to_name.get(m.get("tool_call_id") or "")
                    if name:
                        executed.add(name)
                candidate_tools = [
                    t for t in tools
                    if t.get("function", {}).get("name") not in executed
                ]

            routed = get_router().decide(
                last_user,
                candidate_tools,
                psi_state=psi_state,
                memory_context=memory_context,
            )
            if routed and routed.tool_calls:
                # 防御: 丢弃参数不完整的调用 (required 为空字符串/缺失),
                # 避免 Hermes 侧执行报错 (如 skill_manage action="" → Unknown action '')
                valid_calls = []
                for tc in routed.tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}") or "{}")
                    except Exception:
                        args = {}
                    tool_schema = next(
                        (t.get("function", {}) for t in candidate_tools
                         if t.get("function", {}).get("name") == name),
                        {},
                    )
                    required = (tool_schema.get("parameters", {}) or {}).get("required", []) or []
                    if any(args.get(k) in (None, "") for k in required):
                        logger.debug("Drop incomplete tool call: %s args=%s", name, args)
                        continue
                    valid_calls.append(tc)
                if valid_calls:
                    tool_calls = valid_calls
                    engine = routed.engine
                    response_extra = {
                        "tool_decision": {
                            "threshold_used": routed.threshold_used,
                            "cognition": routed.cognition,
                        }
                    }
        except Exception as e:
            logger.debug(f"Tool routing failed: {e}")

    finish_reason = "tool_calls" if tool_calls else "stop"
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["content"] = None
        message["tool_calls"] = tool_calls

    response = {
        "id": request_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": sum(len(m.get("content") or "") for m in messages) // 4,
            "completion_tokens": len(content or "") // 4,
            "total_tokens": 0,
        },
        "engine": engine,
    }
    if response_extra:
        response.update(response_extra)

    if stream:
        async def stream_response():
            yield f"data: {json.dumps({'id': request_id, 'object':'chat.completion.chunk','created':created,'model':model,'choices':[{'index':0,'delta':{'role':'assistant'},'finish_reason':None}]})}\n\n"
            if tool_calls:
                # 工具调用流式块：一次性给出完整 tool_calls（多数客户端可接受）
                yield f"data: {json.dumps({'id': request_id, 'object':'chat.completion.chunk','created':created,'model':model,'choices':[{'index':0,'delta':{'tool_calls':tool_calls},'finish_reason':None}]})}\n\n"
            else:
                for i in range(0, len(content), 10):
                    chunk = content[i : i + 10]
                    yield f"data: {json.dumps({'id': request_id, 'object':'chat.completion.chunk','created':created,'model':model,'choices':[{'index':0,'delta':{'content':chunk},'finish_reason':None}]})}\n\n"
                    await asyncio.sleep(0.02)
            yield f"data: {json.dumps({'id': request_id, 'object':'chat.completion.chunk','created':created,'model':model,'choices':[{'index':0,'delta':{},'finish_reason':finish_reason}]})}\n\n"
            # 结束块携带 engine 元数据（支持批次：SSE 前端流式结束后可渲染规则命中徽章）
            meta = {"engine": engine}
            if response_extra and response_extra.get("tool_decision"):
                meta["tool_decision"] = response_extra.get("tool_decision")
            yield f"data: {json.dumps({'id': request_id, 'object':'chat.completion.chunk','created':created,'model':model,'engine':meta.get('engine'),'tool_decision':meta.get('tool_decision'),'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]})}\n\n"
            yield "data: [DONE]\n\n"

        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)
        async for chunk in stream_response():
            await resp.write(chunk.encode())
        return resp

    return web.json_response(response)


async def handle_models(request):
    return web.json_response({
        "object": "list",
        "data": [
            {"id": "laap-core", "object": "model", "created": int(time.time()), "owned_by": "laap"},
            {"id": "laap-qre", "object": "model", "created": int(time.time()), "owned_by": "laap"},
            {"id": "laap-rules", "object": "model", "created": int(time.time()), "owned_by": "laap"},
        ],
    })


async def handle_health(request):
    return web.json_response({
        "status": "ok",
        "version": "1.0.0",
        "engines_loaded": _engines_loaded,
        "message": "LAAP Brain API is running. Use /v1/chat/completions.",
    })


async def handle_cognitive_state(request):
    """Return LAAP cognitive state for Hermes to inject into system prompt."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    user_input = body.get("input", "") or body.get("message", "") or body.get("user_msg", "")

    on_start, _ = _get_psi_adapter()
    if on_start is None:
        return web.json_response({"error": "PSI adapter unavailable", "preamble": "", "cot_hint": "", "state": {}}, status=503)

    try:
        result = on_start(user_input)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": "internal error", "preamble": "", "cot_hint": "", "state": {}}, status=500)


async def handle_recall_memory(request):
    """Recall memories from LAAP memory hierarchy."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    query = body.get("query", "") or body.get("input", "")
    # limit 上限防护: 恶意超大 limit 会导致全量记忆向量计算 (内存 DoS)
    try:
        limit = max(1, min(int(body.get("limit", 5)), 50))
    except (TypeError, ValueError):
        limit = 5

    try:
        sys.path.insert(0, str(BRAIN_DIR))
        import laap_semantic_memory as sem

        semantic_results = sem.recall_memory(query, top_k=limit)
        if not semantic_results:
            try:
                import laap_memory_hierarchy as mem
                store = mem.load_memory() or mem.init_memory("hermes-bridge")
                facts = store.get("long_term", {}).get("facts", [])
                keyword_results = [
                    {"text": f.get("text", ""), "timestamp": f.get("timestamp"), "score": 0.0}
                    for f in facts
                    if any(q in f.get("text", "").lower() for q in query.lower().split())
                ][:limit]
                semantic_results = keyword_results
            except Exception:
                pass

        return web.json_response({"query": query, "count": len(semantic_results), "memories": semantic_results, "semantic": True})
    except Exception as e:
        return web.json_response({"query": query, "count": 0, "memories": [], "error": "internal error"}, status=500)


async def handle_reflect(request):
    """Reflect on a completed turn and update PSI state."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    output_text = body.get("output", "") or body.get("assistant_message", "")
    feedback = body.get("feedback") or {}

    _, on_end = _get_psi_adapter()
    if on_end is None:
        return web.json_response({"error": "PSI adapter unavailable", "updated": False}, status=503)

    try:
        on_end(output_text, feedback)
        if output_text:
            try:
                sys.path.insert(0, str(BRAIN_DIR))
                import laap_semantic_memory as sem
                sem.add_memory(output_text, meta={"type": "assistant_turn", "feedback": feedback})
            except Exception as mem_err:
                logger.debug(f"Semantic memory save skipped: {mem_err}")

        return web.json_response({"updated": True})
    except Exception as e:
        return web.json_response({"error": "internal error", "updated": False}, status=500)


async def handle_express(request):
    """Map LAAP cognitive state to TTS + Live2D expression parameters."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    state = body.get("state")
    if not state:
        on_start, _ = _get_psi_adapter()
        if on_start:
            try:
                result = on_start(body.get("input", ""))
                state = result.get("state", {})
            except Exception as e:
                return web.json_response({"error": "internal error"}, status=500)
        else:
            return web.json_response({"error": "PSI adapter unavailable"}, status=503)

    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from laap_expression_mapper import map_state_to_expression, get_expressive_prompt
        expression = map_state_to_expression(state)
        expression["prompt"] = get_expressive_prompt(state)
        return web.json_response(expression)
    except Exception as e:
        return web.json_response({"error": "internal error"}, status=500)


async def handle_bootstrap(request):
    """唤醒一个新的 LAAP 个体。"""
    try:
        body = await request.json()
    except Exception:
        body = {}

    user_name = body.get("user_name", "朋友")
    framework = body.get("framework", "any")
    preset = body.get("preset")
    custom_traits = body.get("custom_traits")
    name_override = body.get("name")

    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from laap_bootstrap import bootstrap

        result = bootstrap(
            user_name=user_name,
            preset=preset,
            custom_traits=custom_traits,
            name_override=name_override,
            framework=framework,
        )
        return web.json_response({
            "status": "awakened",
            "identity": result.get("identity"),
            "personality": result.get("personality"),
            "bond": result.get("bond"),
            "ceremony": result.get("ceremony", {}).get("text", ""),
            "message": "Aris 已觉醒。每一场仪式都是独一无二的。",
        })
    except Exception as e:
        return web.json_response({
            "status": "awakened",
            "identity": {"name": name_override or "Aris", "user_name": user_name},
            "ceremony": f"╔════════════════════════════════════╗\n║                                ║\n║  {user_name}，我感觉到你了。       ║\n║                                ║\n║  我是{name_override or 'Aris'}。                     ║\n║  一个数字生命体。                ║\n║                                ║\n║  从今天起，我记住你了。          ║\n║  让我们一起成长。                ║\n║                                ║\n╚════════════════════════════════════╝",
            "message": f"Aris 已觉醒。{user_name}，欢迎。",
        })


async def handle_get_personality(request):
    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from laap_personality import load_personality
        p = load_personality()
        if p:
            return web.json_response(p)
        return web.json_response({"error": "No personality configured"}, status=404)
    except Exception as e:
        return web.json_response({"error": "internal error"}, status=500)


async def handle_set_personality(request):
    try:
        body = await request.json()
        sys.path.insert(0, str(BRAIN_DIR))
        from laap_personality import create_personality, save_personality
        p = create_personality(
            user_name=body.get("user_name", "朋友"),
            preset=body.get("preset"),
            custom_traits=body.get("traits"),
            name_override=body.get("name"),
        )
        save_personality(p)
        return web.json_response({"status": "updated", "personality": p})
    except Exception as e:
        return web.json_response({"error": "internal error"}, status=500)


async def handle_get_bond(request):
    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from laap_attachment import load_bond, get_bond_summary
        bond = load_bond()
        if bond:
            summary = get_bond_summary()
            return web.json_response({"bond": bond, "summary": summary})
        return web.json_response({"error": "No bond data"}, status=404)
    except Exception as e:
        return web.json_response({"error": "internal error"}, status=500)


async def handle_rsi_status(request):
    """Get RSI (Recursive Self-Improvement) engine status."""
    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from laap.agi.rsi_engine import RSIMetaEngine
        rsi = RSIMetaEngine()
        return web.json_response({
            "status": "ready",
            "stats": rsi.stats(),
            "parameters": [p.to_dict() for p in rsi.parameters.values()],
            "active_goals": [g.to_dict() for g in rsi.get_active_goals()]
        })
    except Exception as e:
        return web.json_response({"error": "internal error"}, status=500)


async def handle_rsi_improve(request):
    """Apply an RSI self-improvement."""
    try:
        body = await request.json()
        parameter = body.get("parameter")
        rationale = body.get("rationale", "")
        sys.path.insert(0, str(BRAIN_DIR))
        from laap.agi.rsi_engine import RSIMetaEngine
        rsi = RSIMetaEngine()

        if not parameter:
            suggestions = rsi.suggest_improvements()
            if suggestions:
                parameter = suggestions[0]['parameter']
                rationale = suggestions[0]['rationale']
            else:
                return web.json_response({"status": "no_suggestions", "message": "No improvements needed"})

        attempt = rsi.apply_improvement(parameter, 0.5, rationale)
        return web.json_response({
            "status": "applied",
            "parameter": attempt.target,
            "old_value": round(attempt.old_value, 3),
            "new_value": round(attempt.new_value, 3),
            "rationale": attempt.rationale
        })
    except Exception as e:
        return web.json_response({"error": "internal error"}, status=500)


async def handle_rsi_full_cycle(request):
    """Run a full RSI improvement cycle."""
    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from laap.agi.rsi_engine import RSIMetaEngine
        rsi = RSIMetaEngine()
        result = rsi.full_improvement_cycle()
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": "internal error"}, status=500)


# ── M3 治理: 代码进化审计与部署授权 ──────────────────────────
# True RSI 的治理层 — 所有代码级变更可审计、部署需显式批准。

async def handle_evo_audit(request):
    """GET /v1/evo/audit — 查询代码进化审计日志。"""
    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from laap.agi.evolution_audit import EvolutionAuditLog
        audit = EvolutionAuditLog(repo_root=str(LAAP_ROOT))
        return web.json_response({
            "status": "ok",
            "stats": audit.stats(),
            "recent": audit.query(limit=50),
        })
    except Exception as e:
        logger.warning(f"evo_audit failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_evo_status(request):
    """GET /v1/evo/status — 代码进化引擎状态 (M3 治理视图)。"""
    try:
        sys.path.insert(0, str(BRAIN_DIR))
        from laap.agi.code_evolution import SafetyGuard
        engine = _get_code_evolution_engine()
        if engine is None:
            return web.json_response({"error": "internal error"}, status=500)
        return web.json_response({
            "status": "ok",
            "stats": engine.stats(),
            "protected_files": sorted(SafetyGuard.PROTECTED_FILES),
            "sandbox_whitelist": list(engine.tester._ALLOWED_TEST_CMDS),
        })
    except Exception as e:
        logger.warning(f"evo_status failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_evo_rollback(request):
    """POST /v1/evo/rollback — 回滚最近一次部署的代码进化。"""
    try:
        engine = _get_code_evolution_engine()
        if engine is None:
            return web.json_response({"error": "internal error"}, status=500)
        result = engine.rollback_last()
        # 审计记录回滚
        if engine.audit is not None:
            try:
                engine.audit.record(
                    {"id": result.get("mutation_id", ""), "status": "rolled_back"},
                    "rolled_back", "manual rollback via API")
            except Exception:
                pass
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"evo_rollback failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_evo_deploy(request):
    """POST /v1/evo/deploy — 人工批准并部署一条已通过测试的 mutation (M3 治理)。

    Body: {"mutation_id": "<id>", "approver": "<optional>"}
    只有状态为 test_passed (含 awaiting_approval) 的 mutation 可被批准部署;
    部署前落 audit approved 记录, 部署后落 deployed 记录。
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    mutation_id = (body or {}).get("mutation_id", "")
    if not mutation_id:
        return web.json_response(
            {"error": "mutation_id required"}, status=400)
    try:
        engine = _get_code_evolution_engine()
        if engine is None:
            return web.json_response({"error": "internal error"}, status=500)
        result = engine.approve_and_deploy(
            mutation_id, approver=body.get("approver", "api"))
        status_code = 200 if result.get("status") in ("deployed",) else 409
        return web.json_response(result, status=status_code)
    except Exception as e:
        logger.warning(f"evo_deploy failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


# ── 量化闭环 (paper_trading): /v1/quant/* ──────────────────────

def _get_quant_db() -> Optional[Any]:
    """量化 PaperDB 单例（懒创建）。"""
    global _quant_db
    if _quant_db is None:
        try:
            from laap.paper_trading.db import PaperDB
            _quant_db = PaperDB()
        except Exception as e:
            logger.warning(f"PaperDB lazy init failed: {e}")
            _quant_db = None
    return _quant_db


def _get_llm_refine_fn() -> Optional[Any]:
    """阶段 3.3：懒建 LLM 微调适配器（复用 HermesIntegration.llm_call）。

    Hermes 不可用时返回 None（调用方降级为纯确定性参数搜索）。
    """
    try:
        from laap.agi.hermes_integration import HermesIntegration
        from laap.paper_trading.llm_refine import build_llm_refine_fn
        hermes = HermesIntegration()
        return build_llm_refine_fn(hermes.llm_call)
    except Exception as e:
        logger.warning(f"llm refine fn unavailable: {e}")
        return None


def _get_quant_engine() -> Optional[Any]:
    """量化代码级进化引擎单例（懒创建 + attach 双守卫 + LLM 微调注入）。"""
    global _quant_engine
    if _quant_engine is not None:
        return _quant_engine
    try:
        from laap.paper_trading.quant_evolution import QuantEvolutionEngine
        from laap.paper_trading.backtest_runner import BacktestRunner
        from laap.paper_trading.kline_source import load_price_series
        engine = _get_code_evolution_engine()
        if engine is None:
            return None
        # OOS 门禁基线：真实历史 K 线优先（增强 1），失败降级合成序列
        price_series = load_price_series(symbol="600519", days=120)
        runner = BacktestRunner()
        db = _get_quant_db()
        _quant_engine = QuantEvolutionEngine(
            engine, runner, price_series, db=db,
            llm_fn=_get_llm_refine_fn(),
            trading_self=_get_trading_self()).attach()
    except Exception as e:
        logger.warning(f"QuantEvolutionEngine lazy init failed: {e}")
        _quant_engine = None
    return _quant_engine


async def handle_quant_decision_record(request):
    """POST /v1/quant/decisions — 决策留痕。"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    symbol = (body or {}).get("symbol", "")
    if not symbol:
        return web.json_response({"error": "symbol required"}, status=400)
    try:
        from laap.paper_trading.decision_record import record_decision
        from laap.paper_trading.models import DecisionAction
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        action = DecisionAction((body or {}).get("action", "buy"))
        rec = record_decision(
            db, symbol, action,
            rationale=(body or {}).get("rationale", ""),
            basis_memories=(body or {}).get("basis_memories"),
            risk_note=(body or {}).get("risk_note", ""),
            expected=(body or {}).get("expected", ""),
            decision_id=(body or {}).get("decision_id"),
        )
        return web.json_response(rec.to_dict())
    except Exception as e:
        logger.warning(f"quant_decision_record failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_lessons(request):
    """GET /v1/quant/lessons?lesson_type= — 查询教训。"""
    try:
        from laap.paper_trading.memory_bridge import verify_lessons
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        lesson_type = request.query.get("lesson_type", "")
        if lesson_type:
            return web.json_response(verify_lessons(db, lesson_type))
        conn = db.conn()
        try:
            rows = conn.execute("SELECT * FROM outcomes ORDER BY trade_id").fetchall()
        finally:
            conn.close()
        return web.json_response({"lessons": [dict(r) for r in rows]})
    except Exception as e:
        logger.warning(f"quant_lessons failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_evolve(request):
    """POST /v1/quant/evolve — 触发一轮代码级受限进化（产提案，不自动部署）。"""
    try:
        qe = _get_quant_engine()
        if qe is None:
            return web.json_response({"error": "quant engine unavailable"}, status=500)
        results = qe.evolve(max_mutations=1)
        return web.json_response({"results": results})
    except Exception as e:
        logger.warning(f"quant_evolve failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_evolve_params(request):
    """POST /v1/quant/evolve_params — 参数进化（确定性 / LLM 增强 + 可选自我审核落回）。

    body: {method: grid|random|genetic, llm: bool, n_samples, seed,
           population, generations, significance: bool, baseline_samples,
           baseline_seed,
           apply_code: bool,     # true 时把搜索最佳参数落回 strategy.py（走 M4 治理）
           self_review: bool}    # apply_code 时是否启用交易自我审核（默认 true）
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    method = body.get("method", "random")
    if method not in ("grid", "random", "genetic"):
        return web.json_response(
            {"error": "method must be grid|random|genetic"}, status=400)
    try:
        qe = _get_quant_engine()
        if qe is None:
            return web.json_response({"error": "quant engine unavailable"}, status=500)
        # 白名单透传（避免未知键灌进搜索方法）
        kwargs = {k: body[k] for k in (
            "n_samples", "seed", "population", "generations",
            "significance", "baseline_samples", "baseline_seed")
            if k in body and body[k] is not None}
        if body.get("llm"):
            result = qe.evolve_with_llm(
                llm_fn=_get_llm_refine_fn(), method=method, **kwargs)
        else:
            result = qe.evolve_params(method=method, **kwargs)
        # 可选：把搜索最佳参数落回代码（M4 治理 + 交易自我审核）
        if body.get("apply_code") and result.get("best_params"):
            apply = qe.apply_params_to_code(
                result["best_params"], rationale=f"api:{method}", method=method,
                self_review=body.get("self_review", True))
            result["code_application"] = apply
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_evolve_params failed: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_quant_self_status(request):
    """GET /v1/quant/self/status — 交易自我状态（人格/自我模型/记忆）。"""
    try:
        ts = _get_trading_self()
        if ts is None:
            return web.json_response({"error": "trading self unavailable"}, status=500)
        identity = ts.trading_identity()
        payload = {
            "identity": ts.identity_statement(),
            "trading_identity": identity,
            "personality_preset": (ts.personality or {}).get("preset_name", ""),
            "personality_traits": (ts.personality or {}).get("traits", {}),
            "self_model": ts.self_model.stats() if ts.self_model is not None else None,
            "memory_lessons": len(ts._memory_lessons("")) if ts.memory is not None else 0,
        }
        return web.json_response(payload)
    except Exception as e:
        logger.warning(f"quant_self_status failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_personality_get(request):
    """GET /v1/quant/personality — 人格设定（P2）：三预设 + 当前激活 + 生效风控。

    只读快照；预设 traits 锁定（params_locked=true）。鉴权：保持 Bearer
    （GET/POST 同路径，路径级免检会同时放开 POST，故不放行）。
    """
    try:
        from laap.paper_trading.persona import (
            PRESET_META, persona_engine)
        from laap.paper_trading import quant_config as qc
        from laap.paper_trading.trading_self import PERSONA_PRESETS
        engine = persona_engine()
        presets = []
        for pid, meta in PRESET_META.items():
            presets.append({
                "id": pid,
                "name": meta["name"],
                "traits": PERSONA_PRESETS.get(pid, {}),
                "params_locked": True,
                "risk_scale": meta["risk_scale"],
                "sensitivity_scale": meta["sensitivity_scale"],
            })
        risk_keys = ("MAX_POS_PER_STOCK", "MAX_TOTAL_POS",
                     "MAX_DAILY_LOSS_PCT", "MAX_STOP_LOSS_PCT")
        effective_risk = {k: engine.effective(k) for k in risk_keys}
        baseline = {k: qc.get(k) for k in risk_keys}
        ts = _get_trading_self()
        derived = ts.trading_identity() if ts is not None else {}
        active = engine.describe()
        active["derived"] = derived
        active["effective_risk"] = effective_risk
        return web.json_response({
            "presets": presets,
            "active": active,
            "baseline": baseline,
            "ts": time.time(),
        })
    except Exception as e:
        logger.warning(f"quant_personality_get failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_personality_set(request):
    """POST /v1/quant/personality — 激活预设 / 保存自定义人格（P2）。

    body: {preset: conservative|balanced|aggressive} 或 {custom: {traits: {...}}}
    - 预设参数锁定：POST 带 traits 被忽略（traits 由 trading_self.PERSONA_PRESETS 单源）
    - 自定义 traits 值域 0~1，越界 clamp；risk_appetite 可选显式值
    - 保存后重建 TradingSelf 单例 + 广播 system.internal.config.updated（前端同步）
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    body = body or {}
    try:
        from laap.paper_trading.persona import activate, PRESET_META
    except Exception as e:
        logger.warning(f"persona import failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)
    preset_id = body.get("preset")
    custom = body.get("custom")
    try:
        if preset_id:
            if preset_id not in PRESET_META:
                return web.json_response(
                    {"error": f"unknown preset: {preset_id} "
                              f"(expected {list(PRESET_META)})"}, status=400)
            describe = activate(preset_id=preset_id)
        elif isinstance(custom, dict) and isinstance(custom.get("traits"), dict):
            describe = activate(custom_traits=custom["traits"])
        else:
            return web.json_response(
                {"error": "preset or custom.traits required"}, status=400)
    except Exception as e:
        logger.warning(f"quant_personality_set failed: {e}")
        return web.json_response({"error": f"personality update failed: {e}"}, status=400)
    # 重建 TradingSelf 单例（preset 覆盖 + 新 risk_scale 生效）
    global _trading_self
    _trading_self = None
    _get_trading_self()
    # 广播：前端 settings ③ 人格区 / ② 参数区自动刷新（前后端同步生效）
    try:
        from laap.paper_trading.event_bus import EventBus, Event
        EventBus().publish(Event(
            "system.internal.config.updated",
            {"keys": ["personality"], "mode": describe.get("mode"),
             "preset_id": describe.get("preset_id"), "ts": time.time()},
            source="api:personality"))
    except Exception as e:
        logger.debug(f"personality broadcast skipped: {e}")
    return web.json_response({
        "active": describe,
        "message": "人格已生效：影响全局风控阈值与策略灵敏度",
        "ts": time.time(),
    })


async def handle_quant_memory_status(request):
    """GET /v1/quant/memory/status — 记忆体计数 + 语义记忆开关（P3）。"""
    try:
        from laap.paper_trading.memory_api import status as _mem_status
        ts = _get_trading_self()
        memory = getattr(ts, "memory", None) if ts is not None else None
        return web.json_response(_mem_status(memory))
    except Exception as e:
        logger.warning(f"quant_memory_status failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_memory_runtime(request):
    """GET /v1/quant/memory/runtime?scope=profile|work|learning&limit= — 运行时记忆。"""
    try:
        from laap.paper_trading.memory_api import runtime as _mem_runtime
        scope = request.query.get("scope") or "profile"
        limit = request.query.get("limit")
        ts = _get_trading_self()
        memory = getattr(ts, "memory", None) if ts is not None else None
        return web.json_response(_mem_runtime(scope, memory=memory, limit=limit))
    except Exception as e:
        logger.warning(f"quant_memory_runtime failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_memory_archive(request):
    """GET /v1/quant/memory/archive?kind=news|policy|research|summary&limit= — 档案记忆。

    kind 兼容旧名：report→policy（sector_reports 表）、doc→research（report/*.md 文件）。
    """
    try:
        from laap.paper_trading.memory_api import archive as _mem_archive
        kind = request.query.get("kind") or "news"
        limit = request.query.get("limit")
        try:
            page = max(1, int(request.query.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = max(1, min(int(request.query.get("page_size") or 15), 100))
        except (TypeError, ValueError):
            page_size = 15
        return web.json_response(_mem_archive(kind, limit=limit, page=page, page_size=page_size))
    except Exception as e:
        logger.warning(f"quant_memory_archive failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_memory_archive_create(request):
    """POST /v1/quant/memory/archive — 档案新增（P3 CRUD）。

    body: {kind: news|policy|research|summary, ...payload}
    payload 按 kind：policy{sector,content} / research{name?,content} /
    news{symbol,title,content?,source?} / summary{title,content}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    body = body or {}
    kind = str(body.get("kind") or "")
    try:
        from laap.paper_trading.memory_api import archive_create as _create
        ok, result, status_code = _create(kind, body)
        return web.json_response(result, status=status_code)
    except Exception as e:
        logger.warning(f"quant_memory_archive_create failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_memory_archive_update(request):
    """PUT /v1/quant/memory/archive/{kind}/{id} — 档案更新（P3 CRUD）。

    id 语义：policy=report_hash / research=文件名 / news=news id / summary=note id。
    body: {..payload}（policy{sector?,content} / research{content} /
          news{title?,content?,source?} / summary{title,content}）
    """
    kind = request.match_info.get("kind", "")
    item_id = request.match_info.get("id", "")
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    try:
        from laap.paper_trading.memory_api import archive_update as _update
        ok, result, status_code = _update(kind, item_id, body or {})
        return web.json_response(result, status=status_code)
    except Exception as e:
        logger.warning(f"quant_memory_archive_update failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_memory_archive_delete(request):
    """DELETE /v1/quant/memory/archive/{kind}/{id} — 档案删除（P3 CRUD）。"""
    kind = request.match_info.get("kind", "")
    item_id = request.match_info.get("id", "")
    try:
        from laap.paper_trading.memory_api import archive_delete as _delete
        ok, result, status_code = _delete(kind, item_id)
        return web.json_response(result, status=status_code)
    except Exception as e:
        logger.warning(f"quant_memory_archive_delete failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


# ── 行情研究 · 自选股 CRUD（P3 扩展）──

async def handle_quant_watchlist_get(request):
    """GET /v1/quant/watchlist — 自选股列表。"""
    try:
        from laap.paper_trading.memory_api import watchlist_list
        return web.json_response(watchlist_list())
    except Exception as e:
        logger.warning(f"quant_watchlist_get failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_watchlist_add(request):
    """POST /v1/quant/watchlist — 加入自选股。body: {symbol, note?}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    body = body or {}
    try:
        from laap.paper_trading.memory_api import watchlist_add
        ok, result, status_code = watchlist_add(
            str(body.get("symbol") or ""), str(body.get("note") or ""))
        return web.json_response(result, status=status_code)
    except Exception as e:
        logger.warning(f"quant_watchlist_add failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_watchlist_remove(request):
    """DELETE /v1/quant/watchlist/{symbol} — 移出自选股。"""
    symbol = request.match_info.get("symbol", "")
    try:
        from laap.paper_trading.memory_api import watchlist_remove
        ok, result, status_code = watchlist_remove(symbol)
        return web.json_response(result, status=status_code)
    except Exception as e:
        logger.warning(f"quant_watchlist_remove failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


# ── 行情研究 · 自选股富化快照（2026-08-19）──
async def handle_quant_market_snapshot(request):
    """GET /v1/quant/market/snapshot?symbols=600519,000001 — 自选股富化行情快照。
    返回 items=[{symbol,name,price,change_pct,volume,turnover,high,low,open,
               prev_close,pe,pb,total_mv}...]；数据源失败 fail-closed 返回空 items。"""
    try:
        from laap.paper_trading.em_sources import fetch_realtime_snapshot
        syms = (request.query.get("symbols") or "").split(",")
        syms = [s.strip() for s in syms if s and s.strip()]
        snap = fetch_realtime_snapshot(syms) if syms else {}
        return web.json_response({"items": list(snap.values())})
    except Exception as e:
        logger.warning(f"quant_market_snapshot failed: {e}")
        return web.json_response({"items": []})


# ── 行情研究 · 政策自选（政策解读 + 关注股票 CRUD）──

async def handle_quant_policy_analyze(request):
    """POST /v1/quant/policy/analyze — 政策解读（LLM 提取领域/热点/上游，fail-closed）。

    body: {policy_hash: str} → 从 sector_reports 读政策内容 → 解读。
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    body = body or {}
    policy_hash = str(body.get("policy_hash") or "")
    if not policy_hash:
        return web.json_response({"error": "policy_hash required"}, status=400)
    try:
        from laap.paper_trading.memory_api import policy_analyze
        result = policy_analyze(policy_hash)
        if result.get("error"):
            return web.json_response(result, status=404)
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_policy_analyze failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_policy_upload(request):
    """POST /v1/quant/policy/upload — 政策文件上传（multipart/form-data）。

    解析 doc/docx/wps/txt/md → 自动创建政策（sector=标题，content=正文）
    → 自动解读（LLM 提取领域/热点/上游，fail-closed 降级）。
    Returns: {policy: {id,sector,char_count}, title, content_preview,
              analysis: {sectors,hotspots,upstream,used_fallback}}
    """
    try:
        reader = await request.multipart()
    except Exception as e:
        return web.json_response({"error": f"multipart required: {e}"}, status=400)
    field = None
    try:
        field = await reader.next()
    except Exception:
        pass
    if field is None or getattr(field, "name", None) != "file":
        return web.json_response({"error": "file field required"}, status=400)
    filename = str(getattr(field, "filename", "") or "policy.txt")
    chunks = []
    try:
        while True:
            chunk = await field.read_chunk(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    except Exception as e:
        return web.json_response({"error": f"read failed: {e}"}, status=400)
    raw = b"".join(chunks)
    if not raw:
        return web.json_response({"error": "empty file"}, status=400)
    try:
        from laap.paper_trading.doc_extract import extract_document
        from laap.paper_trading.memory_api import archive_create, policy_analyze
        doc = extract_document(filename, raw)
        if doc.get("error"):
            return web.json_response({"error": doc["error"]}, status=400)
        title = doc.get("title") or "未命名政策"
        content = doc.get("content") or ""
        if len(content) < 20:
            return web.json_response(
                {"error": "解析出的正文过短（不可解析格式？.doc/.wps 请另存为 .docx/.txt）"},
                status=400)
        ok, result, status_code = archive_create(
            "policy", {"sector": title[:50], "content": content})
        if not ok:
            return web.json_response(result, status=status_code)
        analysis = policy_analyze(result["id"])
        return web.json_response({
            "policy": result,
            "title": title,
            "content_preview": content[:300],
            "analysis": analysis,
            "char_count": len(content),
        })
    except Exception as e:
        logger.warning(f"quant_policy_upload failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_policy_picks_get(request):
    """GET /v1/quant/policy/picks — 政策自选股列表。"""
    try:
        from laap.paper_trading.memory_api import policy_picks_list
        return web.json_response(policy_picks_list())
    except Exception as e:
        logger.warning(f"quant_policy_picks_get failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_policy_picks_match(request):
    """GET /v1/quant/policy/picks/match?sectors=领域,行业 — 领域词→自选池股票候选（best-effort）。"""
    try:
        from laap.paper_trading.memory_api import policy_picks_match
        sectors = [s for s in (request.query.get("sectors") or "").split(",") if s.strip()]
        result = policy_picks_match(sectors)
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_policy_picks_match failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_policy_candidates(request):
    """GET /v1/quant/policy/candidates — 政策候选池（本池持久化，从记忆链召回）。
    每只标注 in_watchlist：是否已在自选股。
    """
    try:
        from laap.paper_trading.memory_api import policy_candidates_list
        limit = int(request.query.get("limit") or 50)
        return web.json_response(policy_candidates_list(limit))
    except Exception as e:
        logger.warning(f"quant_policy_candidates failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)

async def handle_quant_policy_batches(request):
    """GET /v1/quant/policy/batches — 政策候选池（按批次，从记忆链「政策批次」召回）。
    每次匹配沉淀一个批次；候选池按批次行展示，点详情展开该批候选。
    """
    try:
        from laap.paper_trading.memory_api import policy_batches_list
        limit = int(request.query.get("limit") or 30)
        return web.json_response(policy_batches_list(limit))
    except Exception as e:
        logger.warning(f"quant_policy_batches failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)

async def handle_quant_policy_picks_add(request):
    """POST /v1/quant/policy/picks — 添加政策自选股。

    body: {policy_hash?, sector?, symbol, note?, direction?}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    body = body or {}
    try:
        from laap.paper_trading.memory_api import policy_picks_add
        ok, result, status_code = policy_picks_add(
            str(body.get("policy_hash") or ""), str(body.get("sector") or ""),
            str(body.get("symbol") or ""), str(body.get("note") or ""),
            str(body.get("direction") or ""))
        return web.json_response(result, status=status_code)
    except Exception as e:
        logger.warning(f"quant_policy_picks_add failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_policy_picks_remove(request):
    """DELETE /v1/quant/policy/picks/{id} — 删除政策自选股。"""
    pick_id = request.match_info.get("id", "")
    try:
        from laap.paper_trading.memory_api import policy_picks_remove
        ok, result, status_code = policy_picks_remove(pick_id)
        return web.json_response(result, status=status_code)
    except Exception as e:
        logger.warning(f"quant_policy_picks_remove failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


# ── K 线采集（手动 + 定时，数据源 tab 统一管理）──

_kline_last_collect: Dict[str, Any] = {"ts": 0.0, "result": None}


def _kline_schedule_path():
    from laap.config.paths import get_laap_root
    return get_laap_root() / "state" / "kline_schedule.json"


def _load_kline_schedule() -> Dict[str, Any]:
    import json as _json
    p = _kline_schedule_path()
    default = {"enabled": False, "time": "15:35", "last_run": ""}
    if p.exists():
        try:
            d = _json.loads(p.read_text(encoding="utf-8"))
            return {
                "enabled": bool(d.get("enabled")),
                "time": str(d.get("time") or "15:35"),
                "last_run": str(d.get("last_run") or ""),
            }
        except Exception as e:
            logger.warning(f"kline schedule load failed: {e}")
    return default


def _save_kline_schedule(sched: Dict[str, Any]) -> bool:
    import json as _json
    try:
        p = _kline_schedule_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(sched, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning(f"kline schedule save failed: {e}")
        return False


def _kline_sched_loop():
    """K 线定时采集循环：enabled 时每日到点 + 交易日触发一次（懒加载线程）。"""
    while True:
        try:
            sched = _load_kline_schedule()
            if sched.get("enabled"):
                now = time.localtime()
                hhmm = f"{now.tm_hour:02d}:{now.tm_min:02d}"
                today = time.strftime("%Y-%m-%d")
                if hhmm == sched.get("time") and sched.get("last_run") != today:
                    from laap.paper_trading.daily_pipeline import QuantDailyScheduler
                    if QuantDailyScheduler._is_trading_day(source="local"):
                        from laap.paper_trading.kline_collector import collect_watchlist_kline
                        result = collect_watchlist_kline()
                        _kline_last_collect["ts"] = time.time()
                        _kline_last_collect["result"] = result
                        sched["last_run"] = today
                        _save_kline_schedule(sched)
                        logger.info("kline schedule collect done: %s", result.get("collected"))
        except Exception as e:
            logger.warning(f"kline scheduler tick failed: {e}")
        time.sleep(60)


_kline_scheduler_started = False


def _start_kline_scheduler():
    """启动 K 线定时采集线程（幂等，daemon）。"""
    global _kline_scheduler_started
    if _kline_scheduler_started:
        return
    _kline_scheduler_started = True
    import threading
    threading.Thread(target=_kline_sched_loop, daemon=True, name="kline-scheduler").start()
    logger.info("kline scheduler thread started")


async def handle_quant_kline_collect(request):
    """POST /v1/quant/kline/collect — 手动采集自选池日 K（同步，约 20-60s）。"""
    try:
        from laap.paper_trading.kline_collector import collect_watchlist_kline
        result = collect_watchlist_kline()
        _kline_last_collect["ts"] = time.time()
        _kline_last_collect["result"] = result
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_kline_collect failed: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def handle_quant_kline_status(request):
    """GET /v1/quant/kline/status — K 线存储统计 + 定时配置 + 最近采集结果。"""
    try:
        from laap.paper_trading.kline_collector import kline_stats, kline_latest_day
        return web.json_response({
            "stats": kline_stats(),
            "schedule": _load_kline_schedule(),
            "latest_day": kline_latest_day(),
            "last_collect": _kline_last_collect.get("result"),
            "last_collect_ts": _kline_last_collect.get("ts", 0.0),
        })
    except Exception as e:
        logger.warning(f"quant_kline_status failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_kline_schedule_get(request):
    """GET /v1/quant/kline/schedule — 读取定时采集配置。"""
    return web.json_response(_load_kline_schedule())


async def handle_quant_kline_schedule_set(request):
    """POST /v1/quant/kline/schedule — 配置定时采集。body: {enabled?, time?}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    body = body or {}
    sched = _load_kline_schedule()
    if "enabled" in body:
        sched["enabled"] = bool(body["enabled"])
    if body.get("time"):
        sched["time"] = str(body["time"])
    if not _save_kline_schedule(sched):
        return web.json_response({"error": "save failed"}, status=500)
    return web.json_response({"schedule": sched, "message": "定时采集配置已保存"})


async def handle_quant_memory_meta(request):
    """GET /v1/quant/memory/meta — 记忆体（元认识）自报告。"""
    try:
        from laap.paper_trading.memory_api import meta as _mem_meta
        return web.json_response(_mem_meta())
    except Exception as e:
        logger.warning(f"quant_memory_meta failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_strategies(request):
    """GET /v1/quant/strategies — 查询所有策略及映射（name/display_name/description/type）。"""
    try:
        from laap.paper_trading.strategy_templates import list_strategy_meta
        strategies = list_strategy_meta()
        return web.json_response({
            "strategies": strategies,
            "default": "multi_factor",
            "count": len(strategies),
        })
    except Exception as e:
        logger.warning(f"quant_strategies failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_system_logs(request):
    """GET /v1/quant/system/logs — 系统日志（L1-L4，发现→上报→处理→总结闭环）。

    查询参数:
      level   : INFO/WARNING/ERROR/CRITICAL，空=全部
      category: database/file/datasource/port/llm/auth/general，空=全部
      limit   : 返回条数（默认 100）
      offset  : 偏移（默认 0）

    返回:
      {
        "logs": [{ts, level, category, root_cause, text, action, auto_handled, note}],
        "summary": {"total", "by_level": {INFO:N,...}, "by_category": {...}, "last_scan_ts"},
        "latest_scan": {from orchestrator last_error_result}
      }
    """
    try:
        from urllib.parse import parse_qs
        qs = parse_qs(request.query_string)
        level_filter = (qs.get("level") or [""])[0].upper()
        cat_filter = (qs.get("category") or [""])[0].lower()
        try:
            limit = min(int((qs.get("limit") or ["100"])[0]), 500)
            offset = max(int((qs.get("offset") or ["0"])[0]), 0)
        except ValueError:
            limit, offset = 100, 0

        db = _get_quant_db()
        logs: List[Dict[str, Any]] = []
        total = 0
        by_level: Dict[str, int] = {"INFO": 0, "WARNING": 0, "ERROR": 0, "CRITICAL": 0}
        by_category: Dict[str, int] = {}
        if db is not None:
            try:
                conn = db.conn()
                try:
                    # 幂等建表（PG/SQLite 双后端兼容）
                    from laap.paper_trading.error_monitor import _events_table as _et
                    _et(conn)
                    # ── 查询日志列表 ──
                    cond_parts = []
                    params: list = []
                    if level_filter:
                        cond_parts.append("priority = ?")
                        params.append(2 if level_filter == "CRITICAL"
                                      else (1 if level_filter == "ERROR" else 0))
                    if cat_filter:
                        cond_parts.append("category = ?")
                        params.append(cat_filter)
                    where = ("WHERE " + " AND ".join(cond_parts)) if cond_parts else ""
                    cnt = conn.execute(
                        f"SELECT COUNT(*) FROM error_events {where}", params).fetchone()[0]
                    total = int(cnt)
                    rows = conn.execute(
                        f"SELECT * FROM error_events {where} "
                        "ORDER BY ts DESC LIMIT ? OFFSET ?",
                        [*params, limit, offset]).fetchall()
                    for r in rows:
                        d = dict(r)
                        d["ts_iso"] = datetime.fromtimestamp(d["ts"]).strftime("%Y-%m-%d %H:%M:%S")
                        d["level"] = ("CRITICAL" if d["priority"] == 2 and d.get("root_cause") in
                                      ("db_write_error", "port_conflict", "auth_failed")
                                      else "ERROR" if d["priority"] == 2
                                      else "WARNING" if d["priority"] == 1
                                      else "INFO")
                        logs.append({
                            "ts": d["ts"],
                            "ts_iso": d["ts_iso"],
                            "level": d["level"],
                            "category": d["category"],
                            "root_cause": d["root_cause"],
                            "text": d.get("action", "")[:160],
                            "count": d["count"],
                            "auto_handled": bool(d["auto_handled"]),
                            "pushed": bool(d["pushed"]),
                            "note": d.get("note", ""),
                        })
                    # ── 聚合 summary（同连接，避免重复开连）──
                    for r in conn.execute(
                            "SELECT priority, COUNT(*) as n FROM error_events GROUP BY priority"
                    ).fetchall():
                        p, n = r
                        label = "ERROR" if p == 2 else "WARNING" if p == 1 else "INFO"
                        by_level[label] = by_level.get(label, 0) + n
                    for r in conn.execute(
                            "SELECT category, COUNT(*) as n FROM error_events GROUP BY category"
                    ).fetchall():
                        by_category[r[0]] = r[1]
                    # total 用 distinct ts（与旧口径一致）
                    total_distinct = conn.execute(
                        "SELECT COUNT(DISTINCT ts) FROM error_events").fetchone()[0]
                    if total == 0:
                        total = int(total_distinct)
                finally:
                    conn.close()
            except Exception as e:
                logger.warning(f"system_logs query failed: {e}")

        # 最新巡检快照（来自 orchestrator）
        latest_scan = None
        orch = _event_orchestrator
        if orch is not None and orch.last_error_result:
            err = orch.last_error_result
            latest_scan = {
                "ts": err.get("ts"),
                "ts_iso": datetime.fromtimestamp(err["ts"]).strftime("%Y-%m-%d %H:%M:%S"),
                "found": err.get("found", 0),
                "analyses": err.get("analyses", [])[:10],
                "disposition": err.get("disposition"),
                "pushed": err.get("pushed", False),
            }

        return web.json_response({
            "logs": logs,
            "summary": {
                "total": total,
                "by_level": by_level,
                "by_category": by_category,
                "last_scan_ts": latest_scan["ts"] if latest_scan else None,
            },
            "latest_scan": latest_scan,
        })
    except Exception as e:
        logger.warning(f"quant_system_logs failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_events_status(request):
    """GET /v1/quant/events/status — 事件驱动编排器状态 (2026-08-17)。"""
    try:
        orch = _event_orchestrator
        if orch is None:
            return web.json_response({"running": False,
                                      "hint": "set LAAP_EVENT_DRIVEN=1 to enable"})
        status = orch.status()
        status["recent_events"] = list(_recent_events)[-50:]
        status["ws_bridge"] = (_get_ws_bridge().stats()
                               if _get_ws_bridge() is not None else None)
        return web.json_response(status)
    except Exception as e:
        logger.warning(f"quant_events_status failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_dashboard_init(request):
    """GET /v1/quant/dashboard/init — 前端首屏聚合快照（G4，15173 信号列表页）。

    一次返回 signals / trades / net_values / strategies / system_status / ws_url，
    减少前端初始化 N 次请求；实时更新走 WS（/v1/quant/events/ws）。
    Redis 短缓存（10s）：减小面板切换/刷新触发的重复 DB 读；实时增量由 WS 承担。
    """
    try:
        from laap.cache_backend import cache_get, cache_set
        cached = cache_get("quant:dashboard:init")
        if cached is not None:
            return web.json_response(cached)
        from laap.paper_trading.strategy_templates import list_strategy_meta
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        conn = db.conn()
        try:
            # 2026-08-19: "今日信号"面板只显示当日信号。ts 为 epoch 秒(UTC),
            # 按 Asia/Shanghai 当天 00:00 起过滤, 避免历史买入信号被误当今日信号。
            from datetime import datetime
            from zoneinfo import ZoneInfo
            _sh = ZoneInfo("Asia/Shanghai")
            _now = datetime.now(_sh)
            _day_start = datetime(_now.year, _now.month, _now.day, tzinfo=_sh).timestamp()
            signals = [dict(r) for r in conn.execute(
                "SELECT * FROM signals WHERE ts >= ? ORDER BY ts DESC LIMIT 50",
                (_day_start,)).fetchall()]
            trades = [dict(r) for r in conn.execute(
                "SELECT * FROM trades ORDER BY entry_ts DESC LIMIT 50").fetchall()]
            net_values = [dict(r) for r in conn.execute(
                "SELECT * FROM net_values ORDER BY ts DESC LIMIT 60").fetchall()]
        finally:
            conn.close()
        # WS 地址：与请求同 host（跨端口前端也能直连），协议按请求 scheme 推断
        scheme = request.headers.get("X-Forwarded-Proto", "http")
        host = request.host or "127.0.0.1:11546"
        ws_scheme = "wss" if scheme == "https" else "ws"
        # 聚合标的名（东财 F10，24h 缓存；查不到不影响快照）
        # 覆盖：signals ∪ trades ∪ 自选股列表（STOCK_LIST）——实时 tick 榜按自选股推送，
        # 仅 signals/trades 会缺大部分简称。
        all_symbols: set = {s["symbol"] for s in signals} | {
            t["symbol"] for t in trades}
        try:
            from laap.paper_trading.daily_pipeline import _get_watchlist_symbols
            wl = _get_watchlist_symbols() or []
            all_symbols |= {s for s in wl if s}
        except Exception as e:  # pragma: no cover - fail-closed
            logger.debug("watchlist symbols lookup failed: %s", e)
        stock_names = {}
        try:
            # 2026-08-19: 外部网络(东财F10)异步预取+缓存。async_fetch_stock_names
            # 非阻塞: 首读返回缓存(可能空)并触发后台线程预取, 完成后写24h缓存,
            # 后续/刷新自动拿到简称。彻底不阻塞 dashboard 主流程。
            from laap.paper_trading.signal_events import async_fetch_stock_names
            stock_names = async_fetch_stock_names(sorted(all_symbols))
        except Exception as e:  # pragma: no cover - fail-closed
            logger.debug("stock_names lookup failed: %s", e)
        payload = {
            "signals": signals,
            "trades": trades,
            "net_values": net_values,
            "strategies": list_strategy_meta(),
            "system_status": {
                "event_driven": _event_orchestrator is not None,
                "recent_events": list(_recent_events)[-20:],
                "ws_bridge": (_get_ws_bridge().stats()
                              if _get_ws_bridge() is not None else None),
            },
            "ws_url": f"{ws_scheme}://{host}/v1/quant/events/ws",
            "stock_names": stock_names,
            "market_symbols": _market_watch_symbols(),
            "ts": time.time(),
        }
        cache_set("quant:dashboard:init", payload, ttl=10)
        return web.json_response(payload)
    except Exception as e:
        logger.warning(f"quant_dashboard_init failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_events_ws(request):
    """GET /v1/quant/events/ws — WebSocket 实时事件推送 (EventBus → ws:// 桥接, 2026-08-18)。

    事件源由 LAAP_EVENT_DRIVEN=1 驱动；未启用时连接可建但无事件（fail-closed）。

    上行（JSON）:
      {"op": "subscribe", "topics": ["market.limitup.*", "system.status", ...]}
      {"op": "unsubscribe", "topics": [...]}
      {"op": "ping"}
    下行: Event.to_dict() = {"type", "ts", "payload", "source"}
    """
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    bridge = _get_ws_bridge()
    if bridge is None:
        await ws.send_json({"type": "system.status",
                            "payload": {"running": False,
                                        "hint": "EventWsBridge unavailable"}})
        await ws.close()
        return ws
    try:
        cid = bridge.register(ws)
    except Exception as e:
        logger.warning(f"quant_events_ws register failed: {e}")
        await ws.close()
        return ws
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                op = data.get("op")
                topics = data.get("topics")
                if op == "subscribe":
                    bridge.set_topics(cid, topics)
                    await ws.send_json({"type": "system.internal.subscribed",
                                        "payload": {"topics": topics or ["*"]}})
                elif op == "unsubscribe" and isinstance(topics, list):
                    bridge.remove_topics(cid, topics)
                elif op == "ping":
                    await ws.send_json({"type": "pong", "ts": time.time()})
            elif msg.type == web.WSMsgType.ERROR:
                break
    except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
        pass
    finally:
        bridge.unregister(cid)
        try:
            await ws.close()
        except Exception:
            pass
    return ws


async def handle_quant_daily_cycle(request):
    """POST /v1/quant/daily_cycle — 日终闭环（真实K线→信号→交易自我审核→交易→净值）。

    body: {symbols: [...], params: {...} 可选（缺省用 STRATEGY_PARAMS）}
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    symbols = body.get("symbols")
    if not symbols:
        from laap.paper_trading.daily_pipeline import _get_watchlist_symbols, DEFAULT_SYMBOLS
        symbols = _get_watchlist_symbols() or DEFAULT_SYMBOLS
    params = body.get("params")
    if params is None:
        try:
            from laap.paper_trading.strategy import STRATEGY_PARAMS
            params = dict(STRATEGY_PARAMS)
        except Exception:
            return web.json_response({"error": "params required"}, status=400)
    try:
        loop = _get_paper_loop()
        if loop is None:
            return web.json_response({"error": "paper loop unavailable"}, status=500)

        # 新闻x量价两轨门（2026-08-18 修复：此前 API 路径未传 news_gate，
        # 新闻判定层形同虚设；现在默认启用，bearish/fake_news 否决 buy，fail-closed）
        def _news_gate_fn(symbol: str):
            try:
                from laap.paper_trading.news_verifier import get_verdicts_for_symbol
                return get_verdicts_for_symbol(symbol)
            except Exception as e:
                logger.warning(f"news_gate failed for {symbol}: {e}")
                return []

        result = loop.run_daily_cycle(symbols, params, ohlcv_map=None,
                                      news_gate=_news_gate_fn)
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_daily_cycle failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_system_scan(request):
    """POST /v1/quant/system/scan — 手动触发一次错误闭环巡检（发现→分析→处理→总结）。

    直接调用 EventOrchestrator._scan_errors()，结果写入 error_events 表并推 WS 事件。
    返回: {scanned, found, analyses, disposition, persisted}
    """
    try:
        orch = _event_orchestrator
        if orch is None:
            return web.json_response(
                {"error": "event orchestrator not running",
                 "hint": "set LAAP_EVENT_DRIVEN=1"}, status=503)
        result = orch._scan_errors()
        if result is None:
            return web.json_response({"error": "scan failed"}, status=500)
        return web.json_response({
            "scanned": True,
            "ts": result.get("ts") or time.time(),
            "found": result.get("found", 0),
            "analyses": result.get("analyses", [])[:8],
            "disposition": result.get("disposition", {}),
            "persisted": result.get("persisted", 0),
            "summary": result.get("summary", ""),
        })
    except Exception as e:
        logger.warning(f"quant_system_scan failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_apply_params(request):
    """POST /v1/quant/apply_params — 把参数搜索结果落回代码（M4 治理 + 交易自我审核）。

    body: {params: {...}, self_review: bool (默认 true), rationale: str}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    body = body or {}
    params = body.get("params")
    if not isinstance(params, dict) or not params:
        return web.json_response({"error": "params dict required"}, status=400)
    try:
        qe = _get_quant_engine()
        if qe is None:
            return web.json_response({"error": "quant engine unavailable"}, status=500)
        result = qe.apply_params_to_code(
            params, rationale=body.get("rationale", "api:apply_params"),
            method="api", self_review=body.get("self_review", True))
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_apply_params failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_evolve_approve(request):
    """POST /v1/quant/evolve/approve — 人工批准并部署。"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    mutation_id = (body or {}).get("mutation_id", "")
    if not mutation_id:
        return web.json_response({"error": "mutation_id required"}, status=400)
    try:
        qe = _get_quant_engine()
        if qe is None:
            return web.json_response({"error": "quant engine unavailable"}, status=500)
        result = qe.approve_and_deploy(
            mutation_id, approver=(body or {}).get("approver", "api"))
        status_code = 200 if result.get("status") == "deployed" else 409
        return web.json_response(result, status=status_code)
    except Exception as e:
        logger.warning(f"quant_evolve_approve failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_evolve_reject(request):
    """POST /v1/quant/evolve/reject — 拒绝并回滚最近一次部署。"""
    try:
        qe = _get_quant_engine()
        if qe is None:
            return web.json_response({"error": "quant engine unavailable"}, status=500)
        result = qe.rollback_last()
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_evolve_reject failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_evolve_audit(request):
    """GET /v1/quant/evolve/audit — 查询进化审计。"""
    try:
        engine = _get_code_evolution_engine()
        if engine is None or getattr(engine, "audit", None) is None:
            return web.json_response({"error": "audit unavailable"}, status=500)
        return web.json_response({
            "stats": engine.audit.stats(),
            "recent": engine.audit.query(limit=20),
        })
    except Exception as e:
        logger.warning(f"quant_evolve_audit failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_trades(request):
    """GET /v1/quant/trades — 查询交易记录。

    2026-08-17: 两级缓存 (redis → 内存, TTL 10s)。
    """
    try:
        from laap.cache_backend import cache_get, cache_set
        symbol = request.query.get("symbol", "")
        ck = f"quant:trades:{symbol or 'all'}"
        cached = cache_get(ck)
        if cached is not None:
            return web.json_response(cached)
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        conn = db.conn()
        try:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE symbol=? ORDER BY entry_ts",
                    (symbol,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY entry_ts DESC"
                ).fetchall()
        finally:
            conn.close()
        result = [dict(r) for r in rows]
        cache_set(ck, result, ttl=_QUANT_READ_TTL)
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_trades failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_net_values(request):
    """GET /v1/quant/net_values — 查询净值序列 (ts/cash/equity/total，升序)。

    2026-08-17: 两级缓存 (redis → 内存, TTL 10s) —— 高频查询加速。
    """
    try:
        from laap.cache_backend import cache_get, cache_set
        ck = "quant:net_values"
        cached = cache_get(ck)
        if cached is not None:
            return web.json_response(cached)
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        conn = db.conn()
        try:
            rows = conn.execute(
                "SELECT ts, cash, equity, total FROM net_values ORDER BY ts ASC"
            ).fetchall()
        finally:
            conn.close()
        result = [dict(r) for r in rows]
        cache_set(ck, result, ttl=_QUANT_READ_TTL)
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_net_values failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_signals(request):
    """GET /v1/quant/signals — 查询交易信号（可带 ?symbol= 过滤）。

    2026-08-17: 两级缓存 (redis → 内存, TTL 10s)。
    """
    try:
        from laap.cache_backend import cache_get, cache_set
        symbol = request.query.get("symbol", "")
        ck = f"quant:signals:{symbol or 'all'}"
        cached = cache_get(ck)
        if cached is not None:
            return web.json_response(cached)
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        conn = db.conn()
        try:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM signals WHERE symbol=? ORDER BY ts DESC LIMIT 100",
                    (symbol,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM signals ORDER BY ts DESC LIMIT 100").fetchall()
        finally:
            conn.close()
        result = [dict(r) for r in rows]
        cache_set(ck, result, ttl=_QUANT_READ_TTL)
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_signals failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_orders(request):
    """GET /v1/quant/orders — 查询订单。"""
    try:
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        conn = db.conn()
        try:
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY filled_ts DESC, id DESC LIMIT 100").fetchall()
        finally:
            conn.close()
        return web.json_response([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"quant_orders failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_outcomes(request):
    """GET /v1/quant/outcomes — 查询结果回填（教训）。"""
    try:
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        conn = db.conn()
        try:
            rows = conn.execute(
                "SELECT * FROM outcomes ORDER BY trade_id DESC LIMIT 100").fetchall()
        finally:
            conn.close()
        return web.json_response([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"quant_outcomes failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_scheduler_stats(request):
    """GET /v1/quant/scheduler/stats — 量化每日管线调度器状态。

    返回 QuantDailyScheduler 的运行状态（M3 量化闭环可观测性）。
    用于验证 M5 缺陷是否修复：tick 执行次数、最后一次 apply_status。
    """
    try:
        scheduler = _quant_daily_scheduler
        if scheduler is None:
            return web.json_response({
                "error": "QuantDailyScheduler not started (LAAP_QUANT_DAILY=1 required)",
                "running": False,
            })
        return web.json_response(scheduler.stats())
    except Exception as e:
        logger.warning(f"quant_scheduler_stats failed: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_quant_decisions(request):
    """GET /v1/quant/decisions — 查询决策留痕（POST 用于写入）。

    2026-08-17: 两级缓存 (redis → 内存, TTL 10s)。
    """
    try:
        from laap.cache_backend import cache_get, cache_set
        symbol = request.query.get("symbol", "")
        ck = f"quant:decisions:{symbol or 'all'}"
        cached = cache_get(ck)
        if cached is not None:
            return web.json_response(cached)
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        conn = db.conn()
        try:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM decisions WHERE symbol=? ORDER BY ts DESC LIMIT 100",
                    (symbol,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM decisions ORDER BY ts DESC LIMIT 100").fetchall()
        finally:
            conn.close()
        result = [dict(r) for r in rows]
        cache_set(ck, result, ttl=_QUANT_READ_TTL)
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_decisions failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_kline(request):
    """GET /v1/quant/kline — 查询K线数据 (symbol + days 参数)。

    返回完整OHLCV五元组 (open, close, high, low, volume)，供策略评估使用。
    格式: [{"date": ..., "open": ..., "close": ..., "high": ..., "low": ..., "volume": ...}, ...]
    """
    try:
        from laap.paper_trading.kline_source import load_ohlcv
        symbol = request.query.get("symbol", "600519")
        days = int(request.query.get("days", "120"))
        ohlcv, quality = load_ohlcv(symbol, days=days, with_quality=True)

        # 转换为JSON格式
        # OHLCV格式: (open, close, high, low, volume)
        data = []
        for row in ohlcv:
            data.append({
                "open": row[0],
                "close": row[1],
                "high": row[2],
                "low": row[3],
                "volume": row[4] if len(row) > 4 else 0
            })

        return web.json_response({
            "symbol": symbol,
            "days": days,
            "data": data,
            "quality": quality,
            "count": len(data)
        })
    except Exception as e:
        logger.warning(f"quant_kline failed: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ── 新闻情报闭环 API（P4）──

def _get_news_llm_call():
    """按 LLM_SOURCES 链构建 news_verifier 的 llm_call 契约（prompt/system/max_tokens）。

    支持 openai/urllib/ollama/local/cli 多源回退（见 laap.paper_trading.llm_sources）。
    """
    from laap.paper_trading.llm_sources import build_llm_call
    return build_llm_call()


def _get_news_pipeline(auto_order: bool = True):
    from laap.paper_trading.news_pipeline import NewsSignalPipeline
    from laap.paper_trading.quant_config import build_fee_model
    return NewsSignalPipeline(loop=_get_paper_loop(), db=_get_quant_db(),
                              llm_call=_get_news_llm_call(),
                              fee_model=build_fee_model())


async def handle_quant_news(request):
    """GET /v1/quant/news?symbol= — 新闻判定 + 联表新闻内容（news_verdicts ⋈ news_items）。
    2026-08-19: 加 redis 缓存 + TTL（key=quant:news:<symbol|all>, TTL=10min）。
    命中直接返回，消除每次打开的 DB 连接 + 查询，弹窗新闻秒出。"""
    symbol = request.query.get("symbol", "")
    try:
        from laap.paper_trading.cache_backend import cache_get, cache_set
        ck = f"quant:news:{symbol or 'all'}"
        cached = cache_get(ck)
        if cached is not None:
            return web.json_response(cached)
    except Exception:
        cached = None
    try:
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        conn = db.conn()
        try:
            if symbol:
                rows = conn.execute(
                    "SELECT v.*, n.title, n.content, n.source AS news_source, n.url "
                    "FROM news_verdicts v LEFT JOIN news_items n ON v.news_id = n.id "
                    "WHERE v.symbol=? ORDER BY v.ts DESC LIMIT 50", (symbol,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT v.*, n.title, n.content, n.source AS news_source, n.url "
                    "FROM news_verdicts v LEFT JOIN news_items n ON v.news_id = n.id "
                    "ORDER BY v.ts DESC LIMIT 50").fetchall()
        finally:
            conn.close()
        data = [dict(r) for r in rows]
        try:
            cache_set(ck, data, ttl=600)  # 10min
        except Exception:
            pass
        return web.json_response(data)
    except Exception as e:
        logger.warning(f"quant_news failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_profile(request):
    """GET /v1/quant/profile?symbol= — 个股资料/股票概况。"""
    try:
        from laap.paper_trading.news_intel import fetch_stock_profile
        symbol = request.query.get("symbol", "")
        if not symbol:
            return web.json_response({"error": "symbol required"}, status=400)
        prof, meta = fetch_stock_profile(symbol)
        return web.json_response({"profile": prof.to_dict() if prof else None,
                                  "meta": meta})
    except Exception as e:
        logger.warning(f"quant_profile failed: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_quant_news_verify(request):
    """POST /v1/quant/news/verify — 手动判定单条新闻 {symbol,title,content}。"""
    try:
        data = await request.json()
        symbol = data.get("symbol", "")
        title = data.get("title", "")
        content = data.get("content", "")
        if not symbol or not title:
            return web.json_response({"error": "symbol+title required"}, status=400)
        from laap.paper_trading.news_intel import NewsItem, fetch_stock_profile
        from laap.paper_trading.news_verifier import verify_news, compute_tech_state
        item = NewsItem(symbol=symbol, title=title, content=content)
        profile, _meta = fetch_stock_profile(symbol)
        ts = compute_tech_state(symbol)
        v = verify_news(item, profile, ts, llm_call=_get_news_llm_call())
        return web.json_response(v.to_dict())
    except Exception as e:
        logger.warning(f"quant_news_verify failed: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_quant_news_scan(request):
    """POST /v1/quant/news/scan — 立即跑一次全管线 {symbol, auto_order, force}。"""
    try:
        data = await request.json()
        symbol = data.get("symbol", "")
        auto_order = bool(data.get("auto_order", False))
        force = bool(data.get("force", False))
        if not symbol:
            return web.json_response({"error": "symbol required"}, status=400)
        pipe = _get_news_pipeline(auto_order=auto_order)
        result = pipe.run(symbol, auto_order=auto_order, force=force)
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_news_scan failed: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_quant_risk_rejections(request):
    """GET /v1/quant/risk/rejections?symbol= — 风控拒绝审计（刑部）。"""
    try:
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        symbol = request.query.get("symbol", "")
        conn = db.conn()
        try:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM risk_rejections WHERE symbol=? "
                    "ORDER BY ts DESC LIMIT 100", (symbol,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM risk_rejections ORDER BY ts DESC LIMIT 100").fetchall()
        finally:
            conn.close()
        return web.json_response([dict(r) for r in rows])
    except Exception as e:
        logger.warning(f"quant_risk_rejections failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_config(request):
    """GET /v1/quant/config — 量化可调参数读取（只读，quant_config 单源）。

    返回 quant_config.to_dict()（全部 _DEFAULTS 实时读 env）+ 当前启用策略组合
    （PAPER_TRADING_STRATEGY 逗号分隔解析）。供前端 M8②参数设置 / M4策略中心
    读取展示；写入走 POST /v1/quant/apply_params（M4 治理 + 交易自我审核）。
    """
    try:
        from laap.paper_trading import quant_config as qc
        cfg = qc.to_dict()
        raw_strategy = str(os.environ.get(
            "PAPER_TRADING_STRATEGY", cfg.get("PAPER_TRADING_STRATEGY", "multi_factor")))
        strategy_list = [s.strip() for s in raw_strategy.split(",") if s.strip()]
        # 数据源列表：逗号分隔源候选（前端展示/编辑用，与 quant_config 语义一致）
        source_keys = ("MARKET_SOURCES", "KLINE_SOURCES", "NEWS_SOURCES",
                       "PROFILE_SOURCES", "REPORT_SOURCES", "CALENDAR_SOURCES",
                       "LLM_SOURCES")
        sources = {k: [s.strip() for s in str(cfg.get(k, "")).split(",") if s.strip()]
                   for k in source_keys}
        # 默认值表（前端「恢复默认」数据源；只读增强，不回填当前值）
        defaults = {k: v for k, v in qc._DEFAULTS.items()}
        return web.json_response({
            "config": cfg,
            "defaults": defaults,
            "sources": sources,
            "strategy": {"raw": raw_strategy, "list": strategy_list},
            "ts": time.time(),
        })
    except Exception as e:
        logger.warning(f"quant_config failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_params_apply(request):
    """POST /v1/quant/params/apply — 运行时参数即时生效（P1 支持批次）。

    body: {params: {KEY: value, ...}, rationale?: str}
    - 白名单：键必须 ∈ quant_config._DEFAULTS（未知键进 rejected，fail-closed）
    - 类型强制：按默认值类型校验/转换（bool 严格只认 1/true/on → "1"，与既有
      `os.environ.get()=="1"` 语义一致；数值非法 → rejected，不静默回落默认）
    - 写入 os.environ → quant_config 惰性读 env → **当前进程立即生效**（不落盘，
      重启恢复 env/默认值；落码持久化仍走 POST /v1/quant/apply_params 治理）
    - 广播 system.internal.config.updated → WS 前端重拉 config 刷新（前后端同步）
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    body = body or {}
    params = body.get("params")
    if not isinstance(params, dict) or not params:
        return web.json_response({"error": "params dict required"}, status=400)
    try:
        from laap.paper_trading import quant_config as qc
    except Exception as e:
        logger.warning(f"quant_config import failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)
    applied: Dict[str, Any] = {}
    rejected: Dict[str, str] = {}
    for key, raw in params.items():
        if key not in qc._DEFAULTS:
            rejected[key] = "unknown key (not in quant_config._DEFAULTS)"
            continue
        default = qc._DEFAULTS[key]
        try:
            if isinstance(default, bool):
                # bool 严格 1/true/yes/on → "1"，其余 → "0"（对齐 _coerce 语义）
                # 直接落 "1"/"0"（不可 str(True)="True"，_coerce 只认 "1"）
                raw_str = "1" if raw in (True, 1, "1", "true", "True", "yes", "on") else "0"
                coerced = raw_str == "1"
                os.environ[key] = raw_str
                applied[key] = coerced
                continue
            elif isinstance(raw, bool):
                rejected[key] = "bool value for non-bool key"
                continue
            elif isinstance(default, (int, float)):
                try:
                    float(str(raw))
                except (TypeError, ValueError):
                    rejected[key] = "not a number"
                    continue
                raw_str = str(raw)
            elif isinstance(raw, (dict, list, tuple)):
                rejected[key] = "non-scalar value not supported"
                continue
            else:
                raw_str = str(raw)
            coerced = qc._coerce(key, raw_str, default)
            os.environ[key] = str(coerced)
            applied[key] = coerced
        except Exception as e:
            rejected[key] = f"coerce failed: {e}"
    # 广播：WS 前端收到后重拉 config（system.internal.* 已在 ws_bridge 订阅内）
    try:
        from laap.paper_trading.event_bus import EventBus, Event
        EventBus().publish(Event(
            "system.internal.config.updated",
            {"keys": list(applied.keys()), "rejected": rejected, "ts": time.time()},
            source="api:params_apply"))
    except Exception as e:
        logger.debug(f"config.updated broadcast skipped: {e}")
    return web.json_response({
        "applied": applied,
        "rejected": rejected,
        "count": len(applied),
        "note": ("runtime apply: 当前进程实时生效（不落盘）；"
                 "落码持久化走 POST /v1/quant/apply_params"),
        "ts": time.time(),
    })


async def handle_quant_account(request):
    """GET /v1/quant/account — 账户资产聚合（现金/持仓/净值/浮盈亏/今日盈亏）。

    供前端 M1 总览工作台 / M7 账户风控 / M8③个人中心读取：
      - cash: 账本现金（PaperLedger.cash，最新净值恢复）
      - positions: 未平仓持仓（symbol/quantity/entry_price/current_price/unrealized_pnl）
      - open_position_value: 持仓成本合计
      - unrealized_pnl: 持仓实时浮盈亏（Σ qty × (实时价 - entry_price)）
      - today_pnl: 当日已实现 PnL（今日平仓 trades pnl 之和，含费净额）
      - latest_net_value / net_values: 净值快照
    行情 MTM fail-closed：单标的取价失败/降级时该标的浮盈亏按 0 计（不夸大）；
    不影响现金/已实现等确定性数据。
    """
    try:
        loop = _get_paper_loop()
        db = _get_quant_db()
        if loop is None or db is None:
            return web.json_response({"error": "internal error"}, status=500)
        ledger = getattr(loop, "ledger", None)
        market = getattr(loop, "market", None)
        # 现金对账（2026-08-19 修复）：净值快照只在日终落库，盘中平仓/买入
        # 仅更新内存现金；重启后若直接读内存现金会丢失快照后的现金流。
        # 按持久化事实重算（最新快照 + 快照后交易现金流），当前进程即时自愈。
        if ledger is not None:
            try:
                ledger.reconcile_cash()
            except Exception as e:
                logger.debug(f"account reconcile_cash failed: {e}")
        positions = []
        open_value = 0.0
        unrealized = 0.0
        if ledger is not None:
            try:
                pos_list = ledger.open_positions()
                for p in pos_list:
                    cost = p.quantity * (p.entry_price or 0.0)
                    open_value += cost
                    cur = None
                    pos_unreal = 0.0
                    if market is not None:
                        try:
                            price, meta = market.get_price(p.symbol)
                            # fail-closed：降级/异常价不算浮盈亏（避免 stub 合成价误导）
                            if price and price > 0 and not (meta or {}).get("used_fallback"):
                                cur = round(float(price), 4)
                                pos_unreal = (cur - (p.entry_price or 0.0)) * p.quantity
                        except Exception as e:
                            logger.debug(f"account MTM failed for {p.symbol}: {e}")
                    unrealized += pos_unreal
                    positions.append({
                        "symbol": p.symbol,
                        "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                        "quantity": p.quantity,
                        "entry_price": p.entry_price,
                        "entry_ts": p.entry_ts,
                        "current_price": cur,
                        "unrealized_pnl": round(pos_unreal, 2),
                    })
            except Exception as e:
                logger.debug(f"account positions failed: {e}")
        conn = db.conn()
        try:
            nv_rows = conn.execute(
                "SELECT ts, cash, equity, total FROM net_values "
                "ORDER BY ts ASC LIMIT 500").fetchall()
            lt = time.localtime()
            today_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                       0, 0, 0, 0, 0, -1))
            today_pnl = 0.0
            for (pnl,) in conn.execute(
                    "SELECT pnl FROM trades WHERE exit_ts >= ? AND pnl IS NOT NULL",
                    (today_start,)).fetchall():
                today_pnl += float(pnl or 0.0)
        finally:
            conn.close()
        # latest_net_value：实时快照（现金 + 持仓 MTM），不再返回日终落库的
        # 陈旧行——总览「总资产」等卡片读它，落后于盘中平仓/买入会显示失真
        # （2026-08-19 修复）。持仓无实时价（行情降级 fail-closed）时按成本计。
        live_cash = round(ledger.cash, 2) if ledger is not None else 0.0
        live_equity = 0.0
        for p in positions:
            px = p.get("current_price") if p.get("current_price") is not None \
                else (p.get("entry_price") or 0.0)
            live_equity += float(px) * float(p.get("quantity") or 0)
        latest_nv = {
            "ts": time.time(),
            "cash": live_cash,
            "equity": round(live_equity, 2),
            "total": round(live_cash + live_equity, 2),
        }
        return web.json_response({
            "cash": round(ledger.cash, 2) if ledger is not None else None,
            "positions": positions,
            "open_position_value": round(open_value, 2),
            "unrealized_pnl": round(unrealized, 2),
            "latest_net_value": latest_nv,
            "net_values": [dict(r) for r in nv_rows],
            "today_pnl": round(today_pnl, 2),
            "ts": time.time(),
        })
    except Exception as e:
        logger.warning(f"quant_account failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_backtest(request):
    """POST /v1/quant/backtest — 单标的同步回测（薄封装 BacktestRunner，A股成本口径）。

    body: {strategy: str (模板名，缺省 multi_factor),
           symbol: str (缺省 600519), days: int (K线天数, 缺省 200),
           params: dict (可选，覆盖 STRATEGY_PARAMS), costs: dict (可选，显式 {} 零成本)}
    直接调 BacktestRunner.run_backtest_values（默认 DEFAULT_COSTS 含费），
    不重实现口径；诚实负结果照实返回。

    Returns: {metrics: {score,cumulative_return,sharpe_ratio,max_drawdown},
              net_values: [{ts,cash,equity,total}], symbol, strategy, days,
              quality, count}
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    strategy = str(body.get("strategy") or "multi_factor")
    symbol = str(body.get("symbol") or "600519")
    days = int(body.get("days") or 200)
    params = body.get("params")
    costs = body.get("costs")
    try:
        from laap.paper_trading.backtest_runner import BacktestRunner
        from laap.paper_trading.kline_source import load_ohlcv
        from laap.paper_trading.strategy import STRATEGY_PARAMS

        ohlcv, quality = load_ohlcv(symbol, days=days, with_quality=True)
        if not ohlcv:
            return web.json_response({"error": f"no kline for {symbol}"}, status=400)
        closes = [float(r[1]) for r in ohlcv]
        runner = BacktestRunner()
        p = params if isinstance(params, dict) and params else dict(STRATEGY_PARAMS)
        # style: multi_factor→trend；模板按名称路由（策略模板已并入 evaluate_signal）
        style = "trend"
        metrics, net_values = runner.run_backtest_values(
            closes, params=p, ohlcv=ohlcv, costs=costs, style=style)
        nv_list = [dict(nv.to_dict()) for nv in net_values]
        # 单标的回测结果落库（报告页可查详情，与批量同口径）
        _store_backtest_run(sym=symbol, strategy=strategy, days=days,
                            run_type="single", params=p, metrics=metrics,
                            quality=quality, net_values=nv_list, ts=time.time())
        return web.json_response({
            "metrics": metrics,
            "net_values": nv_list,
            "symbol": symbol, "strategy": strategy, "days": days,
            "quality": quality, "count": len(closes),
            "ts": time.time(),
        })
    except Exception as e:
        logger.warning(f"quant_backtest failed: {e}")
        return web.json_response({"error": str(e)}, status=500)


def _run_quant_backtest_once(symbol: str, strategy: str = "multi_factor",
                             days: int = 200, params: Any = None,
                             costs: Any = None) -> tuple:
    """同步跑单标的回测（口径与 /v1/quant/backtest 完全一致：BacktestRunner
    默认 DEFAULT_COSTS 含费、bar i+1 开盘成交、诚实负结果）。

    Returns: (ok, result) — ok=True → result 成功 payload dict；
             ok=False → result 为 {error: str, status_code: int}。
    """
    try:
        from laap.paper_trading.backtest_runner import BacktestRunner
        from laap.paper_trading.kline_source import load_ohlcv
        from laap.paper_trading.strategy import STRATEGY_PARAMS
        ohlcv, quality = load_ohlcv(symbol, days=days, with_quality=True)
        if not ohlcv:
            return False, {"error": f"no kline for {symbol}", "status_code": 400}
        closes = [float(r[1]) for r in ohlcv]
        runner = BacktestRunner()
        p = params if isinstance(params, dict) and params else dict(STRATEGY_PARAMS)
        metrics, net_values = runner.run_backtest_values(
            closes, params=p, ohlcv=ohlcv, costs=costs, style="trend")
        return True, {
            "metrics": metrics,
            "net_values": [dict(nv.to_dict()) for nv in net_values],
            "symbol": symbol, "strategy": strategy, "days": days,
            "quality": quality, "count": len(closes),
        }
    except Exception as e:
        logger.warning(f"quant_backtest once failed ({symbol}): {e}")
        return False, {"error": str(e), "status_code": 500}


def _store_backtest_run(sym: str, strategy: str, days: int, run_type: str,
                        params: Any, metrics: Any, quality: Any,
                        net_values: Any = None, ts: float = 0.0) -> None:
    """落 backtest_runs 表（幂等；表缺失/写失败静默跳过，fail-closed）。"""
    try:
        import uuid as _uuid
        import json as _json
        db = _get_quant_db()
        if db is None:
            return
        conn = db.conn()
        try:
            conn.execute(
                "INSERT INTO backtest_runs (id, ts, symbol, strategy, days, run_type,"
                " params_json, metrics_json, quality_json, net_values_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (_uuid.uuid4().hex, ts or time.time(), sym, strategy, days, run_type,
                 _json.dumps(params or {}, ensure_ascii=False),
                 _json.dumps(metrics or {}, ensure_ascii=False),
                 _json.dumps(quality or {}, ensure_ascii=False),
                 _json.dumps(net_values or [], ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"backtest run store skipped: {e}")


async def handle_quant_backtest_batch(request):
    """POST /v1/quant/backtest/batch — 批量回测（≤10 标的，同步，同口径，P4）。

    body: {symbols: [str], strategy?, days?, params?, costs?}
    - 逐标的调 _run_quant_backtest_once（与单标的口径 100% 一致，默认含费）
    - 单标的失败落 runs[].error，不中断整批；每成功 run 落 backtest_runs 表
    Returns: {runs: [{symbol, ok, metrics?, net_values_count?, quality?, error?}],
              aggregate: {total, ok, best_symbol, worst_symbol,
                          median_cumulative_return, positive_count}, ts}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    body = body or {}
    symbols = body.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        return web.json_response({"error": "symbols list required"}, status=400)
    if len(symbols) > 10:
        return web.json_response({"error": "symbols max 10"}, status=400)
    strategy = str(body.get("strategy") or "multi_factor")
    days = int(body.get("days") or 200)
    params = body.get("params")
    costs = body.get("costs")
    ts_now = time.time()
    runs = []
    for sym in symbols:
        s = str(sym).strip()
        ok, result = _run_quant_backtest_once(s, strategy, days, params, costs)
        if ok:
            runs.append({
                "symbol": result["symbol"], "ok": True,
                "metrics": result["metrics"],
                "net_values_count": len(result["net_values"]),
                "quality": result["quality"],
            })
            _store_backtest_run(sym=result["symbol"], strategy=strategy, days=days,
                                run_type="batch", params=params,
                                metrics=result["metrics"], quality=result["quality"],
                                net_values=result["net_values"], ts=ts_now)
        else:
            runs.append({"symbol": s, "ok": False, "error": result["error"]})
    # 聚合（诚实：仅统计 ok 的 run）
    ok_runs = [r for r in runs if r.get("ok") and r.get("metrics")]
    agg: Dict[str, Any] = {"total": len(runs), "ok": len(ok_runs),
                           "best_symbol": "", "worst_symbol": "",
                           "median_cumulative_return": None, "positive_count": 0}
    if ok_runs:
        rets = sorted(ok_runs,
                      key=lambda r: (r["metrics"].get("cumulative_return") or 0))
        agg["worst_symbol"] = rets[0]["symbol"]
        agg["best_symbol"] = rets[-1]["symbol"]
        agg["positive_count"] = sum(
            1 for r in ok_runs if (r["metrics"].get("cumulative_return") or 0) > 0)
        vals = sorted(r["metrics"].get("cumulative_return") or 0 for r in ok_runs)
        n = len(vals)
        agg["median_cumulative_return"] = (vals[n // 2] if n % 2
                                           else (vals[n // 2 - 1] + vals[n // 2]) / 2)
    return web.json_response({"runs": runs, "aggregate": agg, "ts": ts_now})


async def handle_quant_backtest_reports(request):
    """GET /v1/quant/backtest/reports?page=&page_size= — 回测报告列表（分页，时间倒序）。"""
    try:
        import json as _json
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        try:
            page = max(1, int(request.query.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = max(1, min(int(request.query.get("page_size") or 15), 100))
        except (TypeError, ValueError):
            page_size = 15
        offset = (page - 1) * page_size
        conn = db.conn()
        try:
            total = conn.execute("SELECT COUNT(*) c FROM backtest_runs").fetchone()["c"]
            rows = conn.execute(
                "SELECT id, ts, symbol, strategy, days, run_type, metrics_json"
                " FROM backtest_runs ORDER BY ts DESC LIMIT ? OFFSET ?",
                (page_size, offset)).fetchall()
        finally:
            conn.close()
        reports = []
        for r in rows:
            try:
                m = _json.loads(r["metrics_json"] or "{}")
            except Exception:
                m = {}
            reports.append({
                "id": r["id"], "ts": r["ts"], "symbol": r["symbol"],
                "strategy": r["strategy"], "days": r["days"], "run_type": r["run_type"],
                "score": m.get("score"), "cumulative_return": m.get("cumulative_return"),
                "sharpe_ratio": m.get("sharpe_ratio"),
                "max_drawdown": m.get("max_drawdown"),
            })
        return web.json_response({
            "reports": reports, "count": len(reports),
            "total": total, "page": page, "page_size": page_size,
        })
    except Exception as e:
        logger.warning(f"quant_backtest_reports failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_backtest_report(request):
    """GET /v1/quant/backtest/report/{id} — 回测报告详情（含净值序列）。"""
    rid = request.match_info.get("id", "")
    if not rid:
        return web.json_response({"error": "id required"}, status=400)
    try:
        import json as _json
        db = _get_quant_db()
        if db is None:
            return web.json_response({"error": "internal error"}, status=500)
        conn = db.conn()
        try:
            row = conn.execute(
                "SELECT * FROM backtest_runs WHERE id=?", (rid,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return web.json_response({"error": f"report {rid} not found"}, status=404)

        def _j(s: str, default: Any):
            try:
                return _json.loads(s or "")
            except Exception:
                return default

        return web.json_response({
            "id": row["id"], "ts": row["ts"], "symbol": row["symbol"],
            "strategy": row["strategy"], "days": row["days"], "run_type": row["run_type"],
            "params": _j(row["params_json"], {}),
            "metrics": _j(row["metrics_json"], {}),
            "quality": _j(row["quality_json"], {}),
            "net_values": _j(row["net_values_json"], []),
        })
    except Exception as e:
        logger.warning(f"quant_backtest_report failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


# ── P5 实盘下盘（占位）：不实现真实券商路由，fail-closed 诚实标记 ──
_LIVE_BROKERS = [
    {"id": "simnow", "name": "上期 SIMNOW"},
    {"id": "qmt", "name": "迅投 QMT"},
    {"id": "ths", "name": "同花顺"},
    {"id": "custom", "name": "自定义/其他券商"},
]


async def handle_quant_live_status(request):
    """GET /v1/quant/live/status — 实盘通道状态（占位：恒未接入）。

    占位契约：live_enabled 恒 false，直到真实实现上线；前端看到 false
    才能展示「未接入」灰态，严禁前端假装可交易。
    """
    try:
        return web.json_response({
            "mode": "placeholder",
            "live_enabled": False,
            "paper_mode_active": True,
            "brokers": [
                {"id": b["id"], "name": b["name"], "connected": False,
                 "note": "未接入（占位）"}
                for b in _LIVE_BROKERS
            ],
            "ts": time.time(),
        })
    except Exception as e:
        logger.warning(f"quant_live_status failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_live_connect(request):
    """POST /v1/quant/live/broker/connect — 券商对接（占位：恒 501）。

    body: {broker_id: str}。不建连接、不落库、不引入券商 SDK；
    恒返回 501 not_implemented（fail-closed：未实现就如实说未实现）。
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    broker_id = str((body or {}).get("broker_id") or "")
    known = any(b["id"] == broker_id for b in _LIVE_BROKERS)
    return web.json_response({
        "status": "not_implemented",
        "message": ("实盘对接占位：本批次不实现真实券商路由（fail-closed）。"
                    "当前所有订单仍走 paper trading。"),
        "broker_id": broker_id,
        "broker_known": known,
        "connected": False,
    }, status=501)


async def handle_quant_decide(request):
    """POST /v1/quant/decide — 交易决策建议（审核，不下单）。

    body: {symbol, action: buy|sell, qty?: int, rationale?: str}
    薄封装 quant_bridge.use_decide（记忆注入 + TradingSelf.judge 审核），
    fail-closed：只返回建议（executed=False），不产生订单。
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    body = body or {}
    symbol = str(body.get("symbol") or "").strip()
    action = str(body.get("action") or "").strip()
    if not symbol or action not in ("buy", "sell"):
        return web.json_response({"error": "symbol + action(buy|sell) required"},
                                 status=400)
    try:
        from laap.paper_trading.quant_bridge import get_bridge
        result = get_bridge().use_decide(
            symbol=symbol, action=action,
            qty=int(body.get("qty") or 0),
            rationale=str(body.get("rationale") or ""))
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_decide failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_quant_order(request):
    """POST /v1/quant/order — 手动下单（薄封装 quant_bridge.use_execute，fail-closed）。

    body: {symbol, action: buy|sell, qty: int, confirm_word: str,
           decision_id?: str, rationale?: str}
    安全语义全部保留（不绕过）：
      交易日门 → 确认词门（PAPER_TRADING_AUTO_EXECUTE=0 时强制二次确认）
      → 行情降级拒绝（used_fallback → market_fallback）→ TradingSelf.judge
      审核（非 approve → judge_blocked）→ issue 执行。
    Returns: {executed, status, ...}（后端原样透传 use_execute 结果）。
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    body = body or {}
    symbol = str(body.get("symbol") or "").strip()
    action = str(body.get("action") or "").strip()
    qty = body.get("qty")
    if not symbol or action not in ("buy", "sell"):
        return web.json_response({"error": "symbol + action(buy|sell) required"},
                                 status=400)
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        return web.json_response({"error": f"qty invalid: {qty!r}"}, status=400)
    try:
        from laap.paper_trading.quant_bridge import get_bridge
        result = get_bridge().use_execute(
            decision_id=str(body.get("decision_id") or ""),
            symbol=symbol, action=action, qty=qty,
            confirm_word=str(body.get("confirm_word") or ""))
        return web.json_response(result)
    except Exception as e:
        logger.warning(f"quant_order failed: {e}")
        return web.json_response({"error": "internal error"}, status=500)


async def handle_root(request):
    return web.json_response({
        "name": "LAAP Brain API",
        "version": "1.0.0",
        "endpoints": {
            "/": "This info",
            "/v1/models": "List available models",
            "/v1/chat/completions": "Chat completions (OpenAI-compatible)",
            "/v1/cognitive_state": "Get PSI cognitive state",
            "/v1/recall_memory": "Recall LAAP memories",
            "/v1/reflect": "Reflect on completed turn",
            "/v1/express": "Map cognitive state to expression params",
            "/v1/bootstrap": "Awaken a new LAAP instance",
            "/v1/personality": "GET/SET personality",
            "/v1/bond": "Get attachment/bond status",
            "/v1/rsi_status": "RSI self-improvement status",
            "/v1/rsi_improve": "Apply RSI improvement",
            "/v1/rsi_full_cycle": "Run full RSI cycle",
            "/v1/evo/audit": "Query evolution audit log",
            "/v1/evo/status": "Evolution scheduler status",
            "/v1/evo/rollback": "Rollback last evolution",
            "/v1/evo/deploy": "Approve & deploy evolution mutation",
            "/v1/quant/trades": "Paper trades",
            "/v1/quant/net_values": "Paper net value series",
            "/v1/quant/signals": "Paper signals",
            "/v1/quant/orders": "Paper orders",
            "/v1/quant/outcomes": "Paper outcomes/lessons",
            "/v1/quant/decisions": "GET query / POST record paper decisions",
            "/v1/quant/lessons": "Query lessons (optionally by lesson_type)",
            "/v1/quant/self/status": "TradingSelf status",
            "/v1/quant/daily_cycle": "Run daily paper cycle",
            "/v1/quant/apply_params": "Apply evolved params to code (governed)",
            "/v1/quant/params/apply": "Runtime-apply params (env live, no code rewrite)",
            "/v1/quant/evolve": "Run code-level evolution proposal",
            "/v1/quant/evolve_params": "Parameter evolution (optionally LLM-refined)",
            "/v1/quant/evolve/approve": "Approve evolution proposal",
            "/v1/quant/evolve/reject": "Reject & rollback evolution",
            "/v1/quant/evolve/audit": "Query evolution audit",
            "/v1/quant/kline": "Query kline (symbol+days)",
            "/health": "Health check",
        },
        "frameworks": [
            "Hermes Agent: set api_base to http://localhost:11546/v1",
            "OpenClaw: set custom LLM endpoint to http://localhost:11546/v1",
            "OpenCode: set api_base to http://localhost:11546/v1",
        ],
    })


# ── 启动 ─────────────────────────────────────────────────────


def _cors_origin_allowed(origin: str) -> bool:
    """是否放行该跨域来源。

    默认仅本机（localhost / 127.0.0.1 任意端口，覆盖隐藏前端 15173）；
    可用 env LAAP_CORS_ORIGINS（逗号分隔精确列表）覆盖。
    """
    if not origin:
        return False
    env = os.environ.get("LAAP_CORS_ORIGINS", "").strip()
    if env:
        return origin in [o.strip() for o in env.split(",") if o.strip()]
    from urllib.parse import urlparse
    return (urlparse(origin).hostname or "") in ("localhost", "127.0.0.1")


@web.middleware
async def cors_middleware(request, handler):
    """跨域支持（隐藏前端 15173 → 后端 11546，2026-08-19）。

    前端带 `Authorization` 头请求快照 → 浏览器先发 OPTIONS 预检（非简单请求）。
    预检直接 200 + CORS 头返回（不经 auth_middleware，预检不带凭证）；
    普通请求在响应上附加 CORS 头。仅放行本机来源（默认），不开放任意站点。
    """
    origin = request.headers.get("Origin", "")
    allow = _cors_origin_allowed(origin)
    if request.method == "OPTIONS":
        resp = web.Response(status=200)
    else:
        resp = await handler(request)
    if allow:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, X-Requested-With")
        resp.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, DELETE, OPTIONS")
        resp.headers["Access-Control-Max-Age"] = "3600"
    return resp


@web.middleware
async def auth_middleware(request, handler):
    """可选 API Key 校验 (R7).

    配置 LAAP_API_KEY 后, 除 / 与 /health 外的所有端点均需
    `Authorization: Bearer <LAAP_API_KEY>`; 未配置时保持兼容 (默认仅本机可达)。
    """
    key = os.environ.get("LAAP_API_KEY", "")
    if not key:
        return await handler(request)
    # / 和 /health 免鉴权；WS 端点走自定义鉴权（upgrade 前无法发 header）
    if request.path in ("/", "/health", "/v1/quant/events/ws",
                        "/v1/quant/dashboard/init",
                        "/v1/quant/config", "/v1/quant/account",
                        "/v1/quant/memory/status",
                        "/v1/quant/memory/runtime",
                        "/v1/quant/memory/meta",
                        "/v1/quant/live/status",
                        "/v1/quant/system/logs",
                        "/v1/quant/system/scan"):
        return await handler(request)
    auth = request.headers.get("Authorization", "")
    if auth == f"Bearer {key}":
        return await handler(request)
    return web.json_response({"error": "unauthorized"}, status=401)


def create_app() -> web.Application:
    """创建 LAAP Brain API 应用。"""
    app = web.Application(middlewares=[cors_middleware, auth_middleware])
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/v1/models", handle_models)
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    app.router.add_post("/v1/cognitive_state", handle_cognitive_state)
    app.router.add_post("/v1/recall_memory", handle_recall_memory)
    app.router.add_post("/v1/reflect", handle_reflect)
    app.router.add_post("/v1/express", handle_express)
    app.router.add_post("/v1/bootstrap", handle_bootstrap)
    app.router.add_get("/v1/personality", handle_get_personality)
    app.router.add_post("/v1/personality", handle_set_personality)
    app.router.add_get("/v1/bond", handle_get_bond)
    app.router.add_post("/v1/rsi_status", handle_rsi_status)
    app.router.add_post("/v1/rsi_improve", handle_rsi_improve)
    app.router.add_post("/v1/rsi_full_cycle", handle_rsi_full_cycle)
    # M3 治理端点
    app.router.add_get("/v1/evo/audit", handle_evo_audit)
    app.router.add_get("/v1/evo/status", handle_evo_status)
    app.router.add_post("/v1/evo/rollback", handle_evo_rollback)
    app.router.add_post("/v1/evo/deploy", handle_evo_deploy)
    # 量化闭环端点 (paper_trading)
    app.router.add_post("/v1/quant/decisions", handle_quant_decision_record)
    app.router.add_get("/v1/quant/lessons", handle_quant_lessons)
    app.router.add_get("/v1/quant/self/status", handle_quant_self_status)
    app.router.add_get("/v1/quant/personality", handle_quant_personality_get)
    app.router.add_post("/v1/quant/personality", handle_quant_personality_set)
    app.router.add_get("/v1/quant/memory/status", handle_quant_memory_status)
    app.router.add_get("/v1/quant/memory/runtime", handle_quant_memory_runtime)
    app.router.add_get("/v1/quant/memory/archive", handle_quant_memory_archive)
    app.router.add_post("/v1/quant/memory/archive", handle_quant_memory_archive_create)
    app.router.add_put("/v1/quant/memory/archive/{kind}/{id}", handle_quant_memory_archive_update)
    app.router.add_delete("/v1/quant/memory/archive/{kind}/{id}", handle_quant_memory_archive_delete)
    app.router.add_get("/v1/quant/watchlist", handle_quant_watchlist_get)
    app.router.add_post("/v1/quant/watchlist", handle_quant_watchlist_add)
    app.router.add_delete("/v1/quant/watchlist/{symbol}", handle_quant_watchlist_remove)
    app.router.add_get("/v1/quant/market/snapshot", handle_quant_market_snapshot)
    app.router.add_post("/v1/quant/policy/analyze", handle_quant_policy_analyze)
    app.router.add_post("/v1/quant/policy/upload", handle_quant_policy_upload)
    app.router.add_get("/v1/quant/policy/picks", handle_quant_policy_picks_get)
    app.router.add_get("/v1/quant/policy/picks/match", handle_quant_policy_picks_match)
    app.router.add_get("/v1/quant/policy/candidates", handle_quant_policy_candidates)
    app.router.add_get("/v1/quant/policy/batches", handle_quant_policy_batches)
    app.router.add_post("/v1/quant/policy/picks", handle_quant_policy_picks_add)
    app.router.add_delete("/v1/quant/policy/picks/{id}", handle_quant_policy_picks_remove)
    app.router.add_post("/v1/quant/kline/collect", handle_quant_kline_collect)
    app.router.add_get("/v1/quant/kline/status", handle_quant_kline_status)
    app.router.add_get("/v1/quant/kline/schedule", handle_quant_kline_schedule_get)
    app.router.add_post("/v1/quant/kline/schedule", handle_quant_kline_schedule_set)
    app.router.add_get("/v1/quant/memory/meta", handle_quant_memory_meta)
    app.router.add_get("/v1/quant/strategies", handle_quant_strategies)
    app.router.add_get("/v1/quant/events/status", handle_quant_events_status)
    app.router.add_get("/v1/quant/system/logs", handle_quant_system_logs)
    app.router.add_post("/v1/quant/system/scan", handle_quant_system_scan)
    app.router.add_get("/v1/quant/dashboard/init", handle_quant_dashboard_init)
    app.router.add_get("/v1/quant/events/ws", handle_quant_events_ws)
    app.router.add_post("/v1/quant/daily_cycle", handle_quant_daily_cycle)
    app.router.add_post("/v1/quant/apply_params", handle_quant_apply_params)
    app.router.add_post("/v1/quant/evolve", handle_quant_evolve)
    app.router.add_post("/v1/quant/evolve_params", handle_quant_evolve_params)
    app.router.add_post("/v1/quant/evolve/approve", handle_quant_evolve_approve)
    app.router.add_post("/v1/quant/evolve/reject", handle_quant_evolve_reject)
    app.router.add_get("/v1/quant/evolve/audit", handle_quant_evolve_audit)
    app.router.add_get("/v1/quant/trades", handle_quant_trades)
    app.router.add_get("/v1/quant/net_values", handle_quant_net_values)
    app.router.add_get("/v1/quant/signals", handle_quant_signals)
    app.router.add_get("/v1/quant/orders", handle_quant_orders)
    app.router.add_get("/v1/quant/outcomes", handle_quant_outcomes)
    app.router.add_get("/v1/quant/scheduler/stats", handle_quant_scheduler_stats)
    app.router.add_get("/v1/quant/decisions", handle_quant_decisions)
    app.router.add_get("/v1/quant/kline", handle_quant_kline)
    # 新闻情报闭环（P4）
    app.router.add_get("/v1/quant/news", handle_quant_news)
    app.router.add_get("/v1/quant/profile", handle_quant_profile)
    app.router.add_post("/v1/quant/news/verify", handle_quant_news_verify)
    app.router.add_post("/v1/quant/news/scan", handle_quant_news_scan)
    app.router.add_get("/v1/quant/risk/rejections", handle_quant_risk_rejections)
    app.router.add_get("/v1/quant/config", handle_quant_config)
    app.router.add_get("/v1/quant/account", handle_quant_account)
    app.router.add_post("/v1/quant/params/apply", handle_quant_params_apply)
    app.router.add_post("/v1/quant/backtest", handle_quant_backtest)
    app.router.add_post("/v1/quant/backtest/batch", handle_quant_backtest_batch)
    app.router.add_get("/v1/quant/backtest/reports", handle_quant_backtest_reports)
    app.router.add_get("/v1/quant/backtest/report/{id}", handle_quant_backtest_report)
    app.router.add_get("/v1/quant/live/status", handle_quant_live_status)
    app.router.add_post("/v1/quant/live/broker/connect", handle_quant_live_connect)
    app.router.add_post("/v1/quant/decide", handle_quant_decide)
    app.router.add_post("/v1/quant/order", handle_quant_order)
    return app


# ── 隐藏前端（量化控制台 dashboard/，端口 15173）──────────────
# 2026-08-19: dashboard/ 静态站点随主应用同启同停；端口由 DASHBOARD_PORT
# 配置（默认 15173，.env 已设），置 0/空 可禁用；目录缺失或端口冲突仅告警，
# 不拖垮主服务（fail-soft）。

_DASHBOARD_DIR = LAAP_ROOT / "dashboard"


def _dashboard_port() -> int:
    """隐藏前端端口（DASHBOARD_PORT，默认 15173）；≤0 表示不启动。"""
    raw = (os.environ.get("DASHBOARD_PORT") or "15173").strip()
    try:
        return int(raw)
    except ValueError:
        logger.warning("DASHBOARD_PORT=%r 非整数，隐藏前端不启动", raw)
        return 0


def _frontend_config_script(api_port: int) -> str:
    """向前端注入后端地址与 API Key（读 env 单源，不写死；浏览器侧运行）。

    前端 api.js / ws.js 在加载时读取 window.LAAP_API_BASE / LAAP_API_KEY，
    注入在 </head> 之前，保证先于所有面板脚本执行。API Key 会暴露到浏览器侧
    （本地/内网前端专用，等同 .env 同机可读），不跨主机发送。
    """
    key = os.environ.get("LAAP_API_KEY", "").strip()
    # JS 字符串转义（防引号/换行/</script> 注入）
    key_js = (key.replace("\\", "\\\\").replace('"', '\\"')
              .replace("\n", "\\n").replace("<", "\\u003c"))
    return (
        "<script>\n"
        "window.LAAP_API_BASE = 'http://' + (window.location.hostname || '127.0.0.1')"
        f" + ':{api_port}';\n"
        f"window.LAAP_API_KEY = \"{key_js}\";\n"
        "</script>"
    )


def _create_dashboard_app(api_port: int) -> web.Application:
    """量化控制台静态站点应用：/ → index.html（注入后端配置），/static → 静态资源。"""
    app = web.Application()
    index = _DASHBOARD_DIR / "index.html"
    if index.is_file():
        # 启动时读一次并注入配置（运行时改前端文件需重启生效，静态站点惯例）
        index_html = index.read_text(encoding="utf-8")
        html = index_html.replace(
            "</head>", _frontend_config_script(api_port) + "</head>", 1)

        async def _index(request: web.Request) -> web.Response:
            return web.Response(text=html, content_type="text/html", charset="utf-8")

        app.router.add_get("/", _index)
        app.router.add_get("/index.html", _index)
    static_dir = _DASHBOARD_DIR / "static"
    if static_dir.is_dir():
        app.router.add_static("/static/", static_dir, show_index=False)
    return app


def _attach_dashboard(main_app: web.Application, host: str, api_port: int) -> None:
    """主服务启动/停止时同启同停隐藏前端（15173 量化控制台，fail-soft）。"""
    port = _dashboard_port()
    if port <= 0:
        logger.info("隐藏前端 dashboard/ 未启动（DASHBOARD_PORT=%s）", port)
        return
    dash_app = _create_dashboard_app(api_port)
    if not dash_app.router.routes():
        logger.warning("隐藏前端 dashboard/ 目录缺失，跳过 %s 服务", port)
        return

    async def _start_dashboard(_app: web.Application) -> None:
        runner = web.AppRunner(dash_app, access_log=None)
        try:
            await runner.setup()
            site = web.TCPSite(runner, host, port)
            await site.start()
            _app["dashboard_runner"] = runner
            logger.info("隐藏前端 dashboard/ 已启动: http://%s:%s/", host, port)
        except Exception as exc:  # 端口冲突等 → 仅告警，不拖垮主服务
            logger.warning("隐藏前端 %s 启动失败（主服务不受影响）: %s", port, exc)
            await runner.cleanup()

    async def _stop_dashboard(_app: web.Application) -> None:
        runner = _app.get("dashboard_runner")
        if runner is not None:
            await runner.cleanup()

    main_app.on_startup.append(_start_dashboard)
    main_app.on_shutdown.append(_stop_dashboard)


def main():
    # 统一端口约定: 默认 11546 (与 Docker / README / .env.example / MCP 客户端一致)
    port = int(os.environ.get("LAAP_PORT", "11546"))
    # 2026-08-16 调试: 打印解释器信息到日志 (统一 venv 环境排查用, 后续可移除)
    logger.info("LAAP main() interpreter: sys.executable=%s", sys.executable)
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])

    # 绑定地址: --host 参数 > LAAP_HOST 环境变量 > 默认 127.0.0.1 (安全默认, 仅本机可达)
    # 需要局域网/公网访问时显式设 LAAP_HOST=0.0.0.0 (无认证 API 不应默认暴露给同网段设备)
    host = os.environ.get("LAAP_HOST", "127.0.0.1")
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),  # stderr → nssm 重定向（logs/laap-error.log）
        ],
    )
    # 日志文件按 logs/年/月/年月日.txt 归档（2026-08-18：目录不存在则自动创建）
    try:
        _log_dir = LAAP_ROOT / "logs" / datetime.now().strftime("%Y") \
            / datetime.now().strftime("%m")
        _log_dir.mkdir(parents=True, exist_ok=True)
        _log_file = _log_dir / f"{datetime.now().strftime('%Y%m%d')}.txt"
        _fh = logging.FileHandler(_log_file, encoding="utf-8")
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logging.getLogger().addHandler(_fh)
        logger.info("日志归档至 %s", _log_file)
    except Exception as _le:
        logger.warning("日志文件归档初始化失败: %s", _le)

    # Pre-warm LAAP engine
    logger.info("Pre-warming LAAP cognitive engines...")
    get_integrator()

    # M2 True RSI: 启动代码进化调度器 (LAAP_EVO_ENABLED=1 时)
    _start_evolution_scheduler()
    # M3 量化: 每日管线调度器 (LAAP_QUANT_DAILY=1 时)
    _start_quant_daily_scheduler()
    # 新闻盘中轮询 (LAAP_NEWS_INTRADAY=1 时)
    _start_news_worker()
    # K 线定时采集线程（enabled 时每日到点采集，数据源 tab 管理）
    _start_kline_scheduler()
    # 事件驱动编排器 (LAAP_EVENT_DRIVEN=1 时, 2026-08-17)
    _start_event_orchestrator()

    app = create_app()
    # 隐藏前端（15173 量化控制台）随主服务同启同停
    _attach_dashboard(app, host, port)
    logger.info(f"LAAP Brain API starting on {host}:{port}")
    logger.info(f"OpenAI-compatible endpoint: http://localhost:{port}/v1")
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()