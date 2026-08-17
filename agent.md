# daily_stock_analysis 策略体系说明（agent.md）

> 本文件面向 Agent / 开发者，集中说明本仓库的策略全景、每个策略的执行逻辑、
> 策略执行链路、规则引擎与回测引擎。文档与代码如有出入，以代码为准。

---

## 1. 策略全景

项目包含 **三层策略体系**：

| 层 | 名称 | 数量 | 定义位置 | 运行方式 |
| --- | --- | --- | --- | --- |
| ① LLM 交易策略（Skills） | 个股/单标的分析策略 | 15 | `paper_trading/strategies/configs/*.yaml` | LLM Agent 按自然语言指令分析（+ 可回测） |
| ② 市场复盘策略蓝图 | 大盘复盘框架 | 5 | `src/core/market_strategy.py` | 注入每日大盘复盘 prompt |
| ③ 程序化回测模板 | 确定性规则策略 | 4 | `paper_trading/strategies/engine/templates.py` | 规则引擎 + 回测引擎纯代码执行 |

其中 ① 的 YAML 定义同时被两套系统消费：

- **LLM 分析**：`src/agent/skills/base.py` 加载成 `Skill`，指令注入系统 prompt；
- **程序化回测**：`paper_trading/strategies/engine/`（schema / indicators / rule_engine）
  将其转换为确定性 buy/sell 规则（回测模板见 ③）。

---

## 2. ① LLM 交易策略（Skills）明细与执行逻辑

每个策略 YAML 的公共字段：

```yaml
name: 唯一 id（如 bull_trend）
display_name: 中文展示名
description: 一句话适用场景
category: trend | pattern | reversal | framework   # 分类
core_rules: [1..7]          # 关联的基础风控理念编号（见 2.1）
required_tools: [...]       # 依赖的工具名（get_daily_history / analyze_trend / ...）
aliases: [中文别名]          # 供 NL 选择器 / 机器人命令匹配
default_active / default_router / default_priority / market_regimes  # 激活与路由元数据
instructions: |             # 核心：自然语言分析指令（注入 LLM prompt）
```

### 2.1 基础风控理念（core_rules 引用编号）

1. **严进策略（不追高）**：偏离 MA5 > 5% 坚决不买；乖离率 <2% 最佳、2-5% 小仓、>5% 观望
2. **趋势交易（顺势而为）**：只做 MA5 > MA10 > MA20 多头排列
3. **效率优先（筹码结构）**：90% 筹码集中度 <15%、获利盘 70-90% 警惕回吐
4. **买点偏好（回踩支撑）**：缩量回踩 MA5 最佳 / MA10 次优 / 破 MA20 观望
5. **风险排查**：减持、业绩预亏、监管处罚、政策利空、大额解禁
6. **估值关注**：PE 明显偏高需在风险点说明
7. **强势趋势股放宽**：可适当放宽乖离率，轻仓追踪并设止损

### 2.2 各策略执行逻辑

#### 📈 趋势类（trend）

**bull_trend 默认多头趋势**（priority 10，默认激活，`[trending_up]`）
- 执行逻辑：先用 `analyze_trend` 确认 MA5≥MA10≥MA20 且 MA20 斜率向上为多头结构；
  再看价格位置（回踩不破优先、远离均线提示等待）；用 `get_daily_history` 验证量价
  （放量突破加分、缩量上涨谨慎）；最后输出 买入/观望/减仓 倾向 + 止损位（MA20 或结构低点）。
- 评分：多头排列 +12；回踩企稳 +8；放量突破 +10；跌破 MA20 −12。

**ma_golden_cross 均线金叉**（priority 20，`[trending_up]`）
- 执行逻辑：检测 MA5 近 3 日内上穿 MA10（主信号）或 MA10 上穿 MA20（强信号），
  配合 MACD 零轴上方金叉；要求金叉日成交量 > 5 日均量、量比 >1.2；按趋势背景分级
  （盘整后金叉最强、深跌中金叉需确认）；乖离率 <5% 才允许入场。
- 评分：MA5×MA10 金叉 +10；MA10×MA20 金叉 +8；MACD 零轴上方金叉额外 +5。

**volume_breakout 放量突破**（priority 30，`[trending_up]`）
- 执行逻辑：用 `analyze_trend` 定位阻力位（20 日高点 / 平台顶）；要求当日量 > 5 日均量
  2 倍、量比 >2.0；收盘站上阻力位且位于当日振幅上方 30%（强势收盘）；
  突破后乖离率仍须 <5%；有数据时验证次日开盘在突破位之上区分真假突破；
  通过 `search_stock_news` 过滤重大利空、PE 过高。
- 评分：放量突破确认 +12；板块共振额外 +5。买点=突破位附近，止损=突破位下方 3%。

**shrink_pullback 缩量回踩**（priority 40，默认路由，`[trending_down, sideways]`）
- 执行逻辑：前提是上升趋势（MA5>MA10>MA20）；检测价格回踩 MA5（±1%）或 MA10（±2%）
  且回调量 < 5 日均量 70%；当前价格守住均线支撑且 MA5 乖离率 <2% 为最佳买点；
  用 `search_stock_news` 确认无利空、筹码获利比例 50-80%。
- 评分：缩量回踩 MA5 +10；回踩 MA10 且量 <0.6 倍均量 +8。买点=MA5/MA10，止损=MA20。

**dragon_head 龙头策略**（priority 90，`[sector_hot]`）
- 执行逻辑：用 `get_sector_rankings` 确认板块领涨地位、个股率先启动/涨停；
  用 `get_realtime_quote` 检查换手率 >5%、量比 >1.5；对比个股涨幅跑赢板块平均 2%+；
  `search_stock_news` 找板块级催化剂；龙头股乖离率可放宽至 7%（>10% 仍谨慎）。
- 评分：确认为龙头 +10；板块主动轮动期额外 +5。

#### 📊 形态类（pattern）

**one_yang_three_yin 一阳夹三阴**（priority 110，无 regime 标签）
- 执行逻辑：取最近 5 日 K 线验证形态——第 1 日大阳线（实体 >2%）、第 2-4 日连续缩量
  小阴线（不破第 1 日开盘价、收在第 1 日实体范围内、量比 <0.8）、第 5 日阳线突破第 1 日
  收盘价；再用 `analyze_trend` 确认多头排列。
- 评分：形态成立 + 趋势看多 +15；形态成立但趋势不明 +5。买点=第 5 日收盘附近，
  止损=第 1 日开盘价下方。

#### 🔄 反转类（reversal）

**bottom_volume 底部放量**（priority 60，`[trending_down]`）
- 执行逻辑：确认 30 日累计跌幅 >15%、trend_status 为 BEAR/STRONG_BEAR；要求当日量 >
  5 日均量 3 倍且出现在前期极度缩量之后；当日收阳、守住近期低点、最好长下影；
  `search_stock_news` 确认基本面催化、筹码成本收敛。
- 风险：反转信号风险高于趋势跟踪，仓位 ≤2-3 成，止损设在近期低点下方。
- 评分：底部放量确认 +8；阳线 + 新闻催化额外 +5。

#### 🧩 框架类（framework）

**hot_theme 热点题材**（priority 35，`[sector_hot]`）
- 执行逻辑：用 `get_sector_rankings` 判断板块强度与扩散（单股异动而板块未共振则降权）；
  用 `search_stock_news` 核对个股业务/订单/客户与热点是否实质相关（区分实质受益/间接受益/
  概念关联弱）；用 `get_realtime_quote` + `analyze_trend` 比较个股相对板块的强弱；
  不在连续加速、高乖离位置追涨，监管问询/澄清公告一票降级。
- 评分：热点启动/扩散期且实质受益 +12；强于板块 +6；分化/退潮 −8；纯蹭概念且高乖离 −12。

**event_driven 事件驱动**（priority 45，`[sector_hot, volatile]`）
- 执行逻辑：`search_stock_news` 梳理事件并分类（业绩/政策/订单产品/资本运作/监管风险），
  排除过期信息；判断影响路径（收入/利润率/估值/融资/情绪）与兑现周期；用实时行情判断
  价格是否已充分反映（放量未破阻力等待、高位放量滞涨警惕兑现）；输出事件性质、
  可信度、兑现周期、已反映程度，操作建议必须含失效条件。
- 评分：高可信正向事件且未充分反映 +14；已大幅兑现 −6；负面发酵 −15；信息冲突维持中性降置信度。

**box_oscillation 箱体震荡**（priority 50，`[sideways]`）
- 执行逻辑：取 60~120 日数据识别箱顶/箱底（各触碰 2-3 次有效）；判断现价区间——
  箱底（距支撑 ≤5%）买入/加仓、箱中观望、箱顶（距阻力 ≤5%）减仓/止盈；量能辅助
  （箱底放量企稳强信号、箱顶缩量滞涨卖出信号）；宽度 <5% 不参与；连续两日收盘突破 +
  放量视为真突破并切换策略。
- 评分：箱底企稳缩量 +10；箱底放量攻顶 +12；向上有效突破 +15 转趋势策略；箱顶 −5；
  箱底有效跌破 −15 离场。

**growth_quality 成长质量**（priority 55，`[trending_up]`）
- 执行逻辑：优先看财报字段（营业收入/归母净利润/经营现金流/ROE），判断收入与利润是否
  同向（警惕增收不增利）；ROE 高且稳定、现金流与净利润方向一致为高质量；
  用 PE/PB/市值判断估值是否透支成长；用 `analyze_trend` 确认成长逻辑是否被资金确认
  （基本面好但技术未确认则给观察条件）。
- 评分：收入/利润/现金流/ROE 同向改善 +15；行业景气与新闻互证额外 +6；高估值但成长
  未验证 −8；增收不增利或现金流恶化 −12。

**expectation_repricing 预期重估**（priority 65，`[volatile, sector_hot]`）
- 执行逻辑：`search_stock_news` 识别改变预期的信息并区分硬信息（公告/财报/订单）与
  软信息（传闻/观点）；判断预期差方向（正向修复/负向落空/已被大涨兑现）；用 PE/PB/ROE/
  现金流验证估值重估是否有基本面支撑；用 `analyze_trend` 判断预期是否转化为趋势
  （放量突破=资金确认、缩量反弹=修复观察、利好不涨或破位=预期转弱）。
- 评分：正向预期差且未充分反映 +15；已被连续大涨兑现 −5；负向预期差/核心假设证伪 −15。

**chan_theory 缠论**（priority 70，`[volatile]`）
- 执行逻辑：取 60 日日线识别分型→笔→线段→中枢（连续 3 段重叠区间）→趋势；最高优先级
  信号为背驰（顶背驰=价格新高但 MACD 红柱面积缩小→卖出；底背驰相反→买入）；判定买卖点
  一买/二买/三买（及对称的一卖/二卖/三卖）；按级别定仓位（日线 30-50%、周线 50-80%、
  多级别共振最强）；止损设前低（买）/前高（卖）。
- 评分：底背驰 + 一买 +15；二买/三买共振 +10；中枢震荡维持基准；顶背驰/趋势向下 −15。

**wave_theory 波浪理论**（priority 80，`[volatile]`）
- 执行逻辑：取近 120 日数据识别 5 浪推动（1 浪反转温和放量、3 浪最强放量 MACD 强势、
  5 浪量能减弱可能顶背离）+ 3 浪调整（A 浪放量下跌、B 浪缩量反弹陷阱、C 浪超 A 浪）；
  用斐波那契定位（2 浪回调 38.2-61.8%、3 浪目标 1.618-2.618 倍、4 浪不得入 1 浪区域、
  C 浪 ≥A 浪）；最优买点=2 浪回调企稳（黄金坑，止损 1 浪起点）；B 浪不重仓；
  输出浪型位置、关键斐波位、置信度。
- 评分：2 浪底部企稳 +15；3 浪突破确认 +12；5 浪末端/顶背离 −10；C 浪下跌 −12。

**emotion_cycle 情绪周期**（priority 100，`[sector_hot]`）
- 执行逻辑：用换手率量化情绪热度（<0.5%/日 冷淡底部、2-5% 活跃、>5% 警惕顶、
  >10% 极度过热）；分析近 20 日换手率走势（由高向低+缩量=退潮等待、由低向高+放量=启动、
  单日暴量超 5 倍=主力出货警惕）；用新闻情绪面（利好兑现/机构推荐=过热、利空悲观=底部）；
  用均线收缩（三线粘合=蓄势）与 ATR 萎缩判断蓄势；情绪底部特征 ≥3 项买入、顶部特征 ≥3 项减仓。
- 评分：底部特征 ≥3 项 +14、全部 5 项 +20；顶部特征 ≥3 项 −12、全部 5 项 −20。

---

## 3. 策略执行链路（LLM 驱动）

### 3.1 加载与激活

- `SkillManager`（`src/agent/skills/base.py`）从内置目录加载 15 个 YAML；可通过
  `config.agent_skill_dir` 加载自定义目录（同名自定义覆盖内置）。
- `src/agent/factory.py:resolve_skill_prompt_state()` 解析激活集合，优先级：
  **请求显式指定 > 配置 `agent_skills` > 默认集 `bull_trend`**；校验未知 skill id 并告警。
- 激活后 `get_skill_instructions()` 把指令按分类（趋势/形态/反转/框架）拼进系统 prompt；
  同时注入 `CORE_TRADING_SKILL_POLICY_ZH`（默认基线）或 `TECHNICAL_SKILL_RULES_EN`。

### 3.2 路由（SkillRouter，`src/agent/skills/router.py`）

`select_skills(ctx, max_count=3)` 决策顺序：

1. 用户显式请求（`ctx.meta.skills_requested / strategies_requested`）——最高优先级；
2. 配置 `agent_skill_routing=manual` 时用 `config.agent_skills` 白名单；
3. 自动模式：从 technical agent 的 `raw_data` 检测市场状态
   （`ma_alignment` + `trend_score` → `trending_up / trending_down / sideways / volatile`，
   `ctx.meta.sector_hot` → `sector_hot`），按 `market_regimes` 标签匹配策略；
4. 回退到 `get_default_router_skill_ids()`。

### 3.3 执行（单 Agent / 多 Agent）

- **单 agent（`agent_arch=single`）**：`AgentExecutor` 直接把激活策略指令注入单个 LLM
  prompt，配 `ToolRegistry` 工具（`get_daily_history`、`analyze_trend`、
  `get_realtime_quote`、`get_stock_info`、`get_sector_rankings`、`search_stock_news` 等）。
- **多 agent（`agent_arch=multi`）**：`AgentOrchestrator` 为每个激活策略生成一个
  `SkillAgent`（`src/agent/skills/skill_agent.py`，agent 名 `skill_<id>`），其
  system prompt 即该策略的 instructions，只开放 `required_tools`；每个 SkillAgent
  输出 JSON：`{skill_id, signal(strong_buy..strong_sell), confidence, conditions_met,
  conditions_missed, score_adjustment(-20..+20), reasoning}`。

### 3.4 聚合（SkillAggregator，`src/agent/skills/aggregator.py`）

- 信号映射：strong_buy=5.0 … strong_sell=1.0；
- 权重 = `opinion.confidence × 性能权重`。性能权重来源：
  - 回测自动权重（`agent_skill_autoweight=True` 时）：`BacktestService.get_skill_summary`
    评估数 ≥30 时用 `0.5 + win_rate`；
  - 记忆权重：`AgentMemory.compute_skill_weights()`（回测权重持久化）；
- 加权求和 → 阈值映射最终信号；同时累加 `score_adjustment` 供下游修正 sentiment_score；
- 输出 `skill_consensus` 观点参与最终报告。

### 3.5 用户入口

- 机器人：`/strategies`（`bot/commands/strategies.py`，列策略与激活状态）、
  `/ask <代码> <策略名>`（支持中文别名）；
- API：`/api/v1/agent` 请求体可带 `skills` 列表；
- 配置：`AGENT_SKILLS` 环境变量 / `agent_skills` 配置项（逗号分隔，`all` 激活全部）。

---

## 4. ③ 程序化规则引擎与回测模板

### 4.1 规则 Schema（`paper_trading/strategies/engine/schema.py`）

规则策略由 YAML 描述：`indicators`（指标列表）+ `entry_rules` / `exit_rules`
（`left op right`，left/right 可为指标名或数字字面量）+ `params`（如 `lot_size`）。

合法 op：`> < >= <= == cross_up cross_down`（cross_* 需对比前一根 bar）。

### 4.2 指标库（`paper_trading/strategies/engine/indicators.py`）

支持：`ma{N}`、`ema{N}`、`rsi{N}`（Wilder 平滑）、`macd/macd_signal/macd_hist`（12/26/9）、
`boll_mid/upper/lower`（20 期 ±2σ）、`pct_chg{N}`、`atr{N}`（Wilder EMA of TR）、
`fib{N}` / `fib_0.618`（0.236/0.382/0.5/0.618/0.786 滚动回撤位，自动判断趋势方向）、
`support/resistance`（fractal 或 cluster 法）、`obv`、`sto/sto_k/sto_d`、`cci{N}`、
`wr{N}`、`vma{N}`、`vwap`，以及原始 OHLCV 列 `close/open/high/low/volume`。

### 4.3 RuleEngine（`paper_trading/strategies/engine/rule_engine.py`）

- `evaluate(strategy, df, code)`：取最新两根 bar，**先查 exit_rules 再查 entry_rules**
  （持仓优先）；所有规则 AND 匹配；输出 `Signal(side=buy/sell/none, trigger_price=最新收盘,
  suggested_quantity=lot_size, reason, risk_mandated)`；
- `evaluate_multi_timeframe()`：多周期 AND 共识——所有周期必须给出**相同**非 none 信号，
  任一周期缺失/无信号/方向冲突都输出 no-signal（保守共识）；
- 数据不足 2 根 bar 或指标计算异常时输出 `side=none`（不抛错，保底降级）。

### 4.4 回测模板（`paper_trading/strategies/engine/templates.py`）

| 模板 id | 中文名 | 入场 | 出场 |
| --- | --- | --- | --- |
| `golden_cross` | 均线金叉 | ma5 cross_up ma10 | ma5 cross_down ma10 |
| `rsi_reversal` | RSI 反转 | rsi14 < 30 | rsi14 > 70 |
| `boll_breakout` | 布林带突破 | close cross_up boll_upper | close cross_down boll_lower |
| `macd_momentum` | MACD 动量 | macd_hist cross_up 0 | macd_hist cross_down 0 |

可通过 `get_template(name)` 实例化，也支持把 YAML 规则策略直接喂给 RuleEngine。

---

## 5. 回测引擎

### 5.1 BacktestEngine（`paper_trading/backtest/engine.py`）

逐 bar 历史回测（复用 RuleEngine + FeeModel，不改动它们）：

- **防未来函数**：第 i 根 bar 评估时只传入 `df.iloc[:i+1]`（`_ensure_no_lookahead`）；
- **成交模拟 `_simulate_fill`**：滑点（默认 5bp）→ A 股涨跌停过滤（±10%，涨停止买、
  跌停止卖）→ 成交价必须在当日 high/low 区间内（否则 rejected）→ 计算手续费
  （佣金 2.5bp / 最低 5 元 / 卖出印花税 10bp）；
- **仓位**：买入数量按手（100 股）取整，受现金与单票上限（默认 30% 总资产）约束；
- **基准**：默认沪深 300（`000300`），产出同期基准收益与超额收益；
- **绩效指标**：总收益、年化收益（242 交易日）、Sharpe、最大回撤与回撤时长、
  胜率、盈亏比、平均持仓天数、Calmar、基准收益、超额收益；
- 每个交易日输出 `DailySnapshot`（现金/总资产/持仓市值/日收益/累计收益/基准收益）。

### 5.2 WalkforwardOptimizer（`paper_trading/backtest/walkforward.py`）

滚动训练/测试防过拟合验证：

- 默认窗口：train=504 bar、test=126 bar、step=63 bar；
- 每个窗口在训练段对 `param_grid` 网格搜索（评分 = Sharpe，并列按总收益），
  再用最优参数在测试段做**样本外**评估；
- 输出：各窗口样本外 Sharpe/收益、最优参数出现频率（`param_stability`）、
  跨窗口最常选中的参数组合。

### 5.3 策略级回测服务（`paper_trading/strategy_backtest_service.py`）

- `run_strategy_backtests()`：跑全部 4 个模板策略 × 自选股（默认 `config.stock_list`，
  初始资金 100 万，起点 2024-01-01），结果落 `strategy_backtest_results` 表；
- `_compute_softmax_weights()`：对**有交易**的策略按 Sharpe 做 SoftMax 归一化得到融合权重；
- `refresh_fusion_weights()` / `load_fusion_weights()`：重算/加载最新批次融合权重
  （listener 启动时加载，空则保持默认权重 1.0）；
- `weekly_backtest_job()`：每周重算任务（注册到 runtime_scheduler，建议周日）。

> 注：这里的「模板策略回测权重」与 3.4 节 SkillAggregator 的「skill 回测权重」
> 是两条独立链路：前者服务融合策略权重持久化，后者服务多策略共识聚合。

### 5.4 策略生命周期状态机（`paper_trading/strategy_lifecycle.py`）

```
DRAFT → BACKTEST → PAPER → REVIEW → LIVE ⇄ PAUSED → RETIRED
        └───────────┴── 任意阶段可回退 DRAFT（重新起草）
```

- 非法转移抛 `LifecycleTransitionError`；合法转移记录审批日志（operator + 时间戳）；
- `is_live(name)` 判断策略是否实盘；默认未知策略状态为 DRAFT。

---

## 6. ② 市场复盘策略蓝图（`src/core/market_strategy.py`）

`MarketStrategyBlueprint` 结构：`region / title / positioning / principles /
dimensions(StrategyDimension: name/objective/checkpoints) / action_framework`。

`get_market_strategy_blueprint(region)` 按区域返回，未知区域回退 CN。

| region | 蓝图 | 分析维度（dimensions） | 行动框架 |
| --- | --- | --- | --- |
| cn | A股市场三段式复盘策略 | 趋势结构 / 资金情绪 / 主线板块 | 进攻/均衡/防守 |
| us | US Market Regime Strategy | Trend Regime / Macro & Flows / Sector Themes | Risk-on/Neutral/Risk-off |
| hk | 港股市场三段式复盘策略 | 趋势结构 / 资金情绪（南向）/ 主线板块 | 进攻/均衡/防守 |
| jp | 日本市场三段式复盘策略 | 趋势结构 / 宏观与汇率 / 主题线索 | 进攻/均衡/防守 |
| kr | 韩国市场三段式复盘策略 | 趋势结构 / 科技周期 / 主题线索 | 进攻/均衡/防守 |

**注入方式**（`src/market_analyzer.py`）：

- `to_prompt_block()`：渲染为 `## Strategy Blueprint` prompt 指令块，注入 LLM 复盘生成；
- `to_markdown_block()`：渲染为报告模板 fallback 章节（US 用英文 `### VI. Strategy
  Framework`，其余中文 `### 六、策略框架`）。

---

**总结：** 项目策略体系 = ① 15 个 LLM 驱动的个股交易 Skills（趋势/形态/反转/框架四类）+ ② 5 个区域大盘复盘蓝图（CN/US/HK/JP/KR）。默认激活的是 bull_trend，其余按需通过 /ask 指定或配置 agent_skills 启用。

## 7. 新增 / 修改自定义策略指南

1. 在 `paper_trading/strategies/configs/` 新建 `*.yaml`（或配置 `agent_skill_dir`
   指向自定义目录），必填字段：`name`、`display_name`、`description`、`instructions`；
   可选：`category`、`core_rules`、`required_tools`、`aliases`、`default_active`、
   `default_router`、`default_priority`、`market_regimes`。
2. `instructions` 是核心——建议按「适用场景 → 判定标准（引用工具数据）→ 输出要求 →
   评分调整建议」组织，评分以 `sentiment_score ±N` 表达，与聚合器 `score_adjustment` 对接。
3. 验证：`/strategies` 命令确认列出；`/ask <代码> <策略名>` 走通分析；
   程序化回测请参照 `templates.py` 编写 `entry_rules / exit_rules`。
4. 相关测试参考：`tests/test_market_strategy.py`、`tests/test_news_strategy_config.py`、
   `tests/test_strategy_backtest_service.py`、`tests/test_strategy_lifecycle.py`、
   `tests/test_strategies_v2_phase3.py`。
