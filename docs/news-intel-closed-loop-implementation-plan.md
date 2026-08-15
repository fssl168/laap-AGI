# LAAP Paper Trading — 新闻情报 × LLM+RSI 真伪识别 × 研报策略 × 自动下单闭环 实施计划

> **日期**: 2026-08
> **状态**: ✅ **P0-P4 全部实施完成 + 用户环境判定链路验收通过**（2026-08-15）：news_intel/fees/limit_utils/quant_config + news_verifier + research_strategy/risk_gate + news_pipeline/NewsSignalWorker + db schema 4 表 + 5 条 API 路由；ledger 扣费（B2）落地；LLM 适配器修复（DeepSeekProvider+initialize+urllib 回退）；D1 去重修复（仅非 fallback 视为已判定）+ scan `force` 参数。用户环境：真实 akshare 新闻/研报、LLM 判定真实生效（利空→bearish/neutral 不下单、构造利好→genuine_bullish 0.95 buy）、`auto_order=true` 对利空正确静默（D1 去重+缓存）。**自动下单真实触发待一条真实利好新闻**（S2 代码路径沙箱 E2E 已验证）。9 测试文件 93 新测试 + test_ledger_fees 5 + test_quant_api 扩展，全量化回归 288 passed / 0 failed。
> **任务**: 参考 DSA 项目（`D:\leanpython\daily_stock_analysis`）与金策智算（`D:\projects\jin-ce-zhi-suan`），为 `laap/paper_trading` 增加新闻驱动的盘中闭环能力
> **已确认决策（用户拍板）**: 自动下单 = **Paper 自动执行**（接入现有 PaperLedger 自动成交，预留真实券商接口缝，本轮不做真钱下单）

---

## 1. 背景与目标

### 1.1 现状

`laap/paper_trading/` 已有最小真实交易循环 + 记忆×自进化闭环：

```
行情(K线/实时价) → 多因子信号判定(含RSI) → TradingSelf审核 → 自动下单/成交 → 留痕 → 教训沉淀 → 参与推理
```

但它**没有**外部信息能力：不获取新闻、不获取个股资料、不获取研报，信号只来自技术面。且实盘执行层有三个量化真实性缺口（金策智算对标确认）：**① 下单前无规则风控门；② ledger 实盘不扣交易成本；③ 涨停/停牌判定只有近似启发式**。

### 1.2 目标（用户需求）

1. 获取**外部标的相关新闻** + **股票标的基本资料**（"股票概况" / "个股资料"）；
2. 基于 **LLM + RSI（基本面上下文）** 识别**真利好**与**假消息**，输出**置信度（0~1）**；
3. **为真** → 调度信号能**立即产生**（不等日终）；**为假** → **静默**（不产生订单）；
4. 通过**研报策略**得出**最佳买入/卖出时间 + 最佳股数**；
5. 以上全管线与**自动下单**打通形成**闭环**（Paper 自动执行），且下单前经过**规则风控门**。

### 1.3 成功标准

| # | 标准 | 验证方式 |
|---|---|---|
| S1 | `fetch_stock_news / fetch_stock_profile / fetch_research_reports` 在用户环境对 `600519` 返回真实数据 | 验收命令（§8） |
| S2 | 真利好新闻 → 立即自动下单：`signals / orders / trades / decisions` 落库，`decision_id` 追溯链闭环 | `test_news_pipeline.py` E2E |
| S3 | 假消息/中性/低置信 → **0 订单**（静默），判定结果留痕可查 | `test_news_pipeline.py` E2E |
| S4 | 研报策略输出 `best_buy_time / best_sell_time / best_quantity`：100 整手、受仓位上限约束、含止盈止损价位、**预算已扣交易成本** | `test_research_strategy.py` |
| S5 | **下单前风控门**生效：超单票/总仓位上限、单日亏损熔断、连亏暂停 → 订单被拒绝并留痕（刑部式审计） | `test_risk_gate.py` |
| S6 | 新增测试全绿，基线 `pytest tests -q` 失败数不增 | §7 |

---

## 2. 参考项目映射

### 2.1 DSA（`D:\leanpython\daily_stock_analysis`）

| 能力 | DSA 参考文件/函数 | 参考要点 |
|---|---|---|
| 新闻搜索 | `src/search_service.py::SearchService.search_stock_news(stock_code, stock_name, max_results, focus_keywords)` | 多供应商、缓存、相关性排序；含研报查询词 `"{stock_name} 研报 目标价 评级 深度分析"`（L4008） |
| 新闻包装 | `src/services/alphasift_service.py::search_dsa_stock_news` | 轻量 wrapper |
| LLM 判定+置信度 | `paper_trading/agent_risk.py::AgentRiskReviewer` | 严格 JSON verdict `{approved, confidence 0~1, reason, concerns, action, stop_loss, take_profit}`；超时/失败 fallback 不阻塞交易循环 |
| 置信度门槛静默 | `paper_trading/hooks.py::push_ai_signal_from_decision` | `confidence < paper_trading_ai_signal_min_confidence(0.7) → 跳过推送（静默）` |
| 信号→订单闭环 | `paper_trading/market_listener.py::_consume_ai_signals` | 队列 → 自选股过滤 → 置信度门 → 转内部 Signal → `engine.submit_signal` → 回写审计（decision_id ↔ signal_id） |
| 异步信号 worker | `paper_trading/ai_signal_worker.py::AISignalWorker` | daemon 线程按 cron 触发分析，不阻塞规则引擎 tick |
| 止盈止损 | `paper_trading/sltp_calculator.py::SLTPCalculator` | ATR + Fibonacci + 支撑/阻力三源融合止损止盈 |
| 真实券商适配 | `paper_trading/broker/`（base/paper_broker/eastmoney_broker/router） | Broker 抽象层：本轮只留接口缝 |

### 2.2 金策智算（`D:\projects\jin-ce-zhi-suan`）— 深入借鉴点

金策智算以「三省六部」分权体系组织量化系统：**中书省**（信号生成）→ **门下省**（风控一票否决）→ **尚书省**（执行/持仓/止损触发）→ 六部（户部管资金、兵部管撮合、刑部管审计、礼部管报表）。以下 9 点对量化值得借鉴，全部纳入本计划（标注 **[必取]** / **[可选]**）：

| # | 借鉴点 | 参考文件/函数 | 金策智算做法 | 对 laap 的落地价值 |
|---|---|---|---|---|
| B1 **[必取]** | 下单前规则风控门（一票否决） | `src/core/menxia_sheng.py::MenxiaSheng.check_signal` | 5 条规则：R1 止损距离≤上限；R2 单票仓位≤上限；R3 总仓位≤上限；R4 单日亏损熔断；R5 连亏≥N 当日停止开仓；拒绝全部记入刑部 | 新闻驱动的自动下单**不能只靠 LLM 置信度放行**，必须有硬风控门兜底；拒绝留痕形成审计闭环 |
| B2 **[必取]** | 交易成本模型 | `src/ministries/hu_bu_revenue.py::HuBuRevenue.calculate_cost`；`src/utils/constants.py` | 金策：佣金 `max(5, 金额×万2.5)`、印花税 0.1%、过户费 万0.1、滑点 0.1%。**本项目统一采用 `laap/paper_trading/costs.py::DEFAULT_COSTS`（现行 A 股：佣金 0.025% 双边、印花税 0.05% 仅卖出、滑点 0.1%，min_commission/transfer_fee 默认 0）**——金策 0.1% 为 2023-08 减半前旧口径，不采用 | laap 实盘 ledger 目前**零成本**：买入预算必须含费（否则超买）、卖出 PnL 必须扣费（否则收益虚高）；研报策略"最佳股数"以含费预算为准；**费率单源在 costs.py，回测与 ledger 一致** |
| B3 **[必取]** | 涨停/跌停/停牌检测 | `src/ministries/bing_bu_war.py::_is_limit_up/_is_limit_down/_is_suspended_or_invalid` | 多信号检测：`is_limit_up` 标记 / `limit_status` / `close vs up_limit×0.9999` / OHLC 形态启发式 | 替换 news_verifier 里 `change_pct≥9.8%` 的近似判断；买入拒涨停、卖出拒跌停、停牌拒单 |
| B4 **[必取]** | 现金不足二分下打量 | `src/core/shangshu_sheng.py::ShangshuSheng.execute_order`（L127-143） | 请求量超出含费现金时，对整手数**二分查找最大可买量**；不足一手拒单 | 研报策略给出的股数是预估，实际下单时现金/费用变化 → 优雅降档而非失败 |
| B5 **[必取]** | 盘中时段判定 | `src/core/live_cabinet.py::_is_market_session_time` | 9:30-11:30 / 13:00-15:00 才允许盘中逻辑 | NewsSignalWorker 只在 A 股盘中轮询，避免盘前/盘后误下单 |
| B6 **[必取]** | 启动数据新鲜度校验 | `src/core/live_cabinet.py::warm_up` / `startup_kline_freshness` | 实盘循环启动前校验 K 线新鲜度，陈旧则**大声失败**而非静默跑旧数据 | NewsSignalWorker 启动时校验 kline 最新日期与新闻最近发布时间；陈旧 → 告警并跳过本轮 |
| B7 **[必取]** | 可调参数机制 | `src/utils/runtime_params.py::get_value(path, default)` + `config_loader` | 所有阈值/成本/风控红线走配置，运行时可热调 | 本计划全部阈值（置信度门槛、风控红线、成本率、轮询间隔）统一走 `laap/config` + env，运行时可调，不改代码 |
| B8 **[可选]** | 股数模式 | `src/core/zhongshu_sheng.py::_resolve_fallback_qty` | `order_qty_mode`: `fixed`（固定股数）/ `cash_pct`（现金百分比 → 整手） | 研报策略支持两种股数模式，默认 `cash_pct`（风险预算制） |
| B9 **[可选]** | T+1 持仓 lot + FIFO 成本 | `src/core/shangshu_sheng.py::_ensure_lots/_sellable_qty_t1/_consume_lots_fifo` | 持仓按买入批次（buy_day/unit_cost）记账，T+1 可卖量=非当日 lot 之和，卖出 FIFO 计算成本 | laap 现为单笔 trade 简单同自然日锁仓；lot 模型可支撑多批买入后的精确"最佳卖出股数"。**本轮列为增强项**（laap 单笔买入场景已够用） |
| B10 **[可选]** | 信号执行延迟监控 | `src/core/live_cabinet.py::_emit_live_alert` | warn/critical 双阈值告警（如 signal_execution_delay_ms） | NewsSignalWorker 记录"新闻→判定→下单"延迟指标并告警 |
| B11 **[可选]** | 策略意图 LLM 解析 | `src/strategy_intent/strategy_intent.py` / `screener_parser.py` | LLM 把自然语言策略描述 → 结构化 `StrategyIntent{strategy_type, logic, indicators, entry, exit, risk_profile, confidence}` | 未来可由 LLM 直接解析研报内容生成结构化交易计划（extension，本轮不做） |
| B12 **[可选]** | 业绩报表 | `src/ministries/li_bu_rites.py` | 连胜/连亏、月度盈利比、权益曲线、排行榜 | 叠加到现有 `trade_fitness`（extension，本轮不做） |

---

## 3. 架构与数据流

### 3.1 管线图

```
┌─────────────────────────── 数据获取层 news_intel.py ───────────────────────────┐
│  akshare: stock_news_em(symbol)         → 个股新闻  List[NewsItem]              │
│  akshare: stock_individual_info_em      → 个股资料  StockProfile  ─┐           │
│  akshare: stock_profile_cninfo          → 公司概况(兜底)  ─────────┴ 双源合并   │
│  akshare: stock_research_report_em      → 个股研报  List[ResearchReport]       │
│  进程内缓存 TTL 30min + used_fallback 标记（失败静默降级，不抛异常）             │
└──────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────── 技术状态层（复用 backtest_runner）───────────────────┐
│  load_ohlcv(symbol) → _rsi / _atr / _sma → TechState{rsi, atr, close, ma20,     │
│      prev_close, change_pct} + limit_utils 涨停/跌停/停牌检测（借鉴 B3）          │
└──────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────── 判定层 news_verifier.py ─────────────────────────────┐
│  verify_news(item, profile, tech_state, llm_call) → NewsVerdict                 │
│    LLM（严格 JSON）: {verdict, confidence 0~1, reasons[], impact}               │
│    verdict ∈ {genuine_bullish, fake_news, neutral, bearish}                     │
│    RSI 合成: 超买(>70) 降档 conf×0.8（追高）；超卖(<30)+真利好 升档 +0.05        │
│    涨停检测（B3 多信号，替代 9.8% 近似）→ trade_action="wait" 不开新仓           │
│  门槛: genuine_bullish 且 confidence ≥ NEWS_MIN_CONFIDENCE(0.7) → 放行          │
│  LLM 失败 → keyword 启发式 fallback + used_fallback=True，默认静默(fail-closed) │
│  aggregate_verdicts(verdicts) → AggregatedNewsDecision{dispatch, confidence,    │
│                                                         top_news_ids, reason}   │
└──────────────────────────────────────────────────────────────────────────────────┘
                                      │ dispatch=true
                                      ▼
┌─────────────────────────── 研报策略层 research_strategy.py ─────────────────────┐
│  build_trade_plan(...) → TradePlan                                               │
│    best_buy_time: 评级占比(买入/增持≥60% 且 RSI≤50 → now；超买/乖离大 → pullback│
│                   回调至MA20/支撑；无研报 → wait)                                │
│    best_sell_time: stop_loss = price − atr×sl_mult（DSA SLTP ATR 止损）          │
│                    take_profit = min(研报目标价均值, price + atr×tp_mult)        │
│    best_quantity: 含费预算 cash × position_scale − 交易成本(B2)                  │
│                   qty = 预算 / (price − stop) → 100 整手 → ≤ 仓位上限            │
└──────────────────────────────────────────────────────────────────────────────────┘
                                      │ buy_time=="now" 且 auto_order
                                      ▼
┌─────────────────────────── 风控门 risk_gate.py（借鉴 B1 门下省）────────────────┐
│  check_signal(plan, ledger, cash, daily_pnl) → (ok, rule_id, reason)            │
│    R1 止损距离 ≤ max_stop_loss_pct(5%)                                          │
│    R2 单票仓位 ≤ max_pos_per_stock(10%) 且 ≤ TradingSelf.position_scale_max     │
│    R3 总仓位 ≤ max_total_pos(50%)                                               │
│    R4 单日亏损(含未实现) ≥ 2% → 熔断                                            │
│    R5 连亏 ≥ 3 笔 → 当日停止开仓                                                │
│    拒绝 → 落 risk_rejections 表（刑部式审计）+ 静默（不产生订单）               │
└──────────────────────────────────────────────────────────────────────────────────┘
                                      │ pass
                                      ▼
┌─────────────────────────── 管线+自动下单闭环 news_pipeline.py ──────────────────┐
│  NewsSignalPipeline.run(symbol, auto_order) → Dict（llm_call 在构造参数注入）          │
│    1. [可选] TradingSelf.judge("buy") 审核门（与 run_daily_cycle 一致）          │
│    2. 下单量二次校验: 含费现金二分下打量（B4）；涨停/停牌拒单（B3）              │
│    3. loop.decide_and_trade(...) = record_decision +                            │
│         ledger.submit_signal(client_request_id=decision_id 幂等) +              │
│         ledger.fill_order(自动成交, 扣交易成本 B2)                               │
│    4. rationale 注入 [news] 证据（标题+置信度）                                  │
│    5. 落 news_verdicts 表（含静默判定）+ risk_rejections（风控拒绝）             │
│    6. 闭环贯通: decision_id → decisions → orders → trades → outcomes/lessons    │
│       （现有 decision_record/memory_bridge/trading_self 复用）                   │
│  假消息/中性/低置信 → 静默（0 订单，仅留痕）                                     │
└──────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────── 定时触发（默认关）───────────────────────────────────┐
│  NewsSignalWorker: daemon 线程，盘中(B5 时段判定)每 N 分钟(默认30)轮询 watchlist │
│  启动先做数据新鲜度校验(B6)，陈旧 → 告警跳过；LAAP_NEWS_INTRADAY=1 启用         │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 与现有闭环的关系

- **同构**：新管线复用 `PaperClosedLoop.decide_and_trade` / `run_daily_cycle` 的决策-下单-成交路径与 `TradingSelf` 审核，`decision_id` 作为贯穿键；
- **互补**：日终 `run_daily_cycle` 继续负责技术面多因子信号与平仓（sell 分支）；新管线负责**盘中新闻驱动买入**；
- **新增硬约束**：自动下单前增加**规则风控门（门下省式）**，与 `TradingSelf` 软审核（人格/经验/自我评估）形成双层防线——软审核管"该不该买"，硬风控管"能不能买"；
- **不重复**：同一 `decision_id` 幂等（`client_request_id`），重复 scan 不重复下单；同一新闻 (symbol, title, published_at) 去重。

---

## 4. 实施清单（文件 + 函数级）

### 4.1 新文件 `laap/paper_trading/news_intel.py` — 外部数据获取层

| 符号 | 职责 | 验收点 |
|---|---|---|
| `@dataclass NewsItem` | `symbol/title/content/source/published_at/url` + `to_dict/from_dict` | 可序列化 |
| `@dataclass StockProfile` | 个股资料（总市值/流通市值/总股本/流通股本/行业/上市时间）+ 公司概况（主营业务/所属行业/上市日期/注册资金），双源合并 | `source` 字段标记来源 |
| `@dataclass ResearchReport` | 报告名称/评级/机构/预测EPS/预测PE/日期/PDF链接 | 可序列化 |
| `fetch_stock_news(symbol, name="", max_results=10, focus_keywords=None, days=7) -> (List[NewsItem], meta)` | `ak.stock_news_em(symbol)`；关键词过滤；按 (symbol, title 归一化, published_at) 去重 | 失败 → 空列表 + `used_fallback=True`，不抛异常 |
| `summarize_news(item, local_tool) -> (summary, summary_hash)` | 用**本地 agents skills 工具**（非远程 LLM，控制成本）对**判定为真的新闻**生成摘要；`sha1(summary)` 作键落盘 `news_summaries` 表，随时可取 | 摘要哈希落盘、可检索、可复现 |
| `fetch_stock_profile(symbol) -> (StockProfile, meta)` | `ak.stock_individual_info_em` 优先（个股资料）→ 失败 `ak.stock_profile_cninfo`（股票概况）→ 再失败空+fallback | 双源兜底链正确；`meta.source/used_fallback` |
| `fetch_research_reports(symbol, max_results=10) -> (List[ResearchReport], meta)` | `ak.stock_research_report_em(symbol)` | 解析列：报告名称/评级/机构/盈利预测/日期 |
| `_cache_get/_cache_put` | 进程内 TTL 缓存（30min，B7 可调） | 短窗口重复拉取命中缓存 |

> `akshare` 模块可注入（测试 monkeypatch 用），延续 `market_source.py` 的注入风格。

### 4.2 新文件 `laap/paper_trading/limit_utils.py` — 涨停/跌停/停牌检测（借鉴 B3）

| 符号 | 职责 | 验收点 |
|---|---|---|
| `is_limit_up(kline_like) -> bool` | 多信号检测：`is_limit_up` 标记 / `limit_status` / `close ≥ up_limit×0.9999` / OHLC 形态启发式（high==low 且 close==high 且 close≥open） | 与金策智算 `bing_bu_war._is_limit_up` 行为一致 |
| `is_limit_down(kline_like) -> bool` | 对称检测（down_limit×1.0001 / 形态） | 同上 |
| `is_suspended_or_invalid(kline_like) -> bool` | close≤0 / high<low / volume≤0 / is_suspended | 停牌/无效数据拒单 |

> 输入统一为 dict（`{close, high, low, open, volume, up_limit, down_limit, is_limit_up, is_limit_down, is_suspended, limit_status}`），由 `market_source.get_price` meta 与 kline 最后一行组装。

### 4.3 新文件 `laap/paper_trading/fees.py` — 交易成本模型（借鉴 B2）

| 符号 | 职责 | 验收点 |
|---|---|---|
| `@dataclass FeeModel` | `commission_rate=0.00025, min_commission=0.0, stamp_duty=0.0005(仅卖出), transfer_fee=0.0, slippage=0.001` | **默认费率 = `costs.py::DEFAULT_COSTS` 单源**（现行 A 股，金策 min_commission=5/transfer_fee 万0.1 不启用）；全字段可配置（B7） |
| `calculate_cost(amount, direction, fee=None) -> (total, commission, stamp, transfer)` | 佣金=max(min, amount×rate)；印花税仅卖出；过户费 | 数值精确 |
| `apply_slippage(price, direction, fee=None) -> float` | 买入价×(1+滑点)，卖出价×(1−滑点) | 双向符号正确 |

> **向后兼容**：`PaperLedger` 默认 `fee_model=None`（零成本，现有 632 项测试行为不变）；新闻管线显式注入真实 `FeeModel`，使"最佳股数"预算与 PnL 扣费在新闻驱动场景生效。

### 4.4 新文件 `laap/paper_trading/risk_gate.py` — 下单前风控门（借鉴 B1）

| 符号 | 职责 | 验收点 |
|---|---|---|
| 常量（B7 可调） | `MAX_STOP_LOSS_PCT=0.05 / MAX_POS_PER_STOCK=0.10 / MAX_TOTAL_POS=0.50 / MAX_DAILY_LOSS_PCT=0.02 / CONSECUTIVE_LOSS_LIMIT=3` | 与金策智算 constants 对齐 |
| `class RiskGate` | 注入 `ledger`（读现金/持仓/单日盈亏/连亏计数） | 可注入可测试 |
| `check_signal(plan, cash, daily_pnl, trading_self=None) -> (ok: bool, rule_id: str, reason: str)` | R1 止损距离 ≤ 5%；R2 单票仓位 ≤ 10% 且 ≤ TradingSelf.position_scale_max；R3 总仓位 ≤ 50%；R4 单日亏损（含未实现 MTM）≥2% 熔断；R5 连亏≥3 当日停开仓 | 每条规则独立可触发；拒绝原因明确 |
| `record_rejection(db, symbol, rule_id, reason, meta) ` | 落 `risk_rejections` 表（刑部式审计） | 拒绝全部留痕 |
| `compute_loss_streak(db, symbol="")` | 从 outcomes⋈trades 计算当前连亏数（平仓回填后）；胜清零/亏累加 | 复用 outcomes 表 |

### 4.5 新文件 `laap/paper_trading/news_verifier.py` — LLM + RSI 判定层

| 符号 | 职责 | 验收点 |
|---|---|---|
| `@dataclass TechState` | `rsi/atr/close/ma20/prev_close/change_pct/limit_up/limit_down/suspended` | 由 kline + limit_utils 组装 |
| `compute_tech_state(symbol, ohlcv=None) -> TechState` | 复用 `backtest_runner._rsi/_atr/_sma`（直接 import，零改动）+ limit_utils | RSI/ATR 与回测口径一致 |
| `@dataclass NewsVerdict` | `news_id/verdict/confidence/reasons/impact/trade_action/used_fallback/rsi` | 全字段可序列化 |
| `build_verify_prompt(item, profile, tech_state) -> str` | 参考 DSA `REVIEW_PROMPT_TEMPLATE`：新闻正文 + 个股资料 + RSI/涨跌幅/涨停状态，要求严格 JSON | prompt 含判定要点（实质利好 vs 炒作 vs 辟谣） |
| `parse_verdict(text) -> Optional[Dict]` | 复用 `llm_refine.parse_params` 的鲁棒 JSON 解析（容忍 markdown 包裹） | 解析成功/失败路径确定 |
| `verify_news(item, profile, tech_state, llm_call) -> NewsVerdict` | **仅当该标的有未判定过的新新闻时才调用 `llm_call`**（先 fetch 过滤，无新新闻 → 不调 LLM，控制成本）；LLM 判定 + RSI 合成（超买降档 ×0.8 / 超卖升档 +0.05）+ 涨停检测（limit_utils）→ wait；`llm_call(prompt, system=..., max_tokens=800)` | 阈值、合成规则正确；无新新闻 → 0 次 LLM 调用 |
| `NEWS_MIN_CONFIDENCE = 0.7` | 模块级常量（对齐 DSA 0.7，B7 可调） | 可配置 |
| `_heuristic_verify(item) -> NewsVerdict` | LLM 不可用时的 keyword 启发式（真利好词库/辟谣词库），`used_fallback=True`，置信度 ≤0.5 | 默认不触发自动下单 |
| `aggregate_verdicts(verdicts) -> AggregatedNewsDecision` | 多篇新闻取最高置信 genuine_bullish；`{dispatch, confidence, top_news_ids, reason}` | 聚合正确 |

> **LLM 契约**：`llm_call(prompt, system="", max_tokens=800) -> dict`（含 `text`）或 `str`，与 `llm_refine.build_llm_refine_fn` 的适配器一致。生产可用 `laap/agi/hermes_integration.py::HermesIntegration.llm_call` 或 `laap/agi/llm_integration.py::LAAPLLMIntegration.llm_call`（`.env` 已有 `DEEPSEEK_API_KEY`）。

### 4.6 新文件 `laap/paper_trading/research_strategy.py` — 研报策略层

| 符号 | 职责 | 验收点 |
|---|---|---|
| `@dataclass TradePlan` | `action(buy/hold), buy_time(now/pullback/wait), quantity, stop_loss, take_profit, rationale, used_fallback` | 全字段可序列化 |
| `rating_bullish_ratio(reports) -> float` | 买入/增持评级占比 | ∈[0,1]，无研报→0 |
| `target_price_mean(reports) -> Optional[float]` | 研报目标价均值（无目标价列时用「评级+预测EPS×行业PE 参考」，缺失→None 不报错） | 缺失安全 |
| `build_trade_plan(symbol, profile, reports, tech_state, cash, position_scale, trading_self=None, fee_model=None) -> TradePlan` | 见 §3.1；**预算含费**（fees.calculate_cost）；`trading_self` 提供 `position_scale_max` 上限；股数模式支持 `cash_pct`（默认）/`fixed`（B8） | 确定性、零新依赖 |
| `_round_lot(qty) -> int` | 向下取整到 100 股整手 | A 股整手 |

### 4.7 新文件 `laap/paper_trading/news_pipeline.py` — 管线编排 + 自动下单闭环

| 符号 | 职责 | 验收点 |
|---|---|---|
| `class NewsSignalPipeline` | 装配 news_intel + news_verifier + research_strategy + risk_gate + fees + PaperClosedLoop | 依赖全部可注入 |
| `run(symbol, auto_order=True, force=False) -> Dict` | 一次新闻扫描全流程（§3.1）：数据 → 判定 → 研报计划 → **风控门** → 下单；返回 `{symbol, news_count, profile, verdicts, aggregated, plan?, order?, dispatched, decision_id, silent, reason}`（llm_call 在构造参数注入；force 忽略 D1 去重强制重判） | 真利好→风控通过→自动下单；假→静默；风控拒→留痕静默 |
| `_dispatch(loop, symbol, plan, decision, evidence) -> Dict` | `TradingSelf.judge`（可选）→ **RiskGate.check_signal** → **含费现金二分下打量（B4）** → `loop.decide_and_trade`（`fill_order` 扣费 B2） | rationale 含 `[news]` 证据；超预算优雅降档 |
| `_persist_verdicts(db, symbol, verdicts, dispatched, decision_id)` | 落 `news_verdicts` 表 | 含静默判定 |
| `class NewsSignalWorker` | daemon 线程：**盘中时段（B5）**每 N 分钟（默认 30）对 watchlist 调 `run()`；**启动新鲜度校验（B6）**；`LAAP_NEWS_INTRADAY=1` 启用；非交易日跳过（复用 `QuantDailyScheduler._is_trading_day`） | start/stop/stats 幂等；陈旧数据告警跳过 |
| `executor` 注入参数 | 默认走 `PaperClosedLoop.decide_and_trade`（Paper 自动执行）；预留未来挂 DSA `EastMoneyBroker` 型适配器 | 本轮不实现真钱下单 |

### 4.8 修改 `laap/paper_trading/db.py` — 持久化

`_SCHEMA` 追加（`CREATE TABLE IF NOT EXISTS` 幂等，老库自动补表，不影响现有 632 项测试）：

```sql
CREATE TABLE IF NOT EXISTS news_items (
    id TEXT PRIMARY KEY,            -- sha1(symbol|title|published_at)
    symbol TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT DEFAULT '',
    source TEXT DEFAULT '',
    published_at TEXT DEFAULT '',
    url TEXT DEFAULT '',
    fetched_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_items_symbol ON news_items(symbol);

CREATE TABLE IF NOT EXISTS news_verdicts (
    news_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    verdict TEXT NOT NULL,          -- genuine_bullish / fake_news / neutral / bearish
    confidence REAL NOT NULL DEFAULT 0.0,
    reasons_json TEXT DEFAULT '[]',
    impact TEXT DEFAULT '',
    rsi REAL,
    trade_action TEXT DEFAULT '',   -- buy / hold / wait / ignore
    dispatched INTEGER NOT NULL DEFAULT 0,
    decision_id TEXT DEFAULT '',    -- 追溯链：→ decisions → orders → trades
    used_fallback INTEGER NOT NULL DEFAULT 0,
    ts REAL NOT NULL,
    PRIMARY KEY (news_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_news_verdicts_symbol ON news_verdicts(symbol);
CREATE INDEX IF NOT EXISTS idx_news_verdicts_dispatched ON news_verdicts(dispatched);

CREATE TABLE IF NOT EXISTS risk_rejections (     -- 刑部式审计（B1）
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    rule_id TEXT NOT NULL,          -- R1..R5 / EXEC_LOT_BLOCK / EXEC_T1_BLOCK ...
    reason TEXT DEFAULT '',
    meta_json TEXT DEFAULT '{}',    -- plan/信号/仓位快照
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_risk_rejections_symbol ON risk_rejections(symbol);

CREATE TABLE IF NOT EXISTS news_summaries (   -- D1: 真新闻摘要哈希落盘
    summary_hash TEXT PRIMARY KEY,            -- sha1(summary)，随时可取
    symbol TEXT NOT NULL,
    title TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    source_tool TEXT DEFAULT '',              -- 本地 agents skills 工具名
    created_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_summaries_symbol ON news_summaries(symbol);
```

### 4.9 修改 `laap_brain/api.py` — 路由

| 新增 handler | 路由 | 职责 | 参考 |
|---|---|---|---|
| `handle_quant_news` | `GET /v1/quant/news?symbol=` | 最近新闻 + 判定（联表 news_items/news_verdicts） | `handle_quant_kline` 模式 |
| `handle_quant_profile` | `GET /v1/quant/profile?symbol=` | 个股资料/股票概况（含 used_fallback） | 同上 |
| `handle_quant_news_verify` | `POST /v1/quant/news/verify` | 手动触发单条新闻判定 `{symbol, title, content}` → verdict+confidence | `handle_quant_decision_record` 模式 |
| `handle_quant_news_scan` | `POST /v1/quant/news/scan` | 立即跑一次全管线 `{symbol, auto_order=true}` → pipeline result | `handle_quant_daily_cycle` 模式 |
| `handle_quant_risk_rejections` | `GET /v1/quant/risk/rejections?symbol=` | 查询风控拒绝审计（刑部） | `handle_quant_outcomes` 模式 |

- 全部注册进 `create_app` 路由表 + 首页 index 说明；
- 复用 `_get_quant_db / _get_paper_loop` 懒创建与错误处理模式（500/400 语义一致）。

### 4.10 测试（tests/）

| 文件 | 覆盖 |
|---|---|
| `tests/test_news_intel.py` | monkeypatch akshare（stub DataFrame）→ 解析/过滤/去重/缓存；`stock_individual_info_em` 失败 → cninfo 兜底 → `used_fallback=True`；全失败 → 空列表不抛 |
| `tests/test_news_verifier.py` | stub `llm_call` 返回严格 JSON → verdict/confidence 正确；<0.7 静默；RSI>70 降档、RSI<30 升档；涨停（limit_utils）→wait；LLM 抛异常 → 启发式 fallback 且不自动放行；markdown 包裹解析 |
| `tests/test_research_strategy.py` | 100 整手、≤仓位上限、**含费预算**（预算−费用后不足→hold 或降档）；评级占比≥60%+RSI≤50→now；ATR 止损/目标价止盈计算 |
| `tests/test_risk_gate.py` | R1-R5 每条规则独立触发：止损超限拒 / 单票超仓拒 / 总仓超限拒 / 单日亏损熔断拒 / 连亏3笔停开仓；拒绝落 `risk_rejections`；通过路径放行 |
| `tests/test_fees.py` | 佣金=max(5,万2.5)、印花税仅卖出、过户费、滑点双向符号；`FeeModel` 可配置 |
| `tests/test_limit_utils.py` | 涨停/跌停/停牌多信号检测（标记/up_limit 边界/OHLC 形态） |
| `tests/test_news_pipeline.py` | E2E（StubMarketSource + fake akshare + stub llm）：真利好 → signals/orders/trades/decisions 各 1 行 + decision_id 追溯 + rationale 含 `[news]` + 扣费；假消息/中性 → 0 订单但 verdict 落库；风控拒绝（如单票超仓）→ 0 订单 + risk_rejections 落库；LLM 失败 → 静默；`auto_order=False` → 只出计划不下单；现金超预算 → 二分降档 |
| `tests/test_quant_api.py`（扩展） | 5 条新路由注册 + 缺 symbol 400 + scan 端到端（stub pipeline） |
| `tests/test_news_eval.py` | **人工抽查集一致性（决策 D3）**：内置 20-50 条带人工标注的新闻样本（genuine_bullish/fake_news/neutral/bearish），跑 `verify_news` 对比 LLM 判定 vs 人工标注，输出一致率/混淆矩阵；**一致率 ≥70% 才建议开启自动下单**（作为判定质量门槛） |

### 4.11 文档

- 本文件即实施计划（随实施进度更新状态）；
- 实施完成后更新 `docs/closed-loop-implementation-checklist.md` 的阶段总览（可选）。

---

## 5. 边界与失败模式

| 场景 | 行为 |
|---|---|
| akshare / 网络失败 | 返回空 + `used_fallback=True`，管线静默（不因数据缺失乱下单） |
| LLM 不可用 / 解析失败 | keyword 启发式低置信（≤0.5）判定 + `used_fallback=True`，**默认不自动下单**（fail-closed） |
| RSI 超买（>70） | 置信度 ×0.8（追高降档），reasons 追加「追高风险」 |
| RSI 超卖（<30）+ 真利好 | 置信度 +0.05（超卖+真利好=买点） |
| **涨停（B3 多信号）** | `trade_action="wait"`，不开新仓（替代原 9.8% 近似） |
| **跌停/停牌** | 卖出/买入拒单（limit_utils） |
| **风控门拒绝（B1）** | 0 订单 + `risk_rejections` 留痕（R1-R5），静默不打扰 |
| **含费现金不足（B2/B4）** | 二分降档到最大可买整手；不足一手 → hold |
| **实时行情降级（stub/无源）** | **自动下单被拒**（fail-closed：绝不用合成价成交），仅出计划+判定留痕；实时价恢复后再触发（E2E 实证） |
| **止损位高于成交价（R1 加固）** | 计划价位与成交价不一致（长仓止损须在下方）→ R1 拒绝 |
| 重复新闻 | (symbol, title, published_at) 去重；短窗口不重复判定/下单 |
| 重复 scan | `client_request_id=decision_id` 幂等（现有 ledger 语义），不重复下单 |
| T+1 | 由 `PaperLedger.enforce_t1` 保证（当日买入不可当日卖）；B9 lot 模型为可选增强 |
| 整手/现金不足 | qty<100 或现金不足 → hold（与 `run_daily_cycle` 一致） |
| **盘前/盘后/午休（B5）** | NewsSignalWorker 非盘中时段不轮询不下单 |
| **数据陈旧（B6）** | Worker 启动校验 kline 最新日期/news 发布时间，陈旧 → 告警跳过本轮（不静默跑旧数据） |
| 测试隔离 | akshare/llm 全注入 stub；SQLite 用 tmp_path（沿用现有约定） |

---

## 6. 配置项（B7 可调参数机制）

| 配置 | 默认 | 说明 |
|---|---|---|
| `NEWS_MIN_CONFIDENCE` | `0.7` | 真利好放行门槛（对齐 DSA） |
| `MAX_STOP_LOSS_PCT` | `0.05` | 风控 R1：单笔止损距离上限（对齐金策智算） |
| `MAX_POS_PER_STOCK` | `0.10` | 风控 R2：单票仓位上限（对齐金策智算） |
| `MAX_TOTAL_POS` | `0.50` | 风控 R3：总仓位上限（对齐金策智算） |
| `MAX_DAILY_LOSS_PCT` | `0.02` | 风控 R4：单日亏损熔断（对齐金策智算） |
| `CONSECUTIVE_LOSS_LIMIT` | `3` | 风控 R5：连亏停开仓阈值（对齐金策智算） |
| `FEE_COMMISSION_RATE` / `FEE_MIN_COMMISSION` | `0.00025` / `0.0` | 佣金（单源 `costs.DEFAULT_COSTS`，最低佣金默认 0） |
| `FEE_STAMP_DUTY` | `0.0005` | 印花税，仅卖出（现行 A 股 0.05%；金策 0.1% 旧口径不采用，单源 `costs.DEFAULT_COSTS`） |
| `FEE_TRANSFER_FEE` | `0.0` | 过户费（默认 0，可配；金策 万0.1 不启用） |
| `FEE_SLIPPAGE` | `0.001` | 滑点（单源 `costs.DEFAULT_COSTS`） |
| `LAAP_NEWS_INTRADAY`（env） | 空（关） | `1` 时启用 NewsSignalWorker 盘中轮询 |
| `LAAP_NEWS_INTERVAL`（env） | `3600` | 轮询间隔秒数（默认 1 小时，勿过密；且**仅对有新新闻的标的调 LLM**，控制成本） |
| `NEWS_CACHE_TTL` | `1800` | 进程内缓存 TTL |
| `RESEARCH_SL_MULT` / `RESEARCH_TP_MULT` | `2.0` / `3.0` | ATR 止损/止盈倍数（对齐 STRATEGY_PARAMS.atr_stop_mult 语义） |
| `ORDER_QTY_MODE` | `cash_pct` | 股数模式：cash_pct（风险预算）/ fixed（固定股数，B8） |

> 落地方式：新增 `laap/paper_trading/quant_config.py`（约 40 行）——从 env 读取 + 默认值兜底，仿 `daily_pipeline` 的 env 门控风格；所有新模块 import 该 config，运行时可调。

---

## 7. 测试与回归

```
pytest tests/test_news_intel.py tests/test_news_verifier.py \
       tests/test_research_strategy.py tests/test_risk_gate.py \
       tests/test_fees.py tests/test_limit_utils.py \
       tests/test_news_pipeline.py tests/test_quant_api.py -q
pytest tests -q --ignore=tests/test_mcp_tools.py   # 回归基线 632 collected（含预存环境失败，失败数不增）
```

---

## 8. 验收命令

```bash
# 数据获取层（用户环境真实源，需联网）
python -c "
from laap.paper_trading.news_intel import fetch_stock_news, fetch_stock_profile, fetch_research_reports
news, m1 = fetch_stock_news('600519'); print('news:', len(news), m1)
prof, m2 = fetch_stock_profile('600519'); print('profile:', prof.to_dict(), m2)
reps, m3 = fetch_research_reports('600519'); print('reports:', len(reps), m3)
"

# 管线 E2E（stub 环境由测试覆盖；用户环境手动触发一次扫描，先关自动下单）
# POST /v1/quant/news/scan  {"symbol":"600519", "auto_order": false}
```

---

## 9. 明确假设（决策记录）

| # | 假设 | 依据 |
|---|---|---|
| A1 | 自动下单 = **Paper 自动执行**（本轮不做真钱下单）；只留 broker 接口缝 | 用户已确认 |
| A2 | "基本面（？）"落地为：以**个股资料/股票概况**（市值/行业/主营/股本）作为 LLM 判定的基本面上下文；不做深度财务模型 | 用户需求原文含问号，取最小可落地解释 |
| A3 | 置信度默认阈值 0.7 | 对齐 DSA `paper_trading_ai_signal_min_confidence` |
| A4 | 新闻/资料/研报源 = akshare 东方财富（与现有行情/K线/日历同源）；不引入 DSA 多供应商搜索服务（避免新增 API key 依赖） | 复用优先原则 |
| A5 | 买卖时间语义：买点 = now/pullback/wait（评级+RSI 决定）；卖点 = 研报目标价 + ATR 止盈止损价位（供日终 sell 分支与人工参照执行） | 需求"卖出最佳时间"落地为卖出价位计划 |
| A6 | `stock_research_report_em` 当前版本含评级/机构/EPS/PE 预测列，无目标价列时 `target_price_mean` 返回 None，止盈退化为 ATR 倍数 | plan 阶段实测列结构 |
| A7 | **风控门/成本模型默认不改变现有 ledger 行为**：`FeeModel` 默认 None（零成本），`RiskGate` 只在新闻管线显式挂载；保证基线 632 项测试行为不变 | 向后兼容原则 |
| A8 | B9 lot/FIFO、B10-B12（延迟监控/LLM 策略意图/业绩报表）列为**可选增强**，本轮不实施，仅记录接口位置 | 控制本轮范围 |

### 决策记录（2026-08-15 复核后用户拍板）

| # | 决策 | 依据 |
|---|---|---|
| D1 | **LLM 成本控制**：仅当标的有未判定过的新新闻才调 `llm_call`（先 `fetch_news` 过滤）；`LAAP_NEWS_INTERVAL` 默认 `3600`（勿过密）；**真新闻摘要用本地 agents skills 工具生成**（非远程 LLM），以 `sha1(summary)` 落盘 `news_summaries` 表，随时可取 | 复核建议，用户拍板 |
| D2 | **测试基线**：以 `632 collected`（含预存环境失败：test_mcp_tools 需 mcp 模块）为回归基线，失败数不增 | 复核实测（2026-08-15） |
| D3 | **判定评估机制**：内置 20-50 条人工标注抽查集，`verify_news` 一致率 ≥70% 才建议开启自动下单 | 复核建议，用户拍板 |
| D4 | **参考项目访问**：DSA（`D:\leanpython\daily_stock_analysis`）/ 金策智算（`D:\projects\jin-ce-zhi-suan`）**已获用户授权访问**（2026-08-15），计划 §2 引用的 15 个参考文件/函数已逐一核验**全部存在**（如 `MenxiaSheng.check_signal`、`HuBuRevenue.calculate_cost`、`SLTPCalculator`、`AISignalWorker`、`_is_market_session_time`）；仅借鉴思路/接口契约，不搬代码 | 用户已授权 + 核验通过 |

---

## 10. 工作量估算

| 阶段 | 内容 | 预计 |
|---|---|---|
| P0 | news_intel（数据获取层）+ limit_utils + fees + quant_config + 测试 | 1.5 人日 |
| P1 | news_verifier（LLM+RSI 判定）+ 测试 | 1 人日 |
| P2 | research_strategy（研报策略，含费预算）+ risk_gate（风控门）+ 测试 | 1 人日 |
| P3 | news_pipeline（管线 + 自动下单闭环 + Worker）+ 测试 | 1 人日 |
| P4 | db schema + api 路由 + test_quant_api 扩展 + 回归 | 0.5 人日 |
| 合计 | 9 个新文件 + 2 处修改 + 8 个测试文件，零新外部依赖 | 约 5 人日 |
