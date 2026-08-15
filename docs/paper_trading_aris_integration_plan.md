# LAAP paper_trading 接入 Aris 认知体系 — 业务实施方案（修订版 v2.0）

> 目标:让 Aris(数字生命体)成为交易业务的**一等参与者**——从"被动回答问题"
> 升级为覆盖 **学习 / 识别 / 采集 / 使用 / 决策** 五能力的交易认知闭环。
> 状态:✅ **Phase 1-3 已实施完成**（2026-08-16 起分阶段落地）
> 日期:2026-08-16 (v2.0 修订)
> 评审要点落实:pt_* 先例清理 / 认知总线枚举扩展 / 记忆双写一致性 / 命名空间决策
> 实施记录:前置(pt_* 悬空清理 `f9020bb`) → P1 感知(`91f7722`) → P2 使用(`753b0e5`) → P3 管理(`f04d1be`)

---

## 0. 评审结论与修订要点（v1.0 → v2.0）

### 0.1 评审发现的问题（均已在本版落实）

| # | 评审发现 | 严重度 | 本版处理 |
|---|---|---|---|
| R1 | `rules_defs.py` 已有 8 条 `pt_*` 规则,其中 3 条(`pt_account_show`/`pt_account_positions`/`pt_strategy_list`)**悬空引用未注册工具**;文档 v1.0 未提及此先例 | **P0** | §4.1 前置任务:清理悬空 + 命名空间决策(选项见 §2.1) |
| R2 | 认知总线 `CognitiveEventType` 现有 11 种事件,无 QUANT_* 类型;v1.0 未说明枚举扩展 | P1 | §4.2 明确 5 个新增枚举成员 + 订阅者兼容 |
| R3 | 教训双写(UnifiedMemory + 语义记忆)未定义失败语义/去重/并发 | P1 | §4.3 双写顺序 + 幂等 + 降级设计 |
| R4 | `pt_*` 触发词("查看持仓/跑回测/风控检查")与计划新增 `tool_quant_*` 高度重叠 → 第四套平行实现风险 | P1 | §2.1 命名空间决策(推荐方案 B:合并到 pt_*) |
| R5 | `tool_quant_decide` 子串匹配与 `run_command` 等宽泛规则优先级冲突 | P2 | §6 验收补充误触发回归用例 |

### 0.2 五能力定位（v2.0 核心）

```
学习  → 从每笔交易沉淀可复用认知 (教训→记忆→规则→人格)
识别  → 感知行情/信号/风险,并"意识到" (事件→认知总线→PSI)
采集  → 主动发起数据拉取 (行情/新闻/参数/净值)
使用  → 决策时调用工具/记忆/策略 (决定→审核→执行)
决策  → 交易业务的第一等参与者 (建议/执行/治理,受风控约束)
       └── 治理贯穿: 审计/进化/人工确认 (fail-closed)
```

---

## 1. 现状盘点（v2.0 补充核实）

### 1.1 paper_trading 已有能力（复用，不重造）— 复核确认

| 能力 | 位置 | 复核结果 |
|---|---|---|
| 数据模型 | `models.py` | ✅ Signal/Order/Trade/DecisionRecord/OutcomeRecord |
| SQLite 存储 | `data/paper_trading.db` | ✅ 10 表（signals/orders/trades/net_values/decisions/outcomes/evolutions/news_items/news_verdicts/risk_rejections/news_summaries） |
| 决策闭环 | `paper_service.py::PaperClosedLoop` | ✅ `decide_and_trade → close_and_learn → settle → run_daily_cycle` |
| 交易自我 | `trading_self.py::TradingSelf` | ✅ `judge(action,symbol,qty,...)` L225 / `issue()` L396 |
| 记忆桥 | `memory_bridge.py` | ✅ `encode_lesson(memory, outcome, symbol)` / `lesson_to_experience` / `retrieve_for_symbol` |
| 参数进化 | `param_evolver.py` + `quant_evolution.py` | ✅ 网格→随机→遗传，seed 固定 |
| 新闻情报 | `news_pipeline/intel/verifier.py` | ✅ 采集+验证+摘要（哈希落盘） |
| 行情源 | `kline_source/market_source/data_sources.py` | ✅ 腾讯 fqkline / akshare |
| API | `laap_brain/api.py /v1/quant/*` | ✅ 20+ 端点（decisions/lessons/evolve/evolve_params/self/status/daily_cycle/apply_params/approve/reject/audit/trades/net_values/signals/orders/outcomes/kline/news/profile/risk/rejections） |
| 日终调度 | `daily_pipeline.py::QuantDailyScheduler` | ✅ 交易日历 + 定时闭环 |
| 契约单源 | `costs.DEFAULT_COSTS` / `trade_fitness.FITNESS_WEIGHTS` / `quant_config` | ✅ 符合 AGENTS.md |

### 1.2 Aris 认知体系已有能力（接入基座）— 复核确认

| 能力 | 位置 | 复核结果 |
|---|---|---|
| 规则引擎 | `aris_brain/aris_rules_engine.py`（薄门面）+ `rules_tools.py`(34 工具) + `rules_defs.py`(30 规则) | ✅ R11 拆分后模块化 |
| **pt_* 量化规则先例** | `rules_defs.py` 8 条 `pt_*` 规则 | ⚠️ **其中 3 条悬空**（见 R1） |
| 工具路由 | `laap/agi/tool_router.py` | ✅ quote/ohlcv/portfolio/capital/news/intel/backtest/sector/watchlist 等 18 工具 |
| 语义记忆 | `aris_brain/laap_semantic_memory.py::LaapSemanticMemory` | ✅ `add(text, meta)` L480 / `recall(query, top_k, min_score)` L505 |
| 认知总线 | `laap/agi/cognitive_bus.py::CognitiveBus` | ✅ `publish(event_type, source, payload)` L379 / `subscribe(module, event_type, cb)` L360 |
| PSI 意识 | `psi_jspace_bridge/` | ✅ 需求/情感/唤醒/循环 |
| 潜意识 | `aris_subconscious.py::QuantumSubconscious` | ✅ V12.5 引擎，5s 后台线程 |
| LLM 兜底 | Agnes-2.5-flash(cpk- key) | ✅ 开放话题 |

### 1.3 诚实基线（保持 v1.0 结论）

- 真实 A 股 OOS 回测通过率 10-20%，**无泛化 alpha**——系统定位"受控模拟环境功能验证"
- 记忆桥与 TradingSelf **已存在**，不是从零开始
- 用户立场：拒绝把负结果包装成"实证通过"

---

## 2. 总体架构与关键决策（多选项 + 推荐）

### 2.1 决策点 D1：工具命名空间 — quant_\* vs pt_\*（🔴 必须先定）

**背景**：`rules_defs.py` 已有 8 条 `pt_*` 规则（触发词"查看持仓/跑回测/风控检查/绩效报告/账户列表"），
与 v1.0 计划新增的 `tool_quant_*` 语义重叠。若直接新增 = 第四套平行实现（违反契约单源）。

| 选项 | 描述 | 优点 | 缺点 | 评级 |
|---|---|---|---|---|
| **A. 并存双命名空间** | 保留 `pt_*`（旧），新增 `quant_*`（新） | 不动现有规则 | 触发词重叠冲突、双维护 | ❌ |
| **B. 合并到 pt_\*（推荐）** | 清理 3 条悬空工具，补齐 `pt_*` 工具实现，v2.0 只扩展现有 pt_* 系列 | 单一事实源、无冲突、复用已有触发词 | 需先修悬空引用 | ✅ **推荐** |
| C. 合并到 quant_\* | 废弃 pt_* 改名 quant_* | 命名更清晰 | 破坏既有触发词、需迁移规则 | 🟡 |

**推荐 B**：`pt_*` 已是事实标准（8 条规则 + 触发词已定），v2.0 在 pt_* 上**扩展**：
- 新增 `pt_lessons` / `pt_signals` / `pt_net_value` / `pt_risk_events` / `pt_brief` / `pt_evolution_audit` / `pt_decide` / `pt_execute` / `pt_close`
- 命名：`pt_` 前缀 = paper trading，语义清晰且延续先例

### 2.2 决策点 D2：认知注入强度（用户已确认）

| 选项 | 描述 | 结论 |
|---|---|---|
| A. 只影响情绪 | valence/arousal 变化 | ✅ **已确认**（不动 competence/certainty） |
| B. 全维度影响 | 含能力判断 | ❌ 用户否决 |

### 2.3 决策点 D3：执行边界（用户已确认）

| 选项 | 描述 | 结论 |
|---|---|---|
| A. 只建议不执行 | auto_execute=false + 二次确认 | ✅ **已确认** |
| B. 自动执行 | 无人值守下单 | ❌ 用户否决（fail-closed 违背） |

### 2.4 总体架构（目标态，v2.0）

```
                    ┌─────────────────────────────┐
                    │         Aris 认知层           │
                    │  (规则引擎 pt_* / 工具路由 /   │
                    │   PSI / 语义记忆)              │
                    └──────────┬──────────────────┘
                               │ ① 扩展 pt_* 工具 + 事件源
                               ▼
┌──────────────────────────────────────────────────────┐
│              quant_bridge 桥接层 (新, 薄)              │
│  - learning: outcome→教训→双写(UnifiedMemory+语义记忆) │
│  - sensing:  事件→cognitive_bus→PSI(限情绪)           │
│  - fetching: kline/news/net_value 拉取指令             │
│  - using:    decide→记忆注入→TradingSelf.judge→下单    │
│  - governing:状态快照/审计/进化治理报告                 │
└──────────────────┬───────────────────────────────────┘
                   │ ② 复用现有 API (不改签名)
                   ▼
┌──────────────────────────────────────────────────────┐
│         paper_trading 现有服务 (不动核心契约)           │
│  PaperClosedLoop / TradingSelf / QuantEvolution      │
│  PaperDB (10表) / QuantDailyScheduler / costs.py      │
└──────────────────────────────────────────────────────┘
```

**设计原则**：最小改动 / 复用既有契约 / 单向依赖（服务层不知道 Aris 存在）/ fail-closed 贯穿。

---

## 3. 五能力架构展开

### 3.1 学习（Learn）— 教训 → 认知沉淀

```
OutcomeRecord ──encode_lesson──→ UnifiedMemory (已有)
                    │
                    └──新: 双写──→ LaapSemanticMemory.add(text="【交易教训】...", meta={symbol, type})
                                      │
                                      ├→ Aris 规则引擎 recall_fact_rule 可召回
                                      └→ 触发词"记得交易上吃过什么亏吗" → pt_lessons
```

**双写设计**（落实 R3）：
- 顺序：先写 UnifiedMemory（主，已有语义），成功后写语义记忆（从，Aris 侧）
- 失败降级：主写失败 → 记日志不中断（fail-closed：不因记忆失败影响交易）；从写失败 → 主写已成功，下次 encode 幂等补写
- 幂等：以 `outcome.id` 为去重键，语义记忆 meta 带 `dedup_key`，`add` 前查重
- 并发：语义记忆 JSON 写用 `threading.Lock`（模块级），与 cron 快照共用锁

### 3.2 识别（Sense）— 事件 → 意识

```
paper_trading 事件 ──quant_bridge.sense──→ CognitiveBus.publish(
    event_type=QUANT_*, source="paper_trading", payload={...})
        │
        ├──→ Aris 下一轮对话感知（前缀注入）
        └──→ PSI 情绪更新（限 valence/arousal, 用户已确认）
```

**新增枚举**（落实 R2）：
```python
# laap/agi/cognitive_bus.py :: CognitiveEventType 追加
QUANT_SIGNAL = "quant_signal"               # 新信号产生
QUANT_TRADE_CLOSED = "quant_trade_closed"   # 平仓(含止盈止损)
QUANT_RISK_TRIGGERED = "quant_risk_triggered"  # 风控拒绝
QUANT_DAILY_SETTLE = "quant_daily_settle"   # 日终结算
QUANT_EVOLUTION_PROPOSED = "quant_evolution_proposed"  # 进化提案
```
- 兼容性：现有订阅者按 event_type 过滤，新增类型不影响；`tick()` 聚合时新事件自然流入
- 防刷屏（v1.0 §6 风险）：quant_bridge 侧按事件类型聚合，每类只保留最新 N=5 条

### 3.3 采集（Fetch）— 主动拉取

```
Aris 规则触发 → pt_fetch_* 工具 → quant_bridge.fetch → 复用 /v1/quant/kline|news|net_values
```

| 工具 | 数据 | 复用端点 |
|---|---|---|
| `pt_fetch_kline` | 真实K线 | GET /v1/quant/kline?symbol=&days= |
| `pt_fetch_news` | 新闻判定 | GET /v1/quant/news?symbol= |
| `pt_fetch_netvalue` | 净值序列 | GET /v1/quant/net_values |
| `pt_fetch_profile` | 个股资料 | GET /v1/quant/profile?symbol= |

- 全部只读、无副作用；失败返回空 + `used_fallback=True`（符合 AGENTS.md 3.1）

### 3.4 使用（Use）— 决策时调用

```
用户: "帮我看下600519要不要买"
  → Aris 规则 pt_decide 触发
  → quant_bridge.use.decide(symbol="600519", action="buy", qty=100, rationale=...)
  → 记忆注入: retrieve_for_symbol(symbol) → 历史教训上下文
  → TradingSelf.judge(action, symbol, qty, ...) → 审核
  → 返回建议(带 judge 痕迹), 不下单
用户: "执行"
  → pt_execute 触发 → 需 judge 通过 + 二次确认词("确认执行")
  → PaperClosedLoop.decide_and_trade(...) → 落 decisions/orders 表
```

- 完全复用 `PaperClosedLoop.decide_and_trade` 签名，Aris 只提供 `(symbol, action, qty, rationale)`
- 全部操作写 `risk_rejections` 审计表（已有）

### 3.5 决策（Decide）— 一等参与者

决策不是"代下单"，而是**参与决策过程**：
- **建议层**：pt_decide 给出带记忆/风控上下文的建议（默认，auto_execute=false）
- **审核层**：TradingSelf.judge 一票否决（风控门 R1-R5）
- **执行层**：二次确认后才下单（用户已确认边界）
- **治理层**：进化提案 pt_evolution_audit → 用户 approve/reject → 复用 /v1/quant/evolve/approve|reject
- **报告层**：pt_brief 每日复盘（净值/盈亏/教训/明日关注）

---

## 4. 分阶段实施（文件+函数级）

### 4.1 前置任务：pt_* 清理（0.5 天）【必须先做，落实 R1/R4】

| 文件 | 改动 |
|---|---|
| `aris_brain/rules_tools.py` | ① 补注册 3 个缺失工具：`pt_account_show`(读单账户详情)、`pt_account_positions`(读未平仓持仓)、`pt_strategy_list`(列策略)——每个工具函数 + `register_default_tools` 列表追加 |
| `aris_brain/rules_defs.py` | ② 复核 8 条 pt_* 规则的 tool 引用完整性；③ 确认触发词与新增能力不冲突 |

**验收**：`python -c "from aris_brain.rules_defs import DEFAULT_RULES; from aris_brain.rules_tools import register_default_tools; from aris_brain.rules_defs import ToolRegistry; reg=ToolRegistry(); register_default_tools(reg); assert all(s.tool in reg.list() for r in DEFAULT_RULES for s in r.steps)"` 通过（零悬空）。

### 4.2 Phase 1：感知接入（学习 + 识别）2-3 天

#### 4.2.1 扩展 pt_* 只读工具（规则引擎侧）

`aris_brain/rules_tools.py` 新增（基于决策点 D1-方案 B，pt_* 命名空间）：

| 工具 | 数据来源 | 说明 |
|---|---|---|
| `pt_lessons` | GET /v1/quant/lessons | 交易教训（触发："学到什么/有什么教训"） |
| `pt_signals` | GET /v1/quant/signals | 最近信号（触发："最近信号/交易信号"） |
| `pt_net_value` | GET /v1/quant/net_values | 净值/盈亏（触发："赚了还是亏了/净值"） |
| `pt_risk_events` | GET /v1/quant/risk/rejections | 风控事件（触发："被风控拦过吗/拒绝记录"） |
| `pt_portfolio` | DB trades 未平仓 | 持仓（触发："当前持仓/仓位"） |

`aris_brain/rules_defs.py` 新增 5 条规则（pattern 覆盖口语变体），
沿用 pt_ 命名：`pt_lessons_rule` / `pt_signals_rule` / `pt_net_value_rule` / `pt_risk_events_rule` / `pt_portfolio_rule`。

#### 4.2.2 认知总线事件注入（识别）

`laap/agi/cognitive_bus.py`：
- `CognitiveEventType` 追加 5 个 QUANT_* 成员（见 §3.2）
- 确认 `publish` 与 `tick` 对新类型无副作用（事件聚合自然生效）

**新增 `laap/paper_trading/quant_bridge.py`（薄桥接层，首个函数）**：
```python
def sense_event(loop, event_type, payload):
    """平仓/风控/结算等事件 → CognitiveBus.publish + PSI 情绪更新。"""
    # 1. publish 到认知总线 (事件类型映射 QUANT_*)
    # 2. PSI: 盈利→valence+ / 亏损→valence- / 风险→arousal+ (限情绪维度)
    # 3. 事件聚合: 每类保留最新 5 条, 防刷屏
```

#### 4.2.3 教训双写（学习增强）

`laap/paper_trading/memory_bridge.py`：
```python
def encode_lesson(memory, outcome, symbol=""):
    # 已有: 主写 UnifiedMemory
    # 新增: 双写 LaapSemanticMemory (带 dedup_key=outcome.id, threading.Lock)
    #   标签【交易教训】+ symbol + lesson_type; 失败降级: 主写成功即可
```

**产出**：问 Aris"记得交易上吃过什么亏吗" → 语义记忆召回真实教训。

#### 4.2.4 P1 验收

```
1. "最近交易怎么样" → engine: rules:pt_net_value_rule, 内容含真实净值
2. "有什么交易教训" → 召回真实教训(含标的/类型)
3. 模拟平仓 → 认知总线 QUANT_TRADE_CLOSED → 下一轮 Aris 主动提及
4. 语义记忆含【交易教训】条目, recall 可命中
5. (评审补充) pt_* 悬空工具已清理, 零悬空断言通过
6. (评审补充) 双写失败降级: 语义记忆写失败时主写仍成功, 不中断
```

### 4.3 Phase 2：使用接入（使用 + 决策核心）3-4 天

#### 4.3.1 动作工具（带审核）

`aris_brain/rules_tools.py` 新增 3 个动作工具：

| 工具 | 行为 | 安全机制 |
|---|---|---|
| `pt_decide` | 发起决策(buy/sell/hold + rationale) | `TradingSelf.judge()` 审核 + 记忆注入 |
| `pt_execute` | 确认下单(审核通过后) | judge 通过 + 二次确认词 |
| `pt_close` | 平仓 | judge + 风控检查 |

`aris_brain/rules_defs.py` 新增规则：`pt_decide_rule`（触发"帮我看下X要不要买"）、`pt_execute_rule`（触发"执行"）、`pt_close_rule`（触发"平仓/卖出"）。

`laap/paper_trading/quant_bridge.py` 扩展：
```python
def use_decide(symbol, action, qty, rationale):
    """Aris 决策请求 → 记忆注入 → TradingSelf.judge → 返回建议(不下单)。"""
def use_execute(decision_id, confirm_word):
    """二次确认后 → PaperClosedLoop.decide_and_trade 下单。"""
def use_close(symbol, qty, confirm_word):
    """平仓: judge + 风控 → PaperClosedLoop 执行。"""
```

#### 4.3.2 LLM 微调闭环确认（已有，接通）

`laap_brain/api.py`：确认 `_get_llm_refine_fn()` 在服务启动时注入正常（检查依赖），
使参数进化走 LLM 增强路径，Aris 可对进化提案给"交易员视角"意见。

#### 4.3.3 P2 验收

```
1. "帮我看下600519要不要买" → pt_decide → 建议(带 judge 审核痕迹)
2. 用户确认"确认执行" → pt_execute → 下单 → decisions/orders 表有记录
3. 无确认时 → 只建议不下单(安全)
4. risk_rejections 有完整审计
5. (评审补充) 误触发回归: "五粮液的历史行情" 不触发 pt_decide 落单
```

### 4.4 Phase 3：管理闭环（决策治理 + 报告）2-3 天

#### 4.4.1 每日交易简报规则

`aris_brain/rules_defs.py` + `rules_tools.py`：
- `pt_brief` 工具 + `pt_brief_rule`（触发"今日交易简报/今天交易怎么样"）
- 读 net_values + outcomes + lessons → 结构化简报（净值/盈亏/教训/明日关注）

#### 4.4.2 进化治理接入

- `pt_evolution_audit` 工具 + `pt_evolution_rule`（触发"进化提案/策略改进"）
- 读 `/v1/quant/evolve/audit` → 列待批提案 → 用户决定 → 调 approve/reject

#### 4.4.3 日终自动认知快照（cron）

- 新增 `~/AppData/Local/hermes/scripts/memorize_trading_daily.py`（复用 `memorize_market_daily.py` 模式）
- 每日 15:30：拉取 quant 状态 → 写语义记忆 `【交易日报 YYYY-MM-DD】...`
- 与教训双写共用 `threading.Lock`，避免并发写语义记忆 JSON

#### 4.4.4 P3 验收

```
1. "今日交易简报" → 结构化复盘(净值/盈亏/教训)
2. "看下进化提案" → Aris 列提案 → 批准/拒绝生效
3. cron 15:30 写入交易日报 → 次日 Aris 跨日感知("昨天亏了,今天谨慎些")
```

---

## 5. 文件级改动清单（v2.0 汇总）

| 文件 | 改动 | 阶段 | 契约影响 |
|---|---|---|---|
| `aris_brain/rules_tools.py` | 前置:补 3 悬空工具; P1:+5 只读工具; P2:+3 动作工具; P3:+2 报告工具 | 前置/P1/P2/P3 | 无(纯新增) |
| `aris_brain/rules_defs.py` | P1:+5 规则; P2:+3 规则; P3:+2 规则 | P1/P2/P3 | 无(纯新增) |
| `laap/agi/cognitive_bus.py` | `CognitiveEventType` +5 枚举成员 | P1 | 低(枚举扩展,订阅者按类型过滤) |
| `laap/paper_trading/quant_bridge.py` | **新增**: sense_event / use_decide / use_execute / use_close / fetch_* | P1/P2 | 无(新模块) |
| `laap/paper_trading/memory_bridge.py` | `encode_lesson` 双写 + dedup + 锁 | P1 | 低(签名不变,内部增强) |
| `laap_brain/api.py` | 确认 `_get_llm_refine_fn` 注入正常 | P2 | 无 |
| `~/AppData/Local/hermes/scripts/` | +`memorize_trading_daily.py` cron 副本 | P3 | 无(git 外) |
| 配置 | +`paper_trading_auto_execute=false` 等开关 | P2 | 需同步 .env.example |

**明确不动**（契约漂移风险，AGENTS.md 硬约束）：`models.py` / `db.py` /
`paper_service.py` 核心签名 / `trading_self.py` / `param_evolver.py` /
现有 `/v1/quant/*` 端点行为 / `costs.py::DEFAULT_COSTS` / `trade_fitness.py::FITNESS_WEIGHTS`。

---

## 6. 验证方案（每阶段 + 评审补充用例）

### 通用回归（每阶段必跑）
```bash
TMPDIR=/tmp python -m pytest tests -q                    # 全量(沙箱挂载盘需TMPDIR)
TMPDIR=/tmp python -m pytest tests/test_news_*.py tests/test_quant_api.py tests/test_ledger_fees.py -q
```

### 前置任务验收
```bash
python -c "from aris_brain.rules_defs import DEFAULT_RULES; from aris_brain.rules_tools import register_default_tools; from aris_brain.rules_defs import ToolRegistry; reg=ToolRegistry(); register_default_tools(reg); assert all(s.tool in reg.list() for r in DEFAULT_RULES for s in r.steps)"
# 通过 = 零悬空
```

### P1/P2/P3 验收
见 §4.2.4 / §4.3.3 / §4.4.4（含评审补充用例：双写降级 / 误触发不落单 / 零悬空）。

---

## 7. 风险与边界（v2.0 更新）

| 风险 | 等级 | 缓解 |
|---|---|---|
| 负 alpha 现实 | 高 | 保持"模拟验证"定位,不承诺收益;Aris 如实汇报(诚实基线) |
| 自动下单失控 | 高 | auto_execute=false + 二次确认 + TradingSelf 审核(fail-closed) |
| **pt_\* 悬空工具**（评审发现） | **高(前置)** | §4.1 前置清理 + 零悬空断言入测试 |
| **平行实现/契约漂移**（评审发现） | **高(前置)** | 决策点 D1 方案 B 合并命名空间,禁止第四套 quant_\* |
| 规则引擎子串匹配误触发交易工具 | 中 | 动作工具 require 明确触发词 + 审核 + 误触发回归用例 |
| 认知总线事件刷屏 | 中 | 事件聚合(每类最新 N=5) |
| 记忆双写分叉/并发 | 中(评审补充) | dedup_key + 失败降级 + threading.Lock |
| cron 与主流程并发写 DB | 低 | SQLite WAL + 现有锁机制 |
| NAS 同步覆盖本地规则引擎修改 | 中 | 及时 commit+push(已有教训) |

---

## 8. 决策点汇总（更新）

| # | 决策点 | 状态 |
|---|---|---|
| D1 | 工具命名空间: **B 合并到 pt_\***（清理悬空后扩展） | 🆕 待确认(推荐 B) |
| D2 | 认知注入强度: 只影响情绪(用户已确认) | ✅ 已确认 |
| D3 | 执行边界: 只建议不自动下单(用户已确认) | ✅ 已确认 |
| D4 | 数据范围: 保持 3 标的(600519/000001/000858) | ✅ 已确认 |
| D5 | 简报频率: 每日 15:30 cron | ✅ 已确认 |

---

## 9. 实施顺序建议（更新）

```
前置 (pt_* 清理) ──→ P1 (感知) ──→ P2 (使用+决策) ──→ P3 (治理+报告)
   0.5 天            2-3 天         3-4 天            2-3 天
```

每阶段完成 → 验收(含评审补充用例) → 用户确认 → 进入下一阶段。
前置任务不涉及资金动作，风险最低，**必须先做**（否则 P1 的 pt_* 工具在悬空引用下会静默失败）。

---

## 10. 执行状态（2026-08-16 全部完成）

| 阶段 | 状态 | commit | 交付 |
|---|---|---|---|
| 前置: pt_* 悬空清理 | ✅ | `f9020bb` | 3 悬空工具补齐 + 零悬空契约测试 |
| P1 感知（学习+识别） | ✅ | `91f7722` | pt_lessons/signals/net_value/risk_events 工具+规则、QUANT_* 事件、教训双写 |
| P2 使用（使用+决策） | ✅ | `753b0e5` | pt_decide/execute/close 动作工具（judge 审核+二次确认+fail-closed）、参数提取 |
| P3 管理（治理+报告） | ✅ | `f04d1be` | pt_brief 简报、pt_evolution_audit 治理、日终认知快照 cron |
| 收尾: cron 注册+配置+文档 | ✅ | 本轮 | Windows 计划任务 15:30 工作日、PAPER_TRADING_AUTO_EXECUTE=0 |

**测试基线**: 765 → **800 passed / 0 failed**（Phase 1-3 共 +35 项测试）。

**运行环境**:
- cron: `LAAP_Memorize_Trading_Daily` 计划任务（工作日 15:30，调 hermes scripts/memorize_trading_daily.py）
- 执行边界: `PAPER_TRADING_AUTO_EXECUTE=0`（只建议不下单，fail-closed 默认；二次确认词仍为硬门槛）
- 语义记忆: 教训双写 + 交易日报共用写锁

---

*本文档为实施完成稿（Phase 1-3 全部落地）。*
*设计原则:最小改动、复用既有契约、单向依赖、诚实定位、fail-closed 贯穿。*
