"""
LAAP AGI Tool Router — AGI 认知层工具调用决策
===============================================

Zero-LLM 认知引擎的工具路由核心，隶属于 laap/agi（AGI 认知层）：
通过「股票实体锚点 + 中英双语意图域映射」把用户消息路由到工具，
并融合 AGI 认知状态（PSI 情感 / 自信度 / 需求 / 语义记忆）调整决策阈值。

与 laap_brain.tools 的关系：
  - 本模块是**唯一实现**（匹配、参数提取、tool_calls 生成全部在此）；
  - laap_brain/tools.py 只是向后兼容的 re-export 层（API 层入口不变）。

设计原则：
  - 宁可不匹配（返回普通文本），也不要误匹配（发错误的工具调用）。
  - 基础分：实体锚点 +2；意图域命中（strong 域 ×3 / 普通域 ×2，乘工具域权重）；
    显式点名工具名 +5；价格意图 + 报价类工具 +2。
  - 认知阈值：默认 3 分触发；PSI 情感负面 / 自信度低 → 阈值提高（更保守）；
    competence 需求高 / 记忆支持 → 阈值降低（更主动）。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ── 意图域词典（中英双语）──────────────────────────────────────
_INTENT_DOMAINS: Dict[str, tuple] = {
    "quote": (
        "price", "quote", "realtime", "行情", "价格", "股价", "现价",
        "报价", "多少钱", "涨跌", "涨了", "跌了", "盘口", "实时", "市盈率",
    ),
    "ohlcv": (
        "daily", "ohlcv", "kline", "candle", "k线", "日线", "周线",
        "月线", "蜡烛图",
    ),
    "history": ("history", "historical", "历史", "走势数据", "行情数据"),
    "chip": ("chip", "筹码", "获利盘", "分布"),
    "context": ("context", "分析上下文", "历史分析", "之前的分析"),
    "info": (
        "info", "fundamental", "公司资料", "基本面", "资料", "介绍", "估值",
        "总市值", "股本",
    ),
    "portfolio": ("portfolio", "position", "持仓", "组合", "仓位", "持有", "我的股票"),
    "capital": ("capital", "flow", "资金", "流入", "流出", "主力", "北向", "资金流向"),
    "trend": (
        "trend", "technical", "macd", "rsi", "趋势", "技术面", "技术分析",
        "支撑", "压力", "金叉", "死叉", "买卖信号", "信号",
    ),
    "ma_calc": ("moving", "average", "periods", "均线", "ma计算", "计算均线"),
    "ma_ind": ("ma5", "ma10", "ma20", "alignment", "indicators"),
    "volume": ("volume", "量能", "成交量", "换手", "量比", "放量", "缩量"),
    "pattern": ("pattern", "形态", "k线形态", "图形", "头肩", "双底", "双顶", "突破"),
    "news": ("news", "资讯", "新闻", "公告", "消息", "报道"),
    "intel": ("intel", "intelligence", "情报", "综合", "多维", "深度调研"),
    "backtest": ("backtest", "strategy", "回测", "策略", "绩效", "收益曲线", "夏普"),
    "indices": ("index", "indices", "shanghai", "shenzhen", "composite", "大盘", "指数", "行情", "市场"),
    "sector": ("sector", "industry", "板块", "行业", "行业排名", "排名"),
    "watchlist": ("watchlist", "自选股", "自选", "我的股票", "持仓列表", "股票列表"),
    "paper": ("paper", "papers", "arxiv", "thesis", "research", "survey", "review", "literature", "文献", "论文", "学术", "研究", "综述", "consciousness", "cognition", "cognitive", "mind", "brain", "neural", "意识", "认知", "心智", "大脑", "神经"),
}

# 实体无关 / 意图明确的域：命中即 +3（否则 +2）
_STRONG_DOMAINS = frozenset(
    {"ohlcv", "indices", "sector", "news", "backtest", "intel", "portfolio", "trend", "paper"}
)

_PRICE_INTENT_WORDS = frozenset(
    "price quote 价格 股价 现价 报价 多少钱 值多少 行情 涨跌 涨了 跌了".split()
)

# PSI 情感 → 决策阈值修正
_EMOTION_THRESHOLD_DELTA = {
    "anxiety": +1, "confusion": +1, "fear": +1, "sadness": +1, "tired": +1,
    "neutral": 0, "calm": 0, "joy": 0, "interest": 0, "positive": 0,
}


def tokenize(text: str) -> set:
    """CJK 单字 + 双字 + ASCII 词 + 中英混合词（如 k线）。"""
    text = (text or "").lower()
    mixed = re.findall(r"[a-z0-9]{1,4}[\u4e00-\u9fff]{1,4}", text)
    chars = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    words = re.findall(r"[a-z0-9]{2,}", text)
    return set(chars + bigrams + words + mixed)


def _ascii_words(text: str) -> set:
    words = set(re.findall(r"[a-z0-9_]{2,}", (text or "").lower()))
    extra = {w[:-1] for w in words if len(w) > 3 and w.endswith("s") and not w.endswith("ss")}
    return words | extra


def _domain_hits(tokens: set) -> set:
    return {d for d, words in _INTENT_DOMAINS.items() if tokens & set(words)}


def _tool_domain_weights(tool: Dict) -> Dict[str, int]:
    """工具命中的意图域及权重（描述命中 + 2×名字命中，封顶 3）。"""
    fn = tool.get("function", {})
    name = fn.get("name", "")
    desc = fn.get("description", "")
    name_tokens = tokenize(name) | _ascii_words(name)
    desc_tokens = tokenize(desc) | _ascii_words(desc)
    weights: Dict[str, int] = {}
    for domain, words in _INTENT_DOMAINS.items():
        word_set = set(words)
        hit = len(desc_tokens & word_set) + 2 * len(name_tokens & word_set)
        if hit:
            weights[domain] = min(hit, 3)
    return weights


def _user_intents(user_msg: str) -> set:
    tokens = tokenize(user_msg) | _ascii_words(user_msg)
    intents = _domain_hits(tokens)
    # 2026-08-16: "paper_trading" 是纸面交易意图, tokenize 会拆出 "paper"
    # 误命中论文域(paper) → arxiv 工具被路由 → Hermes tool_search 空匹配泄漏 JSON。
    # 原文含 paper_trading/纸面交易/模拟交易 时剔除论文域, 归入交易相关意图。
    msg_lower = (user_msg or "").lower()
    if any(w in msg_lower for w in ("paper_trading", "paper trading", "纸面交易", "模拟交易", "模拟盘")):
        intents.discard("paper")
        intents.add("portfolio")
    return intents


# ── 股票实体锚点 ──────────────────────────────────────────────
_CN_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_HK_CODE_RE = re.compile(r"(?i)(?<![a-z0-9])(hk\d{4,5})(?![a-z0-9])")
_SH_SZ_CODE_RE = re.compile(r"(?i)(?<![a-z0-9])((?:sh|sz|bj)\d{6})(?![a-z0-9])")
_US_TICKER_RE = re.compile(r"(?<![A-Za-z\u4e00-\u9fff])([A-Z]{2,5})(?![A-Za-z\u4e00-\u9fff])")
# 常见大写缩写：不能当作美股代码（JSON/API/MACD/RSI 等）
_US_TICKER_EXCLUDED = frozenset(
    "AI OK NO US HK SH SZ BJ TV PC PE PB GDP KPI JSON API MACD RSI ETF IPO "
    "USD HKD CNY JPY EUR GBP APP HTML HTTP HTTPS URL SMS CEO CFO CTO COO "
    "CPU GPU RAM ROM MVP VPN DNS IP AIAGENT RAG LLM AGI QA".split()
)

FAMOUS_STOCKS: Dict[str, str] = {
    "贵州茅台": "600519", "茅台": "600519",
    "宁德时代": "300750", "比亚迪": "002594",
    "五粮液": "000858", "中国平安": "601318", "招商银行": "600036",
    "工商银行": "601398", "农业银行": "601288", "中国石油": "601857",
    "中国石化": "600028", "隆基绿能": "601012", "中芯国际": "688981",
    "腾讯": "hk00700", "阿里巴巴": "hk09988", "京东": "hk09618",
    "小米": "hk01810", "美团": "hk03690", "百度": "hk09888",
    "苹果": "AAPL", "特斯拉": "TSLA", "英伟达": "NVDA",
    "微软": "MSFT", "谷歌": "GOOGL", "亚马逊": "AMZN",
    "meta": "META", "奈飞": "NFLX",
}

_CODE_TO_NAME = {v: k for k, v in FAMOUS_STOCKS.items()}


def find_stock_entities(text: str) -> List[str]:
    """提取消息中的全部股票实体（代码优先，其次知名股票名）。"""
    entities: List[str] = []
    seen = set()
    m = _SH_SZ_CODE_RE.search(text)
    if m:
        entities.append(m.group(1).upper())
    m = _HK_CODE_RE.search(text)
    if m:
        entities.append(m.group(1).lower())
    for m in _CN_CODE_RE.finditer(text):
        entities.append(m.group(1))
    for name in sorted(FAMOUS_STOCKS, key=len, reverse=True):
        if name in text:
            entities.append(FAMOUS_STOCKS[name])
    for m in _US_TICKER_RE.finditer(text):
        tok = m.group(1).upper()
        if tok not in _US_TICKER_EXCLUDED:
            entities.append(tok)
    # 保序去重：不能用 list(set(...))——set 迭代顺序受哈希随机化影响
    result: List[str] = []
    for e in entities:
        if e not in seen:
            seen.add(e)
            result.append(e)
    return result


def find_stock_entity(text: str) -> Optional[str]:
    entities = find_stock_entities(text)
    return entities[0] if entities else None


def stock_name_for(code: str, user_msg: str) -> str:
    for name in sorted(FAMOUS_STOCKS, key=len, reverse=True):
        if name in user_msg:
            return name
    return _CODE_TO_NAME.get(code, "")


# ── tool_choice ───────────────────────────────────────────────


def _resolve_tool_choice(tool_choice: object) -> tuple:
    if tool_choice is None or tool_choice == "auto":
        return "auto", ""
    if tool_choice == "none":
        return "none", ""
    if tool_choice == "required":
        return "required", ""
    if isinstance(tool_choice, dict):
        fn = (tool_choice.get("function") or {}).get("name", "")
        if fn:
            return "forced", fn
    return "auto", ""


def _score_tools(
    user_msg: str,
    tools: List[Dict],
    threshold: int = 3,
    domain_boost: Optional[Dict[str, int]] = None,
) -> List[tuple]:
    """为每个工具打分。domain_boost: 域 → 加分（记忆/认知增强）。"""
    msg_lower = user_msg.lower()
    entity = find_stock_entity(user_msg)
    intents = _user_intents(user_msg)
    price_intent = bool(tokenize(user_msg) & _PRICE_INTENT_WORDS)
    boost = domain_boost or {}

    scored: List[tuple] = []
    for tool in tools:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        weights = _tool_domain_weights(tool)

        score = 0
        if entity:
            score += 2
        for d in (intents & set(weights)):
            if d == "quote" and not entity:
                # 个股行情工具（quote 域）需要股票实体；无实体时"行情"应匹配大盘工具
                continue
            base = 3 if d in _STRONG_DOMAINS else 2
            score += base * weights[d]
            score += boost.get(d, 0)
        if name.lower() in msg_lower:
            score += 5
        if price_intent and weights.get("quote"):
            score += 2
        scored.append((score, tool))
    return scored


def match_tool(
    user_msg: str,
    tools: List[Dict],
    tool_choice: object = "auto",
    threshold: Optional[int] = None,
    domain_boost: Optional[Dict[str, int]] = None,
) -> Optional[Dict]:
    """把用户消息路由到最合适的工具；没有足够把握时返回 None。"""
    if not tools:
        return None

    mode, forced_name = _resolve_tool_choice(tool_choice)
    if mode == "none":
        return None
    if mode == "forced":
        for tool in tools:
            if tool.get("function", {}).get("name") == forced_name:
                return tool
        return None

    effective_threshold = threshold if threshold is not None else 3
    scored = _score_tools(user_msg, tools, effective_threshold, domain_boost)

    best_score, best_tool = max(scored, key=lambda x: x[0])
    if mode == "required":
        return best_tool
    if best_score < effective_threshold:
        return None
    scores = [s for s, _ in scored]
    second_score = sorted(scores, reverse=True)[1] if len(scores) > 1 else -1
    if best_score <= second_score:
        return None
    return best_tool


# ── 参数抽取 ─────────────────────────────────────────────────

_REGION_MAP = {
    "cn": ["cn", "a股", "a 股", "中国", "沪", "深", "大陆", "china"],
    "hk": ["hk", "港股", "香港", "hong kong"],
    "us": ["us", "美股", "美国", "usa", "america"],
}


def _extract_value(user_msg: str, prop_name: str, prop_schema: Dict) -> object:
    ptype = prop_schema.get("type", "string")
    name_l = prop_name.lower()

    if name_l in {"symbol", "code", "stock", "ticker", "stock_code", "sec_code", "codes", "symbols", "securities"}:
        return find_stock_entity(user_msg) or ""

    # 自选股列表类参数：提取消息中全部 A 股 6 位代码，空格连接
    if name_l in {"stock_codes", "watchlist_codes", "watchlist", "stocks"}:
        codes = re.findall(r"(?<!\d)(\d{6})(?!\d)", user_msg)
        if codes:
            return " ".join(codes)

    if name_l in {"stock_name", "name", "stock_names", "names"}:
        code = find_stock_entity(user_msg) or ""
        return stock_name_for(code, user_msg)

    # 搜索主题类参数：停用词切分后合并剩余片段即主题（保留短语内部空格）
    if name_l in {"query", "keyword", "search", "q", "topic", "subject"}:
        stop = [
            "搜索", "搜一下", "搜", "查找", "找找", "查一下", "查查", "关于", "论文",
            "文献", "学术", "研究", "帮我", "帮忙", "请", "一下", "什么", "哪些",
            "最新", "相关", "资料", "信息", "看看", "了解", "知道", "推荐", "总结",
            "中文", "英文", "中英文", "中英", "arxiv", "知网", "万方",
            "的", "了", "和", "与", "及",
        ]
        s = user_msg
        for w in sorted(stop, key=len, reverse=True):
            s = s.replace(w, "\x00")  # 哨兵分隔：保留非停用片段内的空格
        parts = [p.strip() for p in s.split("\x00") if p.strip()]
        if parts:
            # 多个片段合并（多主题搜索更精准），去重保序
            seen = []
            for p in parts:
                if p not in seen:
                    seen.append(p)
            return " ".join(seen)
        return ""

    # 论文来源：中文语境 → crossref（openalex 免费额度不稳定）；arxiv/英文 → arxiv；否则不填
    if name_l in {"source", "paper_source"}:
        if "中英文" in user_msg or "中英" in user_msg:
            return "crossref"
        if any(w in user_msg for w in ("中文", "知网", "万方", "中文学术", "国内期刊", "中文期刊")):
            return "crossref"
        if any(w in user_msg for w in ("arxiv", "英文", "预印本")):
            return "arxiv"
        return None

    # 语言过滤：中文/英文；"中英文" 或未提 → 不填（调用方默认 all）
    if name_l in {"language", "lang"}:
        if "中英文" in user_msg or "中英" in user_msg:
            return None
        if "中文" in user_msg:
            return "zh"
        if "英文" in user_msg or "english" in user_msg.lower():
            return "en"
        return None

    # 相对日期/具体日期参数：前天/昨天/今天/YYYY-MM-DD
    if name_l in {"date", "day", "offset", "date_offset", "as_of"}:
        if "前天" in user_msg:
            return "-2"
        if "昨天" in user_msg:
            return "-1"
        if "今天" in user_msg or "今日" in user_msg:
            return "0"
        m = re.search(r"\d{4}-\d{2}-\d{2}", user_msg)
        if m:
            return m.group(0)
        return None

    if ptype in {"integer", "number"}:
        stripped = re.sub(r"(?i)hk\d{4,5}|\d{6}", "", user_msg)
        m = re.search(r"(\d+)\s*(?:条|天|个|次|篇|页|只)", stripped) or re.search(r"(\d+)", stripped)
        if not m:
            return None  # 无数字可提取：非 required 参数将跳过（调用方用默认值）
        if ptype == "number":
            mf = re.search(r"(\d+\.\d+)", stripped)
            return float(mf.group(1)) if mf else int(m.group(1))
        return int(m.group(1))

    if ptype == "boolean":
        if any(w in user_msg for w in ("不要", "不需要", "不带", "不含", "不包含", "别", "否")):
            return False
        if any(w in user_msg for w in ("是", "要", "需要", "应该", "必须", "带", "加上", "包含", "包括", "含")):
            return True
        return False

    if ptype == "array":
        stripped = re.sub(r"(?i)hk\d{4,5}", "", user_msg)
        nums = re.findall(r"\d+", stripped)
        return [int(n) for n in nums] if nums else []

    if name_l in {"periods", "period", "window", "ma_periods"}:
        stripped = re.sub(r"(?i)hk\d{4,5}|\d{6}", "", user_msg)
        nums = re.findall(r"\d+", stripped)
        return ",".join(nums[:8]) if nums else ""

    if name_l in {"region", "market", "exchange"}:
        msg_l = user_msg.lower()
        for region, keywords in _REGION_MAP.items():
            if any(k in msg_l for k in keywords):
                return region
        return "cn"

    enum = prop_schema.get("enum")
    if enum:
        msg_l = user_msg.lower()
        for opt in enum:
            if opt.lower() in msg_l:
                return opt
        return ""

    m = re.search(rf"{re.escape(prop_name)}\s*[=:：]?\s*([\u4e00-\u9fffA-Za-z0-9]+)", user_msg, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def extract_arguments(user_msg: str, parameters: Dict) -> Dict:
    props = (parameters or {}).get("properties", {})
    required = set((parameters or {}).get("required", []) or [])
    args: Dict = {}
    for pname, pschema in props.items():
        value = _extract_value(user_msg, pname, pschema or {})
        if value is None:
            if pname in required:
                # required 参数提取不到：给类型默认值，避免调用方崩
                args[pname] = 0 if (pschema or {}).get("type") in {"integer", "number"} else ""
            continue  # 非 required 且提取不到 → 不传，调用方用默认值
        args[pname] = value
    return args


def _single_call(tool: Dict, user_msg: str) -> Dict:
    fn = tool.get("function", {})
    parameters = fn.get("parameters", {})
    arguments = extract_arguments(user_msg, parameters)
    return {
        "tool_calls": [
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": fn.get("name", ""),
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
        "engine": "tools:rule",
    }


def _build_parallel_calls(tool: Dict, user_msg: str) -> Dict:
    fn = tool.get("function", {})
    parameters = fn.get("parameters", {})
    props = (parameters or {}).get("properties", {})
    calls = []
    for ent in find_stock_entities(user_msg):
        args = {}
        for pname, pschema in props.items():
            args[pname] = _extract_value(ent + " " + user_msg, pname, pschema or {})
        calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": fn.get("name", ""),
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        )
    return {"tool_calls": calls, "engine": "tools:rule"}


def build_tool_calls(
    user_msg: str,
    tools: List[Dict],
    tool_choice: object = "auto",
    threshold: Optional[int] = None,
    domain_boost: Optional[Dict[str, int]] = None,
) -> Optional[Dict]:
    """生成 OpenAI 格式 tool_calls；无把握时返回 None。"""
    mode, _ = _resolve_tool_choice(tool_choice)
    if mode == "none":
        return None

    tool = match_tool(user_msg, tools, tool_choice, threshold, domain_boost)
    if tool is None:
        return None

    fn = tool.get("function", {})
    parameters = fn.get("parameters", {})
    props = (parameters or {}).get("properties", {})
    is_stock_scoped = any(
        p.lower() in {"symbol", "code", "stock", "ticker", "stock_code", "sec_code", "codes", "symbols", "securities"}
        for p in props
    )

    if is_stock_scoped and mode != "forced":
        entities = find_stock_entities(user_msg)
        if len(entities) > 1:
            return _build_parallel_calls(tool, user_msg)

    return _single_call(tool, user_msg)


# ── AGI 认知增强 ─────────────────────────────────────────────


@dataclass
class ToolCallPlan:
    """AGI 工具决策结果。"""
    tool_calls: List[dict]
    engine: str = "agi:tool_router"
    decision: str = "matched"  # matched | forced | none
    confidence: float = 0.0
    threshold_used: int = 3
    cognition: Dict = field(default_factory=dict)


def _memory_domain_boost(memory_context: Optional[List[str]]) -> Dict[str, int]:
    """语义记忆 → 意图域加分：历史记忆里反复出现的域，给对应工具 +1。"""
    boost: Dict[str, int] = {}
    if not memory_context:
        return boost
    for text in memory_context:
        for d in _user_intents(str(text)):
            boost[d] = boost.get(d, 0) + 1
    return boost


class AGIToolRouter:
    """AGI 认知层工具决策器。

    用法：
        router = AGIToolRouter()
        plan = router.decide(user_msg, tools, psi_state=..., memory_context=[...])
        if plan:  # plan.tool_calls 可直接放进 OpenAI 响应
    """

    def __init__(self, threshold_base: int = 3):
        self.threshold_base = threshold_base
        self.last_decision: Optional[ToolCallPlan] = None
        self.decision_history: List[ToolCallPlan] = []

    # ── 认知阈值 ──────────────────────────────────────────

    def adjusted_threshold(self, psi_state: Optional[Dict]) -> int:
        """按 PSI 认知状态调整决策阈值（负面情感/低自信 → 更保守）。"""
        delta = 0
        psi = psi_state or {}
        emotion = str(psi.get("emotion", "neutral")).lower()
        delta += _EMOTION_THRESHOLD_DELTA.get(emotion, 0)

        confidence = psi.get("confidence")
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None and confidence < 0.3:
            delta += 1

        needs = psi.get("needs") or {}
        try:
            competence = float(needs.get("competence", 0.5))
        except (TypeError, ValueError):
            competence = 0.5
        if competence >= 0.7:
            delta -= 1  # 能力感强 → 更愿意尝试工具

        return max(1, self.threshold_base + delta)

    # ── 决策主流程 ────────────────────────────────────────

    def decide(
        self,
        user_msg: str,
        tools: List[Dict],
        tool_choice: object = "auto",
        psi_state: Optional[Dict] = None,
        memory_context: Optional[List[str]] = None,
    ) -> Optional[ToolCallPlan]:
        """AGI 认知决策：返回 ToolCallPlan 或 None（不调用工具）。"""
        threshold = self.adjusted_threshold(psi_state)
        boost = _memory_domain_boost(memory_context)

        mode, forced_name = _resolve_tool_choice(tool_choice)
        if mode == "none":
            return None

        tool = match_tool(user_msg, tools, tool_choice, threshold, boost)
        if tool is None and mode == "required":
            # required：强行取最高分工具
            scored = _score_tools(user_msg, tools, threshold, boost)
            if scored:
                tool = max(scored, key=lambda x: x[0])[1]

        if tool is None:
            plan = ToolCallPlan(
                tool_calls=[],
                decision="none",
                threshold_used=threshold,
                cognition={"psi": psi_state or {}, "memory_boost": boost},
            )
            self.last_decision = plan
            self.decision_history.append(plan)
            return None

        routed = build_tool_calls(user_msg, tools, tool_choice, threshold, boost)
        if routed is None:
            return None

        # 置信度 = 阈值余量（简化）：base + 分数信息已在 routed 里，这里给出认知快照
        plan = ToolCallPlan(
            tool_calls=routed["tool_calls"],
            engine="agi:tool_router",
            decision="forced" if mode == "forced" else "matched",
            threshold_used=threshold,
            cognition={
                "psi": psi_state or {},
                "memory_boost": boost,
                "threshold_adjustment": threshold - self.threshold_base,
            },
        )
        self.last_decision = plan
        self.decision_history.append(plan)
        return plan


# 模块级单例（供 api.py 等直接使用）
_default_router: Optional[AGIToolRouter] = None


def get_router() -> AGIToolRouter:
    global _default_router
    if _default_router is None:
        _default_router = AGIToolRouter()
    return _default_router


# ── tool 结果回填（与 AGI 认知共用）───────────────────────────


def _tool_id_name_map(messages: List[Dict]) -> Dict[str, str]:
    """tool_call_id → 工具名（兼容 {"id","name"} 与 {"id","function":{"name"}} 格式）。"""
    id_to_name: Dict[str, str] = {}
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            tid = tc.get("id") or ""
            tname = tc.get("name") or (tc.get("function") or {}).get("name") or ""
            if tid and tname:
                id_to_name[tid] = tname
    return id_to_name


def _tool_id_name_map(messages: List[Dict]) -> Dict[str, str]:
    """tool_call_id → 工具名（兼容 {"id","name"} 与 {"id","function":{"name"}} 格式）。"""
    id_to_name: Dict[str, str] = {}
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            tid = tc.get("id") or ""
            tname = tc.get("name") or (tc.get("function") or {}).get("name") or ""
            if tid and tname:
                id_to_name[tid] = tname
    return id_to_name


def _tool_id_name_map(messages: List[Dict]) -> Dict[str, str]:
    """tool_call_id → 工具名（兼容 {"id","name"} 与 {"id","function":{"name"}} 格式）。"""
    id_to_name: Dict[str, str] = {}
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            tid = tc.get("id") or ""
            tname = tc.get("name") or (tc.get("function") or {}).get("name") or ""
            if tid and tname:
                id_to_name[tid] = tname
    return id_to_name


def summarize_tool_result(messages: List[Dict]) -> Optional[str]:
    """最后一轮是 tool 结果时，生成简短的中转回答（供 agent 循环消费）。"""
    if not messages:
        return None
    last = messages[-1]
    if last.get("role") != "tool":
        return None
    id_to_name = _tool_id_name_map(messages)
    name = last.get("name") or id_to_name.get(last.get("tool_call_id") or "") or "unknown_tool"
    content = str(last.get("content", ""))
    if len(content) > 400:
        content = content[:400] + "…"
    return f"[工具 {name} 执行完成]\n返回结果：{content}"



def collect_tool_context(messages: List[Dict]) -> str:
    parts = []
    for m in messages:
        if m.get("role") == "tool":
            name = m.get("name", "tool")
            content = str(m.get("content", ""))
            if len(content) > 200:
                content = content[:200] + "…"
            parts.append(f"[{name}] {content}")
    return "\n".join(parts[-5:])
