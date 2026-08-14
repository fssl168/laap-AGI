# RSI 接入量化框架：记忆 × 自进化闭环 实施清单（文件 + 函数级）

> **日期**: 2026-08-15
> **任务**: 参考 DSA 的 `paper_trading`，在 AGI 项目内新建一个最小但真实的 paper_trading 框架，让 AGI 的 UnifiedMemory（记忆）+ RSI/进化（自进化）接上"真实交易决策反馈"闭环。
> **关联**: `docs/memory-evolution-closed-loop-plan.md`（方向）、`docs/true-rsi-feasibility.md`（M1-M4 基础设施）
> **交付物**: 本清单（文件+函数级，分阶段有序推进）

---

## 0. 目标与原则

**目标**：新建 `laap/paper_trading/` 框架，承载"信号→订单→成交→净值"的最小真实交易循环；把两条闭环接入——记忆侧（决策→经验→参与推理）、自进化侧（交易适应度→样本外调参→人工审批）。

**原则**（对齐项目定位——后端组织能力 + Agent 自我学习/反思是核心，前端/冗余从简）：

1. **复用优先**：记忆用 `UnifiedMemory`，参数级自进化用 `RSIMetaEngine`，治理用 `EvolutionAuditLog` + `/v1/evo/*` 语义，零新外部依赖。
2. **最小闭环**：只建承载闭环的骨架，不复制 DSA 的前端 / 完整 OMS / 券商路由 / 固收。
3. **回放优先**：行情源默认回放（历史 K 线 / 合成行情），真实源（akshare/Longbridge）作可选接法，沿用 DSA "在线 + stub fallback" 模式。
4. **默认关、可回滚、人工审批**：延续 M1-M4 治理；代码级自改进（M4 true_rsi.py）保持默认关闭，本清单先跑通**参数级**闭环。

---

## 1. 架构总览

### 1.1 新包结构（`laap/paper_trading/`）

```
laap/paper_trading/
├── __init__.py           # 包入口 + 导出
├── models.py             # 数据模型（dataclass + 枚举）
├── ledger.py             # 最小 OMS（下单/成交/平仓/净值，JSONL 持久化）
├── market_source.py      # 行情源（Replay/Stub/Live 三实现）
├── settle.py             # 日终结算（MTM 净值）
├── decision_record.py    # 决策留痕 + 结果回填 + 教训提炼   ← 闭环 A 前半
├── memory_bridge.py      # 记忆桥接（沉淀/检索/注入 prompt） ← 闭环 A 后半
├── trade_fitness.py      # 交易适应度（收益/夏普/回撤）      ← 闭环 B 前半
├── backtest_runner.py    # 样本外回测 runner（时间切分 + OOS 门禁）
├── auto_tuner.py         # 受限自动调参（接 RSIMetaEngine）  ← 闭环 B 后半
└── paper_service.py      # 装配层（参考 DSA build_full_listener）
```

### 1.2 复用映射（AGI 已有能力 → 闭环接入点）

| AGI 已有能力 | 文件/函数 | 闭环接入点 |
|---|---|---|
| 记忆-经验 | `UnifiedMemory.encode_experience(content, ..., context_triggers)` | 教训沉淀（A） |
| 记忆-检索 | `UnifiedMemory.retrieve_context / generate_memory_prompt / query` | 参与推理（A） |
| 参数级 RSI | `RSIMetaEngine.suggest_improvements(performance_metrics)` / `apply_improvement` / `evaluate_improvement` | 受限调参（B） |
| 进化治理 | `EvolutionAuditLog.record(mutation, decision, reason, meta)` | 调参审计（B） |
| 路由 | `api.py` 的 `handle_rsi_*` / `handle_evo_*` | 新 `/v1/pt/*` 路由参照 |
| 状态存储 | `state/*.jsonl`（evolution_audit 同款） | 账本/决策留痕落库 |

---

## 2. 阶段 P0 — paper_trading 最小交易循环

> **目标**：让"交易决策→结果"有真实载体，闭环 A/B 才能吃上数据。约 1 人日。

### 2.1 新文件 `laap/paper_trading/models.py`

| 符号 | 类型 | 字段（关键） | 说明 |
|---|---|---|---|
| `OrderStatus` | Enum | `pending / filled / canceled` | 订单状态 |
| `DecisionAction` | Enum | `buy / sell / hold` | 决策动作 |
| `PaperSignal` | dataclass | `symbol, action, quantity, trigger_price, ts, rationale` | 信号（参考 DSA `DecisionSignal→PaperSignal`） |
| `PaperOrder` | dataclass | `signal_id, status, fill_price, filled_ts, client_request_id` | 订单 |
| `PaperTrade` | dataclass | `order_id, symbol, side, quantity, entry_price, exit_price, pnl, pnl_pct, hold_days` | 成交/持仓 |
| `DecisionRecord` | dataclass | `trade_id, symbol, action, ts, rationale, basis_memories(list), risk_note, expected` | 决策留痕（闭环 A 载体） |
| `OutcomeRecord` | dataclass | `trade_id, outcome{pnl_pct,hold_days,vs_expected}, lesson, lesson_type, verified` | 结果回填（闭环 A 载体） |
| `PaperNetValue` | dataclass | `ts, cash, equity, total` | 净值快照 |

### 2.2 新文件 `laap/paper_trading/ledger.py`

| 函数 | 职责 | 验收点 |
|---|---|---|
| `class PaperLedger` | 最小 OMS，内存 + JSONL 持久化（`state/paper_ledger.jsonl`） | 重启可恢复 |
| `__init__(repo_root)` | 初始化账本 + `_load()` | |
| `submit_signal(signal, client_request_id=None) -> PaperOrder` | 下单，`client_request_id` 幂等（参考 DSA T-13） | 重复 id 返回同一订单 |
| `fill_order(order_id, fill_price) -> PaperTrade` | 成交 | 状态 pending→filled |
| `cancel_order(order_id) -> PaperOrder` | 撤单 | 仅 pending 可撤 |
| `close_trade(trade_id, exit_price) -> PaperTrade` | 平仓，算 `pnl/pnl_pct/hold_days` | pnl 计算正确 |
| `net_values() -> List[PaperNetValue]` | 净值序列 | 供适应度/回测 |
| `_persist()` / `_load()` | JSONL 读写（复用 evolution_audit 风格） | 原子追加 |

### 2.3 新文件 `laap/paper_trading/market_source.py`

| 符号 | 职责 | 验收点 |
|---|---|---|
| `class MarketSource` | 抽象基类，`get_price(symbol, ts=None) -> float` | |
| `class ReplayMarketSource(MarketSource)` | 回放历史 K 线（默认，跑通闭环） | 按 ts 返回历史价 |
| `class StubMarketSource(MarketSource)` | 合成行情（模拟交易日，参考 DSA `simulate_trading_days.py`） | 净值曲线连续 |
| `class LiveMarketSource(MarketSource)` | 可选：akshare 轮询 + `used_fallback` 标记（参考 DSA） | 离线降级 stub |

### 2.4 新文件 `laap/paper_trading/settle.py`

| 函数 | 职责 | 验收点 |
|---|---|---|
| `class Settlement` | 日终结算 | |
| `daily_settle(date, ledger, market) -> PaperNetValue` | 持仓 MTM 结算（参考 DSA `Settlement.daily_settle`，注意 T+1 可用量） | 净值连续、pos_value 正确 |

**P0 测试**：`tests/test_paper_ledger.py`（下单/成交/平仓/幂等/pnl）、`tests/test_paper_settle.py`（MTM 净值）、`tests/test_market_source.py`（回放/合成）。

---

## 3. 阶段 P1 — 记忆闭环（规划文档 A-1 / A-2）

> **目标**：决策留痕 → 结果回填 → 教训沉淀 → 参与推理。约 1.5 人日。

### 3.1 新文件 `laap/paper_trading/decision_record.py`（A-1 留痕/回填/沉淀）

| 函数 | 职责 | 验收点 |
|---|---|---|
| `record_decision(symbol, action, rationale, basis_memories, risk_note, expected) -> DecisionRecord` | 决策留痕，落 `state/paper_decisions.jsonl` | 含 rationale + basis_memories |
| `close_position(trade_id, exit_price, market) -> OutcomeRecord` | 平仓回填 `outcome`，生成 `lesson` | vs_expected 正确 |
| `_derive_lesson(outcome) -> (lesson, lesson_type)` | 规则化教训提炼（如 `pnl<0 且 hold_days<3 → short_term_chase`；`pnl<0 且未止损 → no_stop_loss`） | lesson_type 稳定可检索 |

### 3.2 新文件 `laap/paper_trading/memory_bridge.py`（A-1 沉淀 / A-2 参与推理）

| 函数 | 职责 | 复用 AGI 接口 | 验收点 |
|---|---|---|---|
| `lesson_to_experience(outcome) -> str` | 把 outcome+lesson 序列化成经验文本 | — | 文本含 symbol/lesson_type/结果 |
| `encode_lesson(memory, outcome) -> str` | 沉淀教训 → `memory.encode_experience(content, context_triggers=[symbol, lesson_type])`，返回 episode_id | `encode_experience` | 落库可查 |
| `retrieve_for_symbol(memory, symbol) -> List[Dict]` | 同标的经验检索 | `memory.query(symbol)` / `retrieve_context` | 命中相关经验 |
| `inject_memory_prompt(memory, symbol, action) -> str` | 生成注入决策的 prompt | `generate_memory_prompt` | prompt 含历史教训 |
| `verify_lessons(memory, lesson_type, min_confirm=2) -> Dict` | 教训校验：同 `lesson_type` 累计 ≥min_confirm 次真实平仓才 `verified=True`（防单笔噪声污染） | `memory.query` | 未验证教训不参与推理 |

### 3.3 修改文件 `laap/paper_trading/ledger.py`（A-2 参与推理入口）

| 函数 | 改动 | 验收点 |
|---|---|---|
| `submit_signal(...)` | 增加可选参数 `memory: UnifiedMemory`；下单前 `inject_memory_prompt` 注入，`rationale` 必须引用 `basis_memories` | 下单决策含记忆依据 |

### 3.4 修改文件 `laap_brain/api.py`（A 侧路由）

| 新增 | 职责 | 参考 |
|---|---|---|
| `handle_pt_decision_record` | `POST /v1/pt/decisions` — 决策留痕 | `handle_rsi_improve` 模式 |
| `handle_pt_lessons` | `GET /v1/pt/lessons?symbol=&lesson_type=&verified=` — 查询教训 | `handle_evo_audit` 模式 |

**P1 测试**：`tests/test_decision_record.py`（留痕/回填/教训提炼）、`tests/test_memory_bridge.py`（沉淀/检索/注入/校验）。

**P1 验收标准**（对齐规划文档 3.5）：
1. 连续 N 笔平仓后，`encode_experience` 写入 ≥ N 条经验记忆；
2. 新决策 `retrieve_context` 命中同标的/同 lesson_type 并注入 prompt；
3. `trade_id → DecisionRecord → OutcomeRecord → lesson` 完整可追溯；
4. 未验证教训标记 `verified=False`，不参与推理（或仅弱信号）。

---

## 4. 阶段 P2 — 自进化闭环（规划文档 B-1 / B-2）

> **目标**：让参数级自进化指向交易业绩，样本外纪律 + 人工审批。约 1.5 人日。

### 4.1 新文件 `laap/paper_trading/trade_fitness.py`（B-1 交易适应度）

| 函数 | 职责 | 验收点 |
|---|---|---|
| `_cumulative_return(net_values) -> float` | 累计收益 | [0,∞) |
| `_sharpe_ratio(net_values, rf=0.0) -> float` | 夏普比率（参考 DSA SignalFusion 按 Sharpe 加权思路） | 合理区间 |
| `_max_drawdown(net_values) -> float` | 最大回撤 | [0,1] |
| `compute_trade_fitness(trades, net_values) -> Dict` | 组合：`score = 0.4*收益_norm + 0.35*夏普_norm + 0.25*(1-回撤)` | score∈[0,1]，分量可诊断 |

> **双门槛原则**：进化只接受"软件健康度不降（现有 FitnessEvaluator）+ 交易适应度提升"的变更。

### 4.2 新文件 `laap/paper_trading/backtest_runner.py`（B-1 OOS 纪律）

| 函数 | 职责 | 验收点 |
|---|---|---|
| `class BacktestRunner` | 样本外回测 runner | |
| `split_series(dates, train=0.6, valid=0.2, oos=0.2) -> (train, valid, oos)` | **时间切分**（非随机，防未来函数） | 三段无重叠、按时间序 |
| `run_backtest(params, split) -> Dict[metrics]` | 在指定段回放参数下的交易 | 返回 trade_fitness 分量 |
| `oos_gate(train_metrics, oos_metrics) -> bool` | OOS 不劣化门禁（如 `oos.cumret >= 0` 且 `oos.sharpe >= train.sharpe * 0.8`） | fail-closed：不满足即拒 |

### 4.3 新文件 `laap/paper_trading/auto_tuner.py`（B-2 受限自动调参）

| 函数 | 职责 | 复用 AGI 接口 | 验收点 |
|---|---|---|---|
| `class RestrictedAutoTuner` | 受限自动调参编排 | | |
| `__init__(rsi, runner, audit)` | 注入 RSIMetaEngine + BacktestRunner + EvolutionAuditLog | | |
| `propose_tuning() -> List[dict]` | 把 `trade_fitness` 作 `performance_metrics` 喂 `rsi.suggest_improvements(performance_metrics)` | `suggest_improvements` | 返回候选参数+rationale |
| `evaluate_proposal(param, new_value, rationale) -> Dict` | 沙箱回测 train/valid → `oos_gate` 校验 | `run_backtest` / `oos_gate` | 不满足 OOS 即拒绝 |
| `apply_approved(attempt_id, approver) -> Dict` | 人工批准后 `rsi.evaluate_improvement(attempt_id, performance_change)` + `audit.record` | `evaluate_improvement` / `audit.record` | 审计落库、可追溯 |
| `_audit_tuning(decision, reason, meta)` | 调参全决策点写 `state/evolution_audit.jsonl` | `EvolutionAuditLog.record` | 同 M3 审计 |

> **治理约束**：调参提案默认 `awaiting_approval`，走人工审批（参考 M3 `/v1/evo/deploy` 语义）；落地后可回滚（`RSIMetaEngine` 的 `save/load` + 参数快照）。

### 4.4 修改文件 `laap_brain/api.py`（B 侧路由）

| 新增 | 职责 | 参考 |
|---|---|---|
| `handle_pt_tune_suggest` | `POST /v1/pt/tune/suggest` — 产调参提案 | `handle_rsi_status` |
| `handle_pt_tune_approve` | `POST /v1/pt/tune/approve` — 人工批准（`{"attempt_id", "approver"}`） | `handle_evo_deploy` |
| `handle_pt_tune_reject` | `POST /v1/pt/tune/reject` — 拒绝并回滚 | `handle_evo_rollback` |

**P2 测试**：`tests/test_trade_fitness.py`（收益/夏普/回撤/组合）、`tests/test_backtest_runner.py`（时间切分/OOS 门禁 fail-closed）、`tests/test_auto_tuner.py`（提案→回测→OOS 拒绝→批准→审计）。

**P2 验收标准**（对齐规划文档 4.4）：
1. 一次受限调参完整走通：提案 → 沙箱回测（train/valid/OOS）→ OOS 不劣化 → 人工批准 → 落地 → 审计；
2. 落地后可回滚，回滚后现有 379 项测试不回归；
3. `trade_fitness` 与 OOS 结果进 `state/evolution_audit.jsonl`；
4. 未满足 OOS 纪律的提案自动拒绝（fail-closed）。

---

## 5. 阶段 P3 — 收尾验收（约 0.5 人日）

| 项 | 内容 |
|---|---|
| 端到端 | 用 `StubMarketSource` 模拟 5 交易日：留痕→成交→平仓→教训沉淀→下一次决策注入→调参提案→OOS 门禁→人工批准，全链路跑通 |
| 回归 | `pytest tests -q --ignore=tests/test_mcp_tools.py` 失败数不增（当前基线 **379 passed / 0 failed**，4 个 pre-existing 环境失败除外） |
| 文档 | 更新 `docs/memory-evolution-closed-loop-plan.md` 实施进度 + 本清单勾选 |
| 门禁 | 新增 `scripts/check_pt_closure.py`（可选）：断言 paper_decisions/paper_ledger/evolution_audit 三处 JSONL 一致、闭环无断点 |

---

## 6. 设计假设与待确认项

| # | 假设（默认） | 待用户确认 |
|---|---|---|
| 1 | 落点 `laap/paper_trading/`（AGI 项目内新包） | 是否改名（如 `laap/quant/`） |
| 2 | 行情源**回放优先**，真实源可选接法 | 是否首版就要真实行情（Longbridge/akshare） |
| 3 | 存储用 **JSONL**（`state/*.jsonl`，与 evolution_audit 一致），不引入 SQLite | 是否对标 DSA 用 SQLite（stock_analysis.db） |
| 4 | 自进化先跑**参数级**（RSIMetaEngine），代码级 M4 保持默认关 | 是否本次就开代码级 |
| 5 | 新建 `/v1/pt/*` 路由，不复用 `/v1/evo/*` | 路由命名偏好 |

> **关键约束（勿重踩，来自记忆）**：沙箱跑测试必须 `TMPDIR=/tmp`（挂载盘 SQLite 9p `disk I/O error`）；真实 DB 核验先 `cp` 到 `/tmp`；涉全局单例（DatabaseManager/get_db）的测试需注入隔离；提交前 `git show --stat` 核对改动量、警惕 CRLF 行尾噪音。

---

## 7. 阶段总览

| 阶段 | 内容 | 新文件 | 测试 | 预计 |
|---|---|---|---|---|
| P0 | 最小交易循环 | models/ledger/market_source/settle | 3 个测试文件 | 1d |
| P1 | 记忆闭环 | decision_record/memory_bridge | 2 个测试文件 | 1.5d |
| P2 | 自进化闭环 | trade_fitness/backtest_runner/auto_tuner | 3 个测试文件 | 1.5d |
| P3 | 收尾验收 | check_pt_closure.py（可选） | 端到端 + 回归 | 0.5d |

**总计约 4.5 人日，10 个新文件 + 2 处 api.py 修改，零新外部依赖。**
