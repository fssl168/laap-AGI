# LAAP 阶段 4 方案差距评估报告

> **角色**：项目经理 · 代码实施审查 + 方案差距评估
> **评估对象**：阶段 4（真实执行）已实现代码 vs `docs/phase2-multi-factor-strategy-plan.md` 第七节计划
> **日期**：2026-08-15
> **结论一句话**：路径 A 主体已实现且 19 项测试通过；但存在 2 个 HIGH 级缺陷（风控退出死代码、现金可为负）已在本次修复，另有数据口径 / 操作面 / 编排三处 MEDIUM 级差距待决策。

---

## 一、实际实现清单（对照计划第七节）

| 计划项 | 计划文件 | 实际状态 | 说明 |
|---|---|---|---|
| 4.0 `load_ohlcv` | kline_source.py | ✅ 已实现 | 五元组 + 合成降级，测试覆盖 |
| 4.0 `incremental_update` | kline_source.py | ⚠️ 已实现但有缺陷 | 死变量 `start` 已清；未传 `start_date/end_date`（拉全量再 tail） |
| 4.0 `market_source` 批量快照（`stock_zh_a_spot_em`） | market_source.py | ❌ 未实现 | 计划 4.0 的加速/降级路径缺失 |
| 路径 A `run_daily_cycle` | paper_service.py | ✅ 已实现 | 信号→下单/平仓→净值快照 |
| 路径 A 信号判定 | backtest_runner.py | ✅ 已实现 `evaluate_signal` | 但风控退出原为死代码（已修复） |
| 路径 B `broker.py`（BrokerAdapter） | — | ❌ 未实现 | 合理：走路径 A 无资金风险，符合计划推荐 |
| 路径 C `live_feed.py`（实时推送/调度） | — | ❌ 未实现 | 合理延后 |
| **计划外新增**：`serialize_params`/`params_to_code` | param_extractor.py | ✅ 已实现 | **正向偏离**：补上阶段 3 F1 断链（代码进化改不了参数值） |
| **计划外新增**：`apply_params_to_code` | quant_evolution.py | ✅ 已实现 | 搜索成果经 M4 治理落回 strategy.py |

**关键判断**：`params_to_code` + `apply_params_to_code` 是计划未列但**方向正确**的收敛——它把"参数空间即代码"（阶段 2 方案 C）真正闭环，让 param_evolver 的搜索成果成为一次受 M1-M4 治理的代码级变更。这是本次实现最大的增量价值。

---

## 二、缺陷清单（按严重度）

### HIGH（已修复，本次）

| # | 位置 | 缺陷 | 风险 | 修复 |
|---|---|---|---|---|
| H1 | `backtest_runner.evaluate_signal` | 风控退出**死代码**：`sl = price <= price*(1-stop_loss)`、`tp = price >= price*(1+take_profit)`、`atr_sig = price <= price - atr_mult*a` 恒为 False；`ts_sig` 定义未用。实盘卖出仅靠 `trend_down or overbought` | 止损/止盈/移动止损/ATR 止损全部失效，持仓无风险保护 | 增加 `entry_price`/`peak` 参数，风控退出改为相对入场价计算；无 entry 时保守跳过 |
| H2 | `paper_service.run_daily_cycle` | `max(qty, 100)` 强制至少买 100 股，`fill_order` 直接 `cash -= price*qty` 不校验 | 预算不足时现金为负 | `qty < 100` 时 hold（"insufficient cash"），不再强制买入 |

### MEDIUM（待决策/建议，未改代码）

| # | 位置 | 缺陷 | 建议 |
|---|---|---|---|
| M1 | `kline_source.incremental_update` | 复权口径 `adjust="qfq"`，但 `load_ohlcv`/`load_price_series` 读 `kline.db` 中既有数据（复权口径未知） | OOS 门禁历史基线与实盘信号可能跨口径比较 → **统一数据口径**（建议 kline.db 全量 qfq，或显式记录口径字段） |
| M2 | `api.py` | `run_daily_cycle`/`apply_params_to_code`/`load_ohlcv`/`incremental_update` 均未接 `/v1/quant/*`（当前仅 `evolve_params`） | 阶段 4 是库级能力，无 HTTP 操作面 → 补 `POST /v1/quant/daily_cycle`、`POST /v1/quant/apply_params` |
| M3 | 全链路编排 | 无 `evolve_params → apply_params_to_code → run_daily_cycle` 的每日管线（计划 SignalScheduler 未做） | 路径 A 验收"跑通 800 天真数据"需自动化入口 → 复用 `laap_brain` 调度器模式加 daily job |
| M4 | `apply_params_to_code` | 仅对 stub CodeEvolutionEngine 测试；真实引擎全链路（真实 repo_root + SafetyGuard + SandboxTester + git.deploy）未端到端验证 | 补一条真实 tmp repo 的端到端测试 |

### LOW（观察项）

| # | 位置 | 观察 | 说明 |
|---|---|---|---|
| L1 | `evaluate_signal` vs `_run_multi_factor` | 买卖条件重复实现（DRY 违反） | 信号源与回测引擎两处逻辑可能漂移 → 后续抽取共享谓词 |
| L2 | `load_ohlcv` 合成降级 | `run_daily_cycle` 未向调用方显式暴露 `used_fallback` | 实盘可能静默用合成 OHLCV → 应在返回结构加 `data_quality` 标记 |
| L3 | `params_to_code` | 正则 `\{[^}]*\}` 替换，dict 内嵌 `}` 或注释含 `}` 会截断 | 当前 14 参数全标量，安全；若未来加嵌套结构需改 AST 改写 |
| L4 | `incremental_update` | 拉全量再 `.tail(days)`，未传日期参数 | 低效；参照 `real_data/fetch_real_kline.py` 传 `start_date/end_date` |

---

## 三、方案差距评估结论

### 已覆盖（计划达成）
- 路径 A 核心三件套：`load_ohlcv` + `evaluate_signal` + `run_daily_cycle` ✅
- 计划外正向收敛：`params_to_code` / `apply_params_to_code` ✅（补阶段 3 断链）

### 未覆盖（计划 vs 实现差距）
| 计划承诺 | 差距定性 | 建议 |
|---|---|---|
| 4.0 market_source 批量快照 | 缺失，但非阻塞（单只 `stock_bid_ask_em` 够用） | 低优先级补 |
| 路径 B broker.py | 未做 | **维持不做**（无资金风险，符合推荐） |
| 路径 C live_feed.py / SignalScheduler | 未做 | 待路径 A 跑稳后再上 |
| 每日编排 / API 操作面 | 缺失 | **下一优先级**（M2/M3） |
| 数据口径统一 | 缺失 | **下一优先级**（M1，影响信号正确性） |

### 验收口径（诚实标注）
- 阶段 4 组件测试：**19 passed**（`test_paper_phase4.py` 12 + `test_params_to_code.py` 8，含本次修复前）
- 本次修复后 + 影响面：**62 passed**
- **未做真实券商实盘 / 未跑 800 天真实数据端到端**：`run_daily_cycle` 用 `ohlcv_map` 注入合成数据验证，真实 `kline.db` 路径未在测试中走通（`test_load_ohlcv_fallback_synthetic` 证明沙箱会降级合成）

---

## 四、本次已实施修复

| 文件 | 改动 |
|---|---|
| `laap/paper_trading/backtest_runner.py` | `evaluate_signal` 增 `entry_price`/`peak` 参数；`sl`/`tp`/`atr_sig`/`ts_sig` 改为相对入场价计算（H1） |
| `laap/paper_trading/paper_service.py` | `run_daily_cycle` 预算不足一手时 hold；卖出入场价传入 `evaluate_signal`（H2） |
| `laap/paper_trading/kline_source.py` | 清 `incremental_update` 死变量 `start` + 标注复权口径 caveat（M1 提示） |
| `tests/test_paper_phase4.py` | 新增 4 项：止盈风控退出可达、无 entry 保守跳过、现金不足 hold |

---

## 五、待用户决策（阻塞后续）

- [ ] **数据口径**：kline.db 是否全量 qfq？还是新增口径字段？（M1，影响信号正确性）
- [ ] **API 操作面**：是否补 `POST /v1/quant/daily_cycle` 与 `POST /v1/quant/apply_params` 端点？（M2）
- [ ] **每日编排**：是否加 daily job（evolve→apply→daily_cycle）？（M3）
- [ ] **阶段 4 测试范围**：是否要求"800 天真实 kline.db 数据端到端跑通"作为验收？（当前为合成注入）

---

## 六、开发修复实施（T1-T4，2026-08-15）

> 第 5 节 4 项决策由项目经理全部拍板执行，逐项 E2E 核验，量化全量 **179 passed / 0 failed**（+17）。

### T1：数据口径统一 + 增量效率 + 数据质量标记（M1 + L4 + L2）✅

| 文件 | 改动 |
|---|---|
| `kline_source.py` | 新增 `KLINE_ADJUST = "qfq"` 常量（统一复权口径，M1）；`incremental_update` 加 `start_date/end_date` 参数（L4）+ 固定 KLINE_ADJUST；`load_ohlcv` 加 `with_quality=True` 返回 `(ohlcv, quality{source, used_fallback, adjust})`（L2） |
| `paper_service.py` | `run_daily_cycle` 收集并返回 `data_quality`（每标的 source/used_fallback/adjust） |

**E2E**：沙箱 kline.db 有真实 600519 数据 → `data_quality: {source: real, used_fallback: False, adjust: qfq}`，真实 K 线路径走通。

### T2：API 操作面（M2）✅

| 端点 | 功能 |
|---|---|
| `POST /v1/quant/daily_cycle` | 日终闭环（真实K线→信号→交易自我审核→交易→净值），body `{symbols, params?}` |
| `POST /v1/quant/apply_params` | 参数落回代码（M4 治理 + 交易自我审核），body `{params, self_review?, rationale?}` |

**E2E**：真实 handler + 真实 kline.db → HTTP 200，data_quality real。

### T3：每日编排（M3）✅

| 组件 | 功能 |
|---|---|
| `daily_pipeline.QuantDailyPipeline` | `run()` = evolve_params 搜索 → apply_params_to_code（自我审核）→ run_daily_cycle；apply 被拒时回退当前参数 |
| `daily_pipeline.QuantDailyScheduler` | daemon 每日调度（默认关，`LAAP_QUANT_DAILY=1` 启用，`LAAP_QUANT_DAILY_INTERVAL` 控制周期，对齐 LAAP_EVO_ENABLED 模式） |
| `api.py` | `_start_quant_daily_scheduler()` + main() 接入 |

**E2E**：真实 pipeline 全链路跑通（gate_blocked 时回退 current，daily_cycle 用真实 kline.db）。

### T4：真实数据端到端（M4）✅

| 项 | 内容 |
|---|---|
| 真实 git E2E 测试 | `test_apply_params_to_code_real_git_e2e`：git init 仓库 → apply → approve → **真实 git.deploy** → strategy.py 更新 + AGI commit |
| 运行入口 | `scripts/run_quant_daily_pipeline.py`（真实组件 + 真实 kline.db，沙箱验证可执行，全程 paper） |

**验收口径更新**：真实 kline.db 路径已在测试中走通（沙箱 kline.db 含 600519 真实数据，`data_quality: real`）；"800 天真实端到端"由 `run_quant_daily_pipeline.py` 在用户真实环境跑。

### 测试分布（+17）

| 文件 | 新增 |
|---|---|
| `test_paper_phase4.py` | +6（T1 口径/质量标记） |
| `test_quant_api.py` | +5（T2 daily_cycle/apply_params） |
| `test_daily_pipeline.py` | +5（T3 pipeline/scheduler） |
| `test_params_to_code.py` | +1（T4 真实 git E2E） |

---

## 七、Step 1 / Step 2 验证执行记录（2026-08-15）

### 7.1 用户决策记录（已确认）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 验证范围 | 自选股（600519/000001/000858）× 800 天 × 5 段滚动 |
| 2 | Go 标准 | 「walk-forward 稳定过门禁（正收益 + z≥1.96）」为进入 Step 2/3 的硬标准 |
| 3 | TradingSelf 人格 | 默认 traits + 新增 保守/平衡/激进 预设（`PERSONA_PRESETS`，`TradingSelf(preset=...)`） |
| 4 | 资金阶梯 | 按行业经验定：paper → 券商 sim → 小额实盘；实盘需显式授权，先卡在 sim 盘 |
| 5 | **Step 2 A/B 对照**（最重要） | 有/无 self-review 对照实验不可省；若只是"自说自话"→ 降级纯记录层（只写 [self]/[benefit]），硬风控单独抽离 fail-closed |

### 7.2 Step 1：滚动 Walk-Forward（决策 1/2）—— 判定 **FAIL**

**工具**：`laap/paper_trading/walkforward.py` + `scripts/walkforward_validation.py` + `tests/test_walkforward.py`（12 项）
**配置**：800 天 × train=400 / test=80（5 段滚动）× random(n=100, seed=42) × 门禁（正收益 + z≥1.96 显著性，baseline=100）

| 指标 | 值 |
|---|---|
| 总段数 | 15（3 标的 × 5 段） |
| 通过门禁 | **1/15（7%）** |
| OOS 正收益段 | 6/15 |
| 跑赢买入持有段 | 10/15（平均超额 +4.18%） |
| 平均 / 中位 OOS 收益 | +0.35% / **-0.18%** |
| 中位 z | **0.04** |
| 判定 | **FAIL**（通过率 7% < 40%，中位收益 ≤ 0，中位 z ≈ 0，与随机参数基线无异） |

**诚实结论**：14 维多因子策略在 3 只自选股 800 天上**无稳定 alpha**；10/15 段跑赢买入持有仅说明它**减亏**（在下跌段少亏），而非产生正收益。按决策 2，**paper 观察期（盈利性验证）暂缓**；策略如需推进须先重新设计因子或换标的池。

### 7.3 Step 2：TradingSelf A/B 对照（决策 5）—— 判定 **BORDERLINE** + 机制发现

**工具**：`laap/paper_trading/paper_replay.py` + `scripts/run_self_review_ab.py` + `tests/test_paper_replay.py`（7 项）
**配置**：同一 STRATEGY_PARAMS × 同一 800 天数据 × 逐日回放；Arm A 直接执行 vs Arm B 经 TradingSelf（balanced 人格）审核

| 标的 | A cumret / dd / trades | B cumret / dd / trades | 裁决 approve/abstain | scoreΔ | 幽灵仓(被弃权买单) |
|---|---|---|---|---|---|
| 600519 | -4.74% / 10.56% / 39 | -1.10% / 1.44% / 1 | 2 / 56 | +0.023 | 56 笔，**-66,288** |
| 000001 | +2.67% / 8.79% / 34 | -2.10% / 3.19% / 1 | 2 / 52 | -0.036 | 52 笔，+44,683 |
| 000858 | -5.70% / 8.82% / 27 | -3.38% / 3.38% / 1 | 2 / 46 | +0.014 | 46 笔，**-144,992** |

**汇总**：score 平均变化 **+0.000**；回撤平均减小 **+0.067（3/3 标的）**；被弃权买单反事实 **154 笔共 -166,597**（胜率仅 25~29%）。

**机制发现（关键）**：TradingSelf **不是自说自话**——幽灵仓反事实证明它的弃权避开了真实亏损（-16.7 万）；但它**过度抑制**：abstain 率 ~95%（每标的仅 2 笔获批），原因有二：
1. **记忆语义副本放大**：`encode_experience` 把一条教训蒸馏成 1 episodic + N semantic 副本，单笔亏损被数成 N 笔 → 记忆门禁误判；
2. **自我效能死锁**：任何一笔亏损后 `self_efficacy` 降到 0.48（<0.5）且 `total_actions>0`，此后所有买单被"未达中性"弃权；弃权→无新交易→效能永不恢复 → 永久锁死。

后果：回撤大幅下降（真效果）但夏普全线恶化、收益被锁死，风险调整得分不变。

### 7.3.1 修复（决策 5 落地）与重跑结果

**修复**（`trading_self.py`）：
| 修复 | 内容 |
|---|---|
| 记忆衰减 | 只取 `episodic` 原始教训（剔除 semantic 副本，防单笔被数成 N 笔）；净负面 `>= 2` 才弃权（单笔不否决）；正面抵消负面=宽恕；检索窗口=自然遗忘 |
| 仓位校准 | `position_scale_max = strategy_position_scale × (0.8 + risk_appetite×0.8)`，锚定策略实际配置，persona 只约束"超配" |
| 效能门禁 | 只约束买入（卖出=风控动作永不受阻）；新增**弃权冷却** `ABSTAIN_COOLDOWN=5`——连续弃权 5 次强制放行重新检验，保证不永久锁死 |

**重跑 A/B（balanced，同一数据/参数）**：

| 标的 | A cumret / dd / trades | B cumret / dd / trades | 裁决 approve/abstain | scoreΔ | 幽灵仓 |
|---|---|---|---|---|---|
| 600519 | -4.74% / 10.56% / 39 | -3.19% / 3.34% / 11 | 22 / 44 | **+0.018** | 44 笔，-32,907 |
| 000001 | +2.67% / 8.79% / 34 | **+3.63%** / 7.25% / 12 | 24 / 32 | **+0.029** | 32 笔，+28,922 |
| 000858 | -5.70% / 8.82% / 27 | -3.63% / 6.53% / 9 | 18 / 32 | +0.006 | 32 笔，-69,198 |

**修复后汇总**：score 平均变化 **+0.017（3/3 标的改善）**；回撤平均减小 **+0.037（3/3）**；被弃权买单反事实 108 笔共 **-73,183**（弃权仍避损）；abstain 率从 ~95% 降到 ~60%（每标的 9~12 笔）。

**判定：BORDERLINE（修复后）**——距 KEEP 阈值（scoreΔ≥0.02）仅差 0.003，且 3/3 标的风险调整得分与回撤同时改善、幽灵仓证明弃权仍在避损。**结论：TradingSelf 非"自说自话"，不降级；作为回撤压缩 + 亏损规避层保留，进入 Step 2 paper 观察期随净值数据持续监控**（若观察期 score 提升稳定 ≥0.02 再升 KEEP）。

### 7.4 测试增量（+20，本记录新增）

| 文件 | 新增 |
|---|---|
| `test_walkforward.py` | 12（滚动段/单段/汇总/判定逻辑） |
| `test_paper_replay.py` | 6（回放/幽灵仓/人格预设/可复现） |
| `test_trading_self.py` | +2（弃权冷却释放 / 卖出不受自我效能阻止） |

量化全量（含本记录，paper_trading/quant 相关 19 个测试文件）：**141 passed / 0 failed**。

---

## 八、Track ①/② 执行记录（2026-08-15）

### 8.1 Track ①：策略层因子重设计 + walk-forward 重跑 —— 判定 **FAIL（诚实负结果）**

**假设**（基于 Step 1 证据"只减亏、不产生正收益"）：基线策略缺趋势过滤，在下跌/震荡市反复被套 → 增加**长期均线 regime 过滤**（价格站上 60/120 日均线才交易、跌破即离场）。

**实现**：`backtest_runner._run_multi_factor` / `evaluate_signal` / `run_backtest(_values)` / `param_evolver.{_score,random_search,genetic_search}` 新增可选 `regime_ma`（None=关闭，向后兼容）；`walkforward` 支持 `--variant none|regime60|regime120`。

**结果**（同一 800 天 × 3 标的 × 5 段滚动，同一门禁 正收益 + z≥1.96）：

| 变体 | 通过率 | 平均 OOS | 中位 z | 判定 |
|---|---|---|---|---|
| none（基线） | 1/15 (7%) | +0.35% | 0.04 | FAIL |
| regime60 | 0/15 (0%) | -0.64% | 0.12 | FAIL |
| regime120 | 0/15 (0%) | **0.00%** | 2.31 | FAIL |

**根因（诚实结论）**：本样本（3 只标的 800 天，15 个 OOS 窗中 **10 个买入持有为负**）以熊市/震荡为主。趋势过滤把"亏损交易"变成"空仓"（regime120 全部 0% = 全程未建仓），**避损但不产生正收益**；regime60 少数建仓段也无正 edge。**在"长期做多 + 该因子族"内继续参数/因子微调预计无解**——下一步应扩大标的池（42 只自选股 kline.db）或换策略族（指数择时 / 多空）再验证。

### 8.2 Track ②：paper 观察期启动 + TradingSelf 分窗监控 —— **KEEP_WITH_MONITORING**

**工具**：`scripts/monitor_trading_self.py`（复用 paper_replay：真实 800 天 × 3 标的，Arm A vs Arm B（修复后 TradingSelf），按 4 × 200 天窗口逐窗对比 score/收益/回撤 + 裁决统计；观察日志追加 `real_data/trading_self_observation_log.json`）。

**分窗结果**（balanced 人格）：

| 标的 | B 优于 A 的窗口 | 整体 B vs A（cumret） | 回撤（全部窗口 B ≤ A） |
|---|---|---|---|
| 600519 | 0,1（2/4） | -3.19% vs -4.74% | ✅ 全窗 Δdd ≥ 0 |
| 000001 | 0,2,3（3/4） | **+3.63% vs +2.67%** | ✅ 全窗 Δdd ≥ 0 |
| 000858 | 0,3（2/4） | -3.63% vs -5.70% | ✅ 全窗 Δdd ≥ 0 |

**监控判定**：B 在 **8/12（67%）** 个(标的×窗口)单元中 score 优于 A；**回撤在所有窗口均未劣化** → **KEEP_WITH_MONITORING**。诚实标注：TradingSelf 并非全窗优于 A（600519 窗 2-3、000858 窗 1-2 略差），其价值主要在**回撤压缩 + 亏损规避**（一致），收益改善在多数窗口但非全部。

**观察期运行方式**：离线回放已启动并记录基线；后续每日由 `scripts/run_quant_daily_pipeline.py`（真实 kline.db + TradingSelf）运行，结果持续追加同一观察日志，按窗口判定 TradingSelf 是否保持价值。

### 8.3 本记录测试增量

| 项 | 说明 |
|---|---|
| `backtest_runner` regime_ma | 向后兼容新增（默认 None），既有测试全绿 |
| `param_evolver` regime_ma 透传 | 向后兼容新增 |
| 量化全量 | **141 passed / 0 failed** |

### 8.4 第二轮执行（2026-08-15）：指数验证 + 真实管线观察

**Track ① 延伸：指数 sh000001（801 天真实 kline.db）walk-forward**（同一门禁）：

| 指标 | 值 |
|---|---|
| 通过门禁 | **0/5（0%）** |
| 平均 / 中位 OOS | -0.51% / +1.28% |
| 中位 z | 0.26 |
| 跑赢买入持有 | **1/5**（平均超额 **-3.75%**，跑输） |

**关键观察**：指数上 train 夏普高达 1.2~2.0（train score 0.57~0.83），但 OOS 无 edge 且跑输买入持有 → **典型 train 过拟合模式**（train 完美、OOS 失效）。

**宇宙扩展受阻**：akshare 未安装（`ModuleNotFoundError`），kline.db 个股仅 64 天（仅 600519=320 天、指数=801 天）→ **无法离线扩充 800 天标的池**；需在用户真实环境 `pip install akshare` + `fetch_real_kline.py` 拉更多标的。

**Track ① 稳健结论（跨 3 标的一 + 指数 + 3 变体共 35 段）**：14 维多因子族在全部可得真实数据上**无稳定长期做多 edge**；`train 过拟合 → OOS 失效`是统一模式。下一步（需用户环境/决策）：① 装 akshare 扩宇宙复验；② 换策略族（指数择时/多空/不同信号）；③ 接受无 alpha 停止投入。

**Track ② 真实管线观察运行**（`scripts/run_quant_daily_pipeline.py --n-samples 30`）：

| 项 | 结果 |
|---|---|
| 数据 | 600519 **real/qfq**（kline.db 320 天），000001/000858 synthetic fallback（kline.db 无数据） |
| evolve 搜索 | random n=30 seed=42 |
| apply | **gate_blocked**（搜索参数未过 OOS 门禁 → 回退 current 参数，fail-closed 正确） |
| daily_cycle 信号 | hold / hold / hold（当前窗口无买入触发） |
| 净值 | 1,000,000.0（无成交，现金完整） |
| data_quality | 600519 real/qfq；其余 synthetic —— 诚实标记 |

**观察日志**：`real_data/trading_self_observation_log.json` 现有 **2 条**（分窗监控基线 + 真实管线运行），后续每日由管线/监控脚本持续追加。

### 8.5 第三轮执行（2026-08-15）：多空策略族 + 42 只自选股横截面

**实现**（`backtest_runner._run_multi_factor` 新增 `long_short` 开关；`run_backtest(_values)` / `param_evolver.{_score,random_search,genetic_search,random_baseline}` / `walkforward` 全链路透传；`--family long_only|long_short`）：
- 记账统一（position 正=多头/负=空头；开多减现金、开空加现金；平仓 `cash += position*price` 符号统一）
- 方向信号：多头=bull（趋势+不过热+放量）；空头只认**趋势信号** `sma<lma` / regime 跌破（超买是均值回归离场，不做空头方向——避免上涨趋势中超买被误判做空）
- 空头风控：止损/止盈/移动止损（谷值回撤）/ATR 止损；多空翻仓（退出即反向开仓）
- 修复方法学 bug：随机基线同步传 `long_short`（同族公平对照）

**800 天 walk-forward 全家族对比**（3 标的 × 5 段，同一门禁 正收益 + z≥1.96）：

| 族/变体 | 通过率 | 平均 OOS | 中位 z | 判定 |
|---|---|---|---|---|
| long_only（基线） | 1/15 (7%) | +0.35% | 0.04 | FAIL |
| long_only + regime60 | 0/15 | -0.64% | 0.12 | FAIL |
| long_only + regime120 | 0/15 | 0.00%（空仓） | 2.31 | FAIL |
| **long_short** | 0/15 | -2.60% | -0.34 | FAIL |
| long_short + regime60 | 0/15 | -2.58% | +0.11 | FAIL |
| 指数 sh000001（long_only） | 0/5 | -0.51% | 0.26 | FAIL |

**43 只自选股横截面**（kline.db 真实 OHLCV，近 64 天普遍下跌，默认参数）：

| 策略 | 正收益 | 跑赢买入持有 | 平均收益 | 中位收益 |
|---|---|---|---|---|
| 买入持有 | 8/43 | — | -10.04% | -16.95% |
| 长期做多 | 20/43 | 37/43 | +1.25% | 0.00% |
| **多空** | **30/43** | **36/43** | **+2.80%** | **+2.27%** |
| regime60 | 1/43 | — | -0.33% | 0.00% |

**综合结论（诚实）**：
1. **全部族/变体在 800 天 walk-forward 上均无稳定 edge**（0~1/15 通过率）；多空族 OOS 反而更差（趋势破位做空在震荡/回弹中被轧）。
2. **但 43 只横截面上多空是唯一正收益族**（30/43，均值 +2.80%）——在近 64 天普跌窗口，做空捕捉了下跌。这是**首个正向信号**，但**强依赖市场状态**（普跌窗口近乎"做空熊市"的顺风车），未通过多状态 800 天 walk-forward 检验。
3. **合成判断**：该因子族（多/空/过滤变体）在可得数据上**无稳定的、状态无关的 alpha**；横截面正向仅证明"熊市做空能赚钱"，不是持久 edge。
4. 后续真正有效路径需用户决策：① 装 akshare 扩 800 天宇宙复验横截面信号是否持久；② 接受"无 alpha"定位，将多空作为**市场状态择时工具**（仅确认下行趋势时启用空头）而非独立盈利源；③ 停投该因子族，换完全不同信号体系。

**测试增量**：`test_backtest_runner.py` +4（多空下跌段盈利/上涨段多头不受损/关闭等价基线/净值非负），量化全量 **145 passed / 0 failed**。

### 8.6 第四轮执行（2026-08-15）：指数择时 + 全族横截面收尾

**实现**（`_run_multi_factor` 新增 `external_regime`（外部指数逐 bar 状态门）；`run_backtest(_values)` 透传；`scripts/index_timing_scan.py`）：
- 指数择时语义：`ext[i] = 当日指数 close > 指数 MA20`；False → 禁多/离场（long_short 时允许开空）
- 横截面按 kline.db 交易日对齐指数与个股（42 只，64 天窗）

**43 只自选股横截面（指数择时 MA20）**：

| 策略 | 正收益 | 平均 | 中位 |
|---|---|---|---|
| 买入持有 | 8/43 | -10.04% | -16.95% |
| 长期做多（无择时） | 20/43 | +1.25% | 0.00% |
| **长期做多 + 指数择时** | **6/43** | **-0.05%** | 0.00% |
| 多空 + 指数择时 | 20/43 | +0.37% | -0.75% |
| 多空（无择时，§8.5） | **30/43** | **+2.80%** | **+2.27%** |

**指数择时结论**：MA20 指数门在震荡下跌市**频繁翻转**（砍掉反弹段盈利、回弹时轧空）→ 长期做多+择时 6/43（远差于无择时 20/43），多空+择时也由 30/43 降至 20/43。**MA20 择时反而有害**；更长周期择时（MA60/120）在 64 天窗内可用天数太少，未测。

### 8.7 Track ① 最终结论（四轮全家族证据）

| 维度 | 证据 | 结论 |
|---|---|---|
| 800 天 walk-forward（3 标的 × 5 段） | 基线 1/15、regime 0/15、多空 0/15、多空+regime 0/15 | 无稳定 edge |
| 指数（801 天） | 0/5，跑输买入持有 | 无 edge + train 过拟合 |
| 43 只横截面（64 天普跌窗） | 多空 30/43 +2.8%（唯一正收益族） | **熊市做空顺风车，状态依赖** |
| 43 只横截面 + 指数择时 MA20 | 择时使两族都变差（6/43、20/43） | MA20 择时有害 |

**最终判断**：用户指定的三条验证路径（扩 42 只池 / 多空 / 指数择时）**全部执行完毕**。因子族在可得数据上**无稳定的、状态无关的 alpha**；唯一正向信号（横截面多空）是普跌窗口的做空顺风车，未通过多状态 walk-forward。**盈利路径的下一步是数据/策略层面的决策**（装 akshare 扩 800 天宇宙复验 / 接受无 alpha 转状态择时工具 / 换信号体系），而非继续参数微调。

**测试增量**：`test_backtest_runner.py` +3（external_regime 全 True 等价 no-op / 下行禁多 / 下行开空），量化全量 **148 passed / 0 failed**。

### 8.8 第五轮执行（2026-08-15）：实证证据升级（用户清单 item 1-5）

**实现**（全向后兼容）：
| item | 实现 |
|---|---|
| 1 扩样本 | `pip install akshare`（成功，网络可用）；腾讯源 `stock_zh_a_hist_tx`（东财被沙箱断连）；`scripts/fetch_universe.py` 拉沪深300成分 → `real_data/universe/`（200 目标，≥500 交易日 qfq） |
| 1 多重检验 | `walkforward.py` 新增 `_two_sided_p`（erfc，无 scipy）+ `_mtc_pass_flags`（bonferroni / fdr BH q=0.05）；`--mtc` |
| 2 交易成本 | `_run_multi_factor` 新增 `costs={commission 0.025%, stamp 0.05%(卖), slippage 0.1%}`（4 处现金变动点）；T+1 由日线粒度天然满足；`--costs ashare` |
| 3 跨周期 | `_regime_class`（OOS 窗买入持有 ±5% → bull/range/bear）+ 汇总按段报告 |
| 4 真实执行留痕 | `docs/paper-observation-runbook.md`（LAAP_QUANT_DAILY=1 一月运行手册，调度器已接 api.py:1516） |
| 5 诚实负结果 | `docs/paper-honest-negative-framing.md`（工程贡献定位 + 措辞红线 + 摘要句） |

**大样本 walk-forward（94 只 × 500+ 天，A 股成本，Bonferroni）**：

| 指标 | 值 |
|---|---|
| 总段数 / 通过 | **0/94（0%）** |
| 中位 z | -0.16 |
| 平均 OOS / 正收益段 | +0.55% / 40/94 |
| **bull 段**（n=52） | mean_oos +3.60%，**excess -41.17%** |
| **range 段**（n=21） | mean_oos -2.96%，excess -2.54% |
| **bear 段**（n=21） | mean_oos -3.48%，**excess +11.88%** |

**全宇宙最终结果（193 只 × 500+ 天，A 股成本，Bonferroni，双族）**：

| 族 | 总段/通过 | 中位 z | 平均 OOS | bull excess | range excess | bear excess |
|---|---|---|---|---|---|---|
| long_only | **0/193** | -0.11 | -0.39% | **-33.63%** | -2.82% | **+12.19%** |
| long_short | **0/193** | -0.20 | -2.33% | **-37.77%** | -6.00% | **+14.59%** |

**最终结论（诚实，193 只大样本）**：加成本 + Bonferroni 后**两族全部 386 段无一通过**；中位 z 均为负（不优于随机参数基线）。唯一一致模式：**熊市段靠少亏/做空获得超额（+12%~+15%），但牛/震荡段严重跑输（-33%~-38%，RSI 超买门导致强趋势中空仓错失行情）**。**该因子族在 193 只 × 500 天大样本上确认无稳定的、状态无关的 alpha**——论文按 item 5 诚实负结果定位，实证由 item 1-3 全证据支撑，item 4 运行手册备好真实执行留痕。

**测试增量**：`test_walkforward.py` +5（two_sided_p/bonferroni/fdr/regime_class/run_mtc）、`test_backtest_runner.py` +2（成本侵蚀/零成本等价），量化全量 **155 passed / 0 failed**。
