# LAAP Paper Trading 多因子策略重构 实施计划

> **目标**：把 RSI 引擎从"受控模拟环境功能验证"推进到"真实可盈利执行交易系统"的基座。
> **方案**：阶段 2 策略接口设计（方案 C：参数空间即代码 + 确定性搜索为主、LLM 增强）。
> **状态**：阶段 1+2 已实施完成（2026-08-15）；阶段 3 已实施完成（2026-08-15）；阶段 4 待确认。
> **关联**：`docs/true-rsi-feasibility.md`（M1-M4 进化治理文档）、`laap/paper_trading/`（本地开发，不进 git/NAS）。

---

## 一、背景与定位

### 1.1 论文定位（不变量）

- 论文《Aris认知引擎RSI能力实证评估》定位 = **受控模拟环境功能验证**，非实证评测
- 真实数据 OOS 回测为**探索性边界说明**，如实报告负结果，不宣称"实证通过"
- 措辞须匹配证据强度（用户既定原则）

### 1.2 本项目目标（独立于论文）

把 RSI 引擎做成**真实可盈利的执行交易系统**，需要：

1. **策略层重构**：参数空间从 2 维（均线窗口）扩展为 14 维多因子（阶段 2，✅ 已完成）
2. **确定性参数搜索**：网格 → 随机 → 遗传（阶段 2，✅ 已完成）
3. **LLM 增强层**：确定性搜索为主、LLM 微调为辅（阶段 2，✅ 已搭框架）
4. **进化适配**：fitness 对接 + 门禁升级（阶段 3，⏳ 待做）
5. **真实执行**：券商 API / paper trading + 实时行情（阶段 4，⏳ 待做）

---

## 二、阶段 1：能力盘点结论（✅ 已完成）

### 2.1 进化链路现状

```
/v1/quant/evolve
  → _get_quant_engine()  (laap_brain/api.py:1030)
  → QuantEvolutionEngine(engine, runner, price_series, db).attach()
  → CodeEvolutionEngine.auto_improve("laap/paper_trading/")
       → scan_targets → _improve_single → patcher.generate_patch → 沙箱测试 → deploy_gate → 部署
```

### 2.2 四个关键发现

| # | 发现 | 影响 |
|---|---|---|
| F1 | **LLM 注入缺失**：`_get_code_evolution_engine()` 创建引擎时未传 `llm_fn`，服务链路 patcher 是 `PatchGenerator(None)`，规则 fallback 只能加注释 | 当前"代码进化"对 strategy.py 是**空转**，改不了参数值 |
| F2 | **参数空间 2 维**：`STRATEGY_PARAMS = {short, long}` | 无盈利策略承载能力 |
| F3 | **回测逻辑单一**：`_run_ma_cross` 仅金叉/死叉 | 无法表达多因子策略 |
| F4 | **fitness 脱节**：`compute_trade_fitness`（交易）与 `FitnessEvaluator.composite`（软件健康）两套体系；`deploy_gate` 是门禁非选择压力 | 进化没有"往盈利方向"的选择压力 |

### 2.3 一句话结论

> 引擎的"代码级自改进"机制与"盈利策略搜索"所需能力之间，缺了**参数搜索**这个桥——阶段 2 解决的就是这个。

---

## 三、阶段 2：策略接口设计（✅ 已实施）

### 3.1 决策记录（用户已确认）

| 决策点 | 选择 | 说明 |
|---|---|---|
| 方案 | **方案 C**（参数空间即代码 + 确定性搜索为主、LLM 增强） | 保持 STRATEGY_PARAMS 扁平结构，param_extractor 零改动 |
| 参数维度 | **14 参数**（含成交量因子） | 趋势 3 + RSI 3 + ATR 3 + 风控 3 + 成交量 2 |
| 搜索算法 | **网格（最可复现）→ 随机（快）→ 遗传（可选）** | 网格带爆炸防护，14 维全组合不可行 |
| LLM 增强 | **做** | `evolve_with_llm()` 框架已搭，llm_fn 由调用方注入 |

### 3.2 契约保证（防漂移，全部验证通过）

| 契约 | 保证 |
|---|---|
| `STRATEGY_PARAMS` 扁平 dict | 保持键值常量，`param_extractor.extract_strategy_params` AST 提取 **零改动**（已验证提取结果 == STRATEGY_PARAMS） |
| `BacktestRunner.run_backtest` 签名 | `(prices, params, split, ohlcv=None)`，`ohlcv` 为可选向后兼容扩展 |
| `compute_trade_fitness` / `oos_gate` / `QuantEvolutionGate` / 审计双写 | **全部不动** |
| `_run_ma_cross` | 保留为向后兼容的均线交叉基线（旧测试依赖） |

---

## 四、实施计划清单（按函数/文件拆分）

### 任务 1：扩展 strategy.py —— STRATEGY_PARAMS 2→14 参数 【✅ 完成】

**文件**：`laap/paper_trading/strategy.py`

| 改动点 | 内容 |
|---|---|
| 删除 | `short` / `long` 2 个键 |
| 新增 | 14 个键，分五类因子 |

```python
STRATEGY_PARAMS = {
    # ── 趋势因子 ──
    "fast_ma": 5,              # 快均线窗口
    "slow_ma": 20,             # 慢均线窗口
    "momentum_window": 10,     # 动量窗口
    # ── 超买超卖因子（RSI）──
    "rsi_period": 14,          # RSI 周期
    "rsi_oversold": 30,        # 超卖阈值
    "rsi_overbought": 70,      # 超买阈值
    # ── 波动率 / 仓位（ATR）──
    "atr_period": 14,          # ATR 周期
    "atr_stop_mult": 2.0,      # ATR 止损倍数
    "position_scale": 0.5,     # 仓位比例 [0,1]
    # ── 止损止盈（风控）──
    "stop_loss_pct": 0.08,     # 固定止损
    "take_profit_pct": 0.20,   # 固定止盈
    "trailing_stop": 0.05,     # 移动止损
    # ── 成交量确认因子 ──
    "volume_ma_window": 20,    # 成交量均线窗口
    "volume_ratio_min": 1.5,   # 放量倍数阈值
}
```

### 任务 2：重写 backtest_runner.py —— 多因子策略引擎 【✅ 完成】

**文件**：`laap/paper_trading/backtest_runner.py`

| 函数 | 改动 |
|---|---|
| `_sma` | 保留（简单移动平均） |
| `_rsi` | **重写**：Wilder 平滑，返回长度 = len(prices)，前 period 个 None（修复长度对齐 bug） |
| `_momentum` | **重写**：返回长度 = len(prices)，前 window 个 None |
| `_atr` | **重写**：有 ohlcv 用真实 TR，缺省用 \|close 变化\| 近似；长度对齐 |
| `_volume_ma` | **新增**：成交量均线（无 ohlcv 返回空） |
| `_run_ma_cross` | **保留**：均线交叉基线（向后兼容，旧测试依赖） |
| `_run_multi_factor` | **新增**：多因子引擎（趋势+RSI+ATR+风控+成交量确认），含 `fast_ma>=slow_ma` 防御降级 |
| `BacktestRunner.run_backtest` | 签名不变 + 可选 `ohlcv` 参数 |
| `BacktestRunner.oos_gate` | **不动** |

**多因子买卖逻辑**：

```
买入（全满足）:
  1. 趋势多头: fast_ma > slow_ma 或 momentum > 0
  2. 不过热:   rsi < rsi_overbought
  3. 放量确认: volume >= volume_ma × volume_ratio_min（无 volume 跳过）
卖出（任一触发）:
  趋势转空 / RSI 超买 / 固定止损 / 固定止盈 / 移动止损 / ATR 止损
仓位: cash × position_scale
```

### 任务 3：新增 param_evolver.py —— 参数进化器 【✅ 完成】

**文件**：`laap/paper_trading/param_evolver.py`（新建）

| 组件 | 内容 |
|---|---|
| `PARAM_SPACE` | 14 参数取值范围 (min, max, step)；`_INT_PARAMS` 标记整数参数 |
| `_round_param` | 整数参数取整 / 浮点保留 4 位 |
| `ParamSpace.grid(max_combos=20000)` | 网格展开，**超阈值抛异常**（14 维全组合爆炸防护） |
| `ParamSpace.sample(rng)` | 随机采样（min/max 均匀） |
| `ParamSpace.mutate(params, rng, rate)` | 高斯扰动变异，截断在范围内 |
| `ParamSpace.crossover(a, b, rng)` | 单点交叉 |
| `ParamEvolver._score` | 单组参数 train 段 fitness 评分 |
| `ParamEvolver.grid_search` | 网格搜索（低维可用） |
| `ParamEvolver.random_search(n_samples, seed)` | 随机搜索（可复现） |
| `ParamEvolver.genetic_search(population, generations, seed)` | 遗传进化（精英保留+交叉+变异） |
| `ParamEvolver.evolve(method, train_ratio, oos_ratio)` | **统一入口**：搜索 → 最佳参数 → OOS 门禁，返回完整结构 |

**evolve() 返回结构**：
```python
{
  "method": "random|grid|genetic",
  "best_params": {...14 键...},
  "best_train": {"score", "cumulative_return", "sharpe_ratio", "max_drawdown"},
  "best_oos": {...},
  "gate": {"ok": bool, "reason": str},
  "candidates": [...],   # 全部候选（按 score 降序）
  "n_candidates": N,
}
```

### 任务 4：扩展 quant_evolution.py —— 接线参数进化 + LLM 增强 【✅ 完成】

**文件**：`laap/paper_trading/quant_evolution.py`

| 方法 | 改动 |
|---|---|
| `__init__` | 新增 `param_evolver` 参数（缺省懒创建 `ParamEvolver(runner)`） |
| `evolve()` | **不动**（保留代码级进化，用于叙事） |
| `evolve_params(method, **kwargs)` | **新增**：调 param_evolver，审计双写 `params_evolved/params_rejected` |
| `evolve_with_llm(llm_fn, method, **kwargs)` | **新增**：确定性搜索 → LLM 微调 → 重评分 + 门禁，返回 `base + llm_refined` |
| `approve_and_deploy` / `rollback_last` / `_audit_to_db` / `stats` | **不动** |

**LLM 增强层契约**：
```python
llm_fn(best_params: dict, best_train: dict, context: str) -> dict  # 返回微调后参数
```

### 任务 5：补测试 + 验证 【✅ 完成】

**文件**：`tests/test_param_evolver.py`（新建，19 项测试）

| 测试分组 | 覆盖 |
|---|---|
| 指标长度对齐 | `_sma/_rsi/_momentum/_atr` 返回长度 == len(prices) |
| RSI 边界 | 0 ≤ RSI ≤ 100 |
| 多因子回测 | run_backtest 返回完整 metrics；split 切片；防御降级 |
| OOS 门禁 | fail-closed（负收益拒绝；夏普劣化拒绝） |
| ParamSpace | 采样在界内；整数参数保持整数；变异在界内；网格爆炸防护 |
| 可复现性 | random/genetic 同 seed 两次结果一致 |
| 遗传收敛 | 遗传 score ≥ 随机 score - 0.05（弱断言） |
| evolve 结构 | 返回完整字段 |
| LLM 增强 | llm_fn=None 等价；llm_fn 正常微调；llm_fn 返回 None 容错 |

**测试结果**：
- 新增 19 项：**19 passed**
- 全量：**501 passed / 2 failed / 5 deselected**
- 2 个失败为**预先存在的环境问题**（`tokenizers==0.23.1` 不满足 transformers `<=0.23.0`，embedding provider 回退 TF-IDF），与本次改动无关（git status 确认未改 semantic_memory/依赖文件）

---

## 五、验证结果（真实数据）

### 5.1 阶段 2 交付验证（800 天 × 3 标的，随机搜索 seed=42）

| 标的 | Train score | Train cumret | OOS cumret | OOS sharpe | 门禁 |
|---|---|---|---|---|---|
| 600519 茅台 | 0.330 | +6.57% | -6.77% | -0.81 | ❌ |
| 000001 平安 | 0.732 | +34.48% | -2.38% | -0.37 | ❌ |
| 000858 五粮液 | 0.532 | +19.38% | -0.63% | -0.15 | ❌ |

### 5.2 遗传 vs 随机（茅台 800 天，seed=42）

| 方法 | Train score | OOS cumret |
|---|---|---|
| 随机搜索 | 0.330 | -6.77% |
| **遗传搜索** | **0.568** | **+2.69%** |

**关键观察**：遗传进化在真实数据上正向收敛（train score 0.330→0.568），OOS 由负转正（-6.77%→+2.69%），但受限于 800 天样本 + 14 维参数空间，尚未稳定通过 OOS 门禁。诚实定位：**探索性验证，不宣称实证通过**。

---

## 六、阶段 3：进化适配（✅ 已实施）

> 阶段 3 目标：把阶段 2 的"确定性参数搜索"接上**进化选择压力**与**LLM 微调**，让门禁从"不劣化"升级为"显著优于随机基线"。分 4 个任务，全部按函数/文件级拆分。

### 任务 3.1：fitness 对接（契约影响：低）

**目标**：确立 `compute_trade_fitness` 为**单一交易适应度语义源**，消除 F4 发现的"交易 fitness 与软件健康 fitness 两套体系脱节"，并明确 param_evolver 搜索目标 == compute_trade_fitness 组合分。

**文件**：`laap/paper_trading/trade_fitness.py`

| 改动点 | 内容 |
|---|---|
| 新增常量 `FITNESS_WEIGHTS` | `{"return": 0.4, "sharpe": 0.35, "drawdown": 0.25}`（从 `compute_trade_fitness` 默认值提出，单源） |
| 新增常量 `RETURN_NORM_CAP` / `SHARPE_NORM_CAP` | `0.5` / `2.0`（收益≥50%、夏普≥2.0 归一化满分阈值，单源） |
| `_normalize_return` / `_normalize_sharpe` | 改用上面两个 cap 常量（行为不变） |
| `compute_trade_fitness` | 默认权重改 `dict(FITNESS_WEIGHTS)`；docstring 标注为"交易适应度单源" |
| 新增公开 `daily_returns(net_values)` | 暴露日收益序列（供 3.2 显著性检验复用）；`_daily_returns` 保留为内部别名 |

**文件**：`laap/paper_trading/param_evolver.py`

| 改动点 | 内容 |
|---|---|
| `ParamEvolver._score` | **不动逻辑**，补 docstring 明确"score == compute_trade_fitness 组合分"；直接 import `compute_trade_fitness` 锁定契约 |

**文件**：`laap/paper_trading/quant_evolution.py`

| 改动点 | 内容 |
|---|---|
| `stats()` | 新增 `fitness_mode: "trade_fitness(compute_trade_fitness)"` 与 `llm_refine_available` 字段（双适应度体系统一观测） |

**契约保证**：`compute_trade_fitness` 对外返回键 `{score, cumulative_return, sharpe_ratio, max_drawdown}` **不变**；新增的常量/函数是增量，不破坏既有调用方。param_evolver 搜索排序键始终是 `score`。

**验收测试**：断言 `ParamEvolver._score` 的 `score` == 同一净值序列上 `compute_trade_fitness` 的 `score`（契约锁定）。

---

### 任务 3.2：门禁升级 —— 正收益 + 显著优于随机基线（契约影响：中，向后兼容）

**目标**：`oos_gate` 从"不劣化"（cumret≥0 且 sharpe≥train×0.8）升级为三层 fail-closed：
1. OOS 累计收益 **严格 > 0**（正收益，原为 ≥0）
2. OOS 夏普 ≥ train 夏普 × 0.8（保留不劣化）
3. （可选）策略 OOS 日均收益 **显著优于随机参数基线**（单侧 z ≥ 1.96）

**新文件**：`laap/paper_trading/significance.py`（新建）

| 组件 | 内容 |
|---|---|
| `mean_std(xs)` | (mean, sample std)，空序列返回 (0,0) |
| `daily_return_stats(net_values)` | 复用 `trade_fitness.daily_returns`，返回 `{mean, std, n}` |
| `z_statistic(strategy_stats, baseline_stats)` | Welch 近似 `z = (m_s−m_b) / √(σ_s²/n_s + σ_b²/n_b)` |
| `beats_baseline(strategy_stats, baseline_stats, z_threshold=1.96)` | 样本<2 拒绝；`z ≥ z_threshold` 通过；返回 `(ok, reason)` |

**文件**：`laap/paper_trading/backtest_runner.py`

| 改动点 | 内容 |
|---|---|
| 新增 `BacktestRunner.run_backtest_values(...)` | 返回 `(metrics, net_values)`（net_values 供显著性检验）；`run_backtest` 改为调用它后只返回 metrics（**签名/行为不变**） |
| `oos_gate` 签名升级 | `oos_gate(train_metrics, oos_metrics, strategy_stats=None, random_baseline=None, z_threshold=1.96)`；`strategy_stats`/`random_baseline` 均为可选 dict `{mean,std,n}` |
| `oos_gate` 逻辑 | ① cumret ≤ 0 → 拒；② sharpe 劣化 → 拒；③ 两者都有时调 `beats_baseline`；**三者都缺时行为等价旧版**（向后兼容） |

**文件**：`laap/paper_trading/param_evolver.py`

| 改动点 | 内容 |
|---|---|
| 新增 `ParamEvolver.random_baseline(price_series, split, ohlcv, n_samples, seed)` | 采样 n 组随机参数 → 各自 OOS net_values → 汇总日收益 → `{mean,std,n}` |
| `evolve()` 新增参数 | `significance=False` / `baseline_samples=100` / `baseline_seed=42`；`significance=True` 时算 strategy_stats + random_baseline 并传入 `oos_gate` |

**契约保证**：`oos_gate(train, oos)` 两参调用（既有测试 + QuantEvolutionGate）**不变**；新参数全部可选。`QuantEvolutionGate`（代码级进化门禁）**不在此任务改动**，继续用两参旧语义。

**验收测试**：`tests/test_significance.py` —— z 数学、样本不足拒绝、显著优于随机时通过/不显著时拒绝、random_baseline 统计有效、`evolve(significance=True)` 返回含显著性 gate。

---

### 任务 3.3：LLM 增强接线 —— api.py 注入 llm_fn（契约影响：低）

**目标**：把 `HermesIntegration.llm_call` 包装成 `evolve_with_llm` 需要的 `llm_fn(best_params, best_train, context) -> dict` 契约，由 api 层注入，新端点触发"确定性搜索 → LLM 微调 → 重评分 + 门禁"。

**新文件**：`laap/paper_trading/llm_refine.py`（新建）

| 组件 | 内容 |
|---|---|
| `build_refine_prompt(best_params, best_train, context)` | 生成"你是量化参数优化专家，只返回 JSON 参数"提示词 |
| `parse_params(text)` | 从 LLM 输出提取 JSON dict（找首尾 `{}` + json.loads，失败返回 None） |
| `clamp_params(params, space)` | 仅保留已知键 → 转 float → 截断到 `[lo,hi]` → 整数参数取整 |
| `build_llm_refine_fn(llm_call, param_space=None)` | 返回符合契约的 `llm_fn`；`llm_call` 为 None 时返回 None（纯确定性降级） |

**文件**：`laap/paper_trading/quant_evolution.py`

| 改动点 | 内容 |
|---|---|
| `__init__` | 新增 `llm_fn` 参数，存 `self.llm_fn` |
| `evolve_with_llm` | `llm_fn` 参数缺省时回退 `self.llm_fn`（api 注入生效）；仍兼容显式传参 |

**文件**：`laap_brain/api.py`

| 改动点 | 内容 |
|---|---|
| 新增 `_get_llm_refine_fn()` | 懒建 `HermesIntegration` → `build_llm_refine_fn(hermes.llm_call)`；Hermes 不可用返回 None（降级纯确定性） |
| `_get_quant_engine()` | `QuantEvolutionEngine(...)` 构造时注入 `llm_fn=_get_llm_refine_fn()` |
| 新增端点 `POST /v1/quant/evolve_params` | body `{method, llm:bool, n_samples, seed, population, generations, significance, baseline_samples}`；`llm=true` 走 `evolve_with_llm`，否则 `evolve_params` |
| 注册路由 | `app.router.add_post("/v1/quant/evolve_params", handle_quant_evolve_params)` |

**契约保证**：`llm_fn(best_params, best_train, context) -> dict` 契约不变；`llm_fn=None` 仍等价纯确定性（既有测试 `test_evolve_with_llm_none_equals_params` 保持通过）。

**验收测试**：`tests/test_llm_refine.py` —— prompt 含参数、parse 正常/垃圾/空、clamp 截断+取整、build 返回可调用/None、Hermes 桩返回微调参数走通。

---

### 任务 3.4：补测试 + 全量回归

| 文件 | 覆盖 |
|---|---|
| `tests/test_significance.py`（新建） | 3.2 全部验收项 |
| `tests/test_llm_refine.py`（新建） | 3.3 全部验收项 |
| `tests/test_trade_fitness.py` / `tests/test_param_evolver.py` | 补 3.1 契约断言（param_evolver score == compute_trade_fitness） |
| 全量 `pytest tests -q` | 无新增失败；既有 2 个预存失败（tokenizers 版本）保持隔离说明 |

### 阶段 3 实施结果（✅ 2026-08-15 验证）

**改动文件**（8 个源码 + 2 个新测试）：

| 文件 | 改动 |
|---|---|
| `laap/paper_trading/trade_fitness.py` | 新增 `FITNESS_WEIGHTS` / `RETURN_NORM_CAP` / `SHARPE_NORM_CAP` + 公开 `daily_returns` |
| `laap/paper_trading/significance.py` | **新建**：`mean_std` / `daily_return_stats` / `z_statistic` / `beats_baseline` |
| `laap/paper_trading/backtest_runner.py` | 新增 `run_backtest_values`；`oos_gate` 升级（正收益 + 夏普不劣化 + 可选显著性） |
| `laap/paper_trading/param_evolver.py` | 新增 `random_baseline`；`evolve` 支持 `significance` 层 |
| `laap/paper_trading/llm_refine.py` | **新建**：`build_llm_refine_fn` / `parse_params` / `clamp_params` |
| `laap/paper_trading/quant_evolution.py` | `__init__` 注入 `llm_fn`；`evolve_with_llm` 回退 `self.llm_fn`；`stats()` 增字段 |
| `laap_brain/api.py` | `_get_llm_refine_fn` + `_get_quant_engine` 注入 + 新端点 `POST /v1/quant/evolve_params` |

**测试结果**：
- 阶段 3 相关测试面（paper_trading / quant / rsi 全部）：**146 passed**
- 阶段 3 定向（含新增 significance/llm_refine/契约断言）：**74 passed**
- 全量：**502 passed / 26 failed / 5 deselected**，其中 26 个失败为**预先存在的环境问题**，与本次改动无关：
  - `pytest-asyncio` 未安装 → 21 个 async 测试 `Failed: async def functions are not natively supported`（test_agi_consciousness_integrator / test_api_security / test_laap_api / test_laap_tools）
  - `matplotlib` 未安装 → 3 个图表测试（test_candidate_chart / test_kline_chart）
  - `tokenizers` 版本冲突 → 2 个语义记忆测试（doc 第五节约定的预存失败）
  - `mcp` 未安装 → test_mcp_tools 收集报错（`--ignore` 后其余全绿）

**向后兼容确认**：`oos_gate(train, oos)` 两参调用、`run_backtest` 签名、`evolve_with_llm(llm_fn=...)` 显式传参、`compute_trade_fitness` 返回键 —— 全部保持既有语义；既有 `tests/test_backtest_runner.py` / `test_trade_fitness.py` / `test_param_evolver.py` 无一失败。

---

## 七、阶段 4：真实执行（⏳ 待确认 → 公共基座 + 路径 A 已实施）

> 阶段 4 是**独立立项的大工程**，风险随资金接触面递增。三条路径按资金风险从低到高排列，**默认推荐路径 A 起步**。
> **2026-08-15 复核修复进展**：4.0 公共基座（`load_ohlcv` / `incremental_update`）与路径 A（`run_daily_cycle` / `evaluate_signal`）已实施并 E2E 核验通过（130 项量化测试全绿）；路径 B/C 仍待用户确认。

### 4.0 公共基座（三条路径共用）【✅ 已实施】

**文件**：`laap/paper_trading/kline_source.py`

| 改动点 | 内容 | 状态 |
|---|---|---|
| 新增 `_with_prefix(symbol)` | 裸代码补交易所前缀（600519→sh600519） | ✅ |
| 新增 `load_ohlcv(symbol, days, fallback)` | 从 `watchlist_kline_store.get_kline` 取 `(open,close,high,low,volume)` 五元组，供多因子启用真实 ATR + 成交量因子；失败降级合成 | ✅ |
| 新增 `incremental_update(symbol, days)` | `akshare.stock_zh_a_hist` 增量拉取 → `upsert_kline` 落 `kline.db`，幂等去重 | ✅（沙箱无 akshare 返回 0 降级） |

**文件**：`laap/paper_trading/market_source.py`

| 改动点 | 内容 | 状态 |
|---|---|---|
| `LiveMarketSource` | 已含 `stock_bid_ask_em` 单只报价 + Stub 运行时降级；`stock_zh_a_spot_em` 批量快照待路径 C 时补 | ⏳ 待路径 C |

### 路径 A：paper trading + 真实行情（✅ 推荐起步，无资金风险）【✅ 已实施】

**文件**：`laap/paper_trading/paper_service.py`

| 改动点 | 内容 | 状态 |
|---|---|---|
| 新增 `PaperClosedLoop.run_daily_cycle(symbols, params, ohlcv_map, runner)` | 日终闭环：`load_ohlcv` → 多因子信号（`evaluate_signal`）→ buy 下单 / sell 平仓 / hold → `settle` 净值快照 | ✅ |
| 新增 `_latest_decision_id(symbol)` | sell 平仓时关联该标的最新的决策键（追溯链闭环） | ✅ |
| `decide_and_trade` | **不动**（已接记忆注入 + 决策留痕 + 成交） | ✅ |

**文件**：`laap/paper_trading/backtest_runner.py`

| 改动点 | 内容 | 状态 |
|---|---|---|
| 新增 `BacktestRunner.evaluate_signal(prices, params, ohlcv, position_held)` | 对最后一个 bar 做多因子信号判定（复用 `_run_multi_factor` 买卖条件），返回 buy/sell/hold + reason——**阶段 4 实盘信号源** | ✅ |

**文件**：`laap/paper_trading/ledger.py` / `settle.py` / `db.py` | **不动**（T+1、幂等、净值快照已具备） | ✅

**验收（已通过）**：E2E 核验——涨→回调→温和反弹触发 buy 建仓，随后下跌触发 sell 平仓，教训沉淀进 UnifiedMemory（7 条命中），净值快照落库；`tests/test_paper_phase4.py` 11 项测试；量化全量 **130 passed / 0 failed**。

### 路径 B：券商 API 对接（需用户提供券商/密钥，谨慎评估）【⏳ 待用户确认】

**新文件**：`laap/paper_trading/broker.py`（待建）

| 改动点 | 内容 |
|---|---|
| 新增 `BrokerAdapter` 抽象 | `submit_order` / `cancel_order` / `get_position` / `get_account` 接口 |
| 新增 `PaperBrokerAdapter` | 把 `PaperLedger` 包成同接口（路径 A 即此实现的雏形） |
| 新增 `RealBrokerAdapter` 占位 | 待用户选定券商后实现（如 qmt/easytrader/券商官方 SDK），**不预埋任何密钥** |

**文件**：`laap/paper_trading/ledger.py`

| 改动点 | 内容 |
|---|---|
| `fill_order` / `close_trade` | 保留 paper 路径；通过依赖注入可选切换 `BrokerAdapter`（**默认仍 paper，不接真实下单**） |

**验收（前置）**：用户确认券商 + 提供 sandbox/paper 级密钥；先券商 sim 盘对账，再谈实盘。

### 路径 C：实时行情推送 + 实时信号（高级，最后做）【⏳ 待用户确认】

**新文件**：`laap/paper_trading/live_feed.py`（待建）

| 改动点 | 内容 |
|---|---|
| 新增 `LiveFeedPoller` | akshare 定时轮询（如 5s/30s），推送最新价到 `MarketSource` |
| 新增 `SignalScheduler` | 按阶段 3 参数实时计算信号，触发 `PaperClosedLoop`（复用 `laap_brain` 现有调度器模式） |

**验收**：盘中 1 小时，信号触发与成交记录完整、可回溯，且**全程 paper，不碰真实资金**。

### 阶段 4 决策项（用户确认后才动工）

- [x] 路径选择：**A（推荐）**——已实施
- [ ] 路径 B（券商 API）/ 路径 C（实时推送）——待确认
- [ ] 标的池：默认 `600519 / 000001 / 000858`（阶段 2 验证过的三只），是否扩展
- [ ] 资金：paper 起始资金 1,000,000（沿用 `PaperLedger` 默认），是否调整

---

## 八、待用户决策

- [x] **是否开始阶段 3**（进化适配：门禁升级 + LLM 接线）→ 已于 2026-08-15 实施完成
- [ ] **是否提交阶段 1+2+3 代码**（注意：`laap/paper_trading/` 在 .gitignore，按"量化交易不同步 NAS"约定为本地文件；新增 `tests/test_param_evolver.py` / `tests/test_significance.py` / `tests/test_llm_refine.py` 为未跟踪新文件）
- [ ] **阶段 4 走哪条路**（路径 A paper trading + 真实行情【推荐】/ 路径 B 券商 API / 路径 C 实时推送）—— 详见第七节

---

## 九、收敛：代码级接通（params→code 落回，2026-08-15）

> 复盘中指出"RSI 引擎在执行链路里从代码级自改进漂移成参数搜索器"。本次收敛：**让 param_evolver 搜索成果以代码形式落回 strategy.py，让 M4 受限递归重新生效**——参数搜索不再是孤立的"结果",而是变成一次走 M1-M4 治理链的代码级变更。

### 9.1 实现

**文件**：`laap/paper_trading/param_extractor.py`

| 新增 | 功能 |
|---|---|
| `serialize_params(params) -> str` | 参数字典 → `STRATEGY_PARAMS = {...}` 代码块（键序保持 strategy.py，整数参数输出 int） |
| `params_to_code(old_code, params) -> str` | 替换既有 STRATEGY_PARAMS 赋值（正则锚定扁平 dict）/ 无则追加；结果可被 `extract_strategy_params` 完整还原（往返契约） |

**文件**：`laap/paper_trading/quant_evolution.py`

| 新增 | 功能 |
|---|---|
| `QuantEvolutionEngine.apply_params_to_code(best_params, rationale, method, auto_deploy)` | 把搜索成果构造为对 strategy.py 的 `CodeMutation`，**全程走 M4 治理链**：SafetyGuard 安全校验 → 沙箱测试 → `deploy_gate`（QuantEvolutionGate 对 strategy.py 做 mutation 前后 OOS 对比）→ 待人工审批 → 审计双写 |

### 9.2 M4 受限递归治理链（这次真正生效）

```
evolve_params() 搜索 best_params
  → apply_params_to_code()
      ① SafetyGuard.validate_mutation   （危险模式 / 变更比例 / 语法）
      ② tester.test_mutation            （沙箱语法 + import 白名单）
      ③ deploy_gate（QuantEvolutionGate）→ strategy.py 走 mutated vs baseline OOS 对比
      ④ awaiting_approval（approved=False，不自动部署）
      ⑤ approve_and_deploy() 人工批准 → git.deploy 写回 strategy.py → 审计
```

部署后 `run_daily_cycle` 直接读 `strategy.STRATEGY_PARAMS` 即拿到新参数——**搜索→代码→执行闭环真正闭合**（此前有"最优参数不自动回接执行"的断点）。

### 9.3 验证

- `tests/test_params_to_code.py` 8 项：serialize/params_to_code 往返契约、replaces/appends、awaiting_approval、gate_blocked（fail-closed）、no_change、沙箱拒绝坏代码、审批部署后 strategy.py 文件更新。
- 集成 E2E：真实 `evolve_params(random, n=30)` → `apply_params_to_code` → deploy_gate 对 strategy.py 做 OOS 对比，合成数据上 OOS 收益 0 → **gate fail-closed 拒绝**（符合预期，治理生效）。
- 量化全量 **138 passed / 0 failed**（+8，零回归）。

### 9.4 残留说明

- 真实 OOS 门禁的"通过"路径需在真实历史 K 线上出现 `mutated OOS 不劣于 baseline` 才算数——目前合成数据被拒是诚实的负结果；通过路径已被单测（stub gate）覆盖。
- 代码仍遵循"`laap/paper_trading/` 本地文件不进 git"约定，未提交。

---

## 十、交易自我（TradingSelf）：人格 × 自我模型 → 判断/审核 → 下达指令（2026-08-15）

> 复盘后用户提出"代码级接通远远不够，要有真正的人格和自我意识，判断/审核，下达指令"。本阶段把 AGI 认知栈接入交易执行链路，让系统成为**有主体性的交易员**。
>
> 用户三项决策：**规则化自我审核** + **自主执行 paper 交易** + **接现有 /v1/personality**。补充要求：每个决策要"给出有意义和利益的决策"。

### 10.1 架构

```
TradingSelf（laap/paper_trading/trading_self.py）
  ├─ personality（/v1/personality 五维 traits）
  │    └─ trading_identity(): 风险偏好/纪律/仓位上限/止损  ← 人格约束
  ├─ EmergentSelfModel（经验中涌现的自我认知）
  │    └─ self_assess / record_experience / stats           ← 自我评估 + 反思
  ├─ UnifiedMemory（教训检索）                               ← 记忆约束
  └─ judge() → issue() → reflect_on_trade()                  ← 判断/指令/反思
```

**判断（judge）——"有意义和利益的决策"**：
1. **利益层**：OOS 收益/夏普 → 综合值得度（进化提案场景要求正收益；实盘信号不强制）
2. **人格层**：仓位不超人格上限（Aris·忠诚守护者 61%）
3. **记忆层**：买入时历史负面教训 → 审慎（卖出是风控不阻止）
4. **自我层**：自我效能过低/有经历但未达中性 → 观望

每个判断输出 `verdict(approve/reject/abstain) + meaning(意义) + benefit(利益)`。

**下达指令（issue）**：审核通过后自主执行 buy/sell，rationale 含 `[self]身份声明 + [benefit]利益 + [memory]记忆` 三层主体叙事；事后 `reflect_on_trade` 更新自我模型。

### 10.2 接入

`PaperClosedLoop` 新增 `trading_self` 参数；`run_daily_cycle` 在技术信号之上由 TradingSelf 审核后自主执行（`self_verdict` 标注）；`build_paper_closed_loop` 默认挂载 TradingSelf。

### 10.3 验证

- `tests/test_trading_self.py` 12 项：人格推导 / 判断三态 / 人格仓位约束 / OOS 负收益拒绝 / 记忆负面教训审慎 / 卖出不受教训阻止 / 自我效能审慎 / issue 主体叙事 / sell 平仓+反思 / run_daily_cycle 自主执行。
- E2E：身份"我是Aris·忠诚守护者"→ DAY1 buy 审核通过自主下单 → DAY2 sell 审核通过自主平仓 → 自我模型 total_actions=1, self_efficacy=0.55。
- 量化全量 **153 passed / 0 failed**（+12 TradingSelf，零回归）。

### 10.4 进化提案自我审核 + /v1/quant API 暴露（2026-08-15 续）

**进化提案由"自我"审核（取代纯 OOS 门禁）**：
- `TradingSelf.judge_proposal(params, train_metrics, oos_metrics)`：四层——人格约束（position_scale 不超上限、stop_loss 不超纪律参考）+ 利益（OOS 正收益）+ 记忆教训 + 自我评估，输出 verdict/meaning/benefit。
- `apply_params_to_code` 新增 `self_review` 参数（默认 True）：deploy_gate（OOS 对比）**之后**插入 `judge_proposal`，verdict≠approve → `self_blocked`（含 meaning + benefit，审计落库 `params_code_rejected`）。
- 治理链升级为：`SafetyGuard → 沙箱 → deploy_gate(OOS) → TradingSelf.judge_proposal → 人工审批 → 部署`。

**/v1/quant API 暴露 TradingSelf**：
- `GET /v1/quant/self/status`：返回身份声明、交易人格（风险偏好/纪律/仓位上限）、personality preset+traits、自我模型 stats、记忆教训数。
- `POST /v1/quant/evolve_params` 新增 `apply_code: bool`（true 时搜索结果落回代码走 M4 治理）+ `self_review: bool`（默认 true）。
- `_get_trading_self()` 单例注入 `QuantEvolutionEngine`。

**验证**：`test_params_to_code.py` 补 5 项（judge_proposal approve/reject/仓位约束、self_review 阻断、self_review 关闭）；`test_quant_api.py` 补 4 项（self/status 路由+返回+不可用、evolve_params apply_code）。量化全量 **162 passed / 0 failed**（+9）。E2E：真实人格 Aris·忠诚守护者加载 → 搜索 → apply_params_to_code → deploy_gate 先拦（OOS fail-closed 负结果，符合治理顺序）；self_review 路径由单测覆盖（gate 放行后自我判断）。
