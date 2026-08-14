# RSI 接入量化框架：记忆 × 自进化闭环 实施清单 v2（文件 + 函数级）

> **日期**: 2026-08-15
> **状态**: ✅ P0-P3 全部完成（63 项测试，442 passed / 0 failed）。复核审计见 `docs/quant-closure-audit.md`。
> **任务**: 参考 DSA 的 `paper_trading`，在 AGI 项目内新建最小但真实的 paper_trading 框架，让 UnifiedMemory（记忆）+ 代码级受限递归（自进化）接上"真实交易决策反馈"闭环。
> **关联**: `docs/memory-evolution-closed-loop-plan.md`（方向）、`docs/true-rsi-feasibility.md`（M1-M4）
> **交付物**: 本清单（P0-P3 有序推进）

---

## 0. 目标与已确认决策

**目标**：新建 `laap/paper_trading/` 框架，承载"信号→订单→成交→净值"最小真实交易循环；接入两条闭环——记忆侧（决策→经验→参与推理）、自进化侧（代码级受限递归 + 样本外回测门禁 + 人工审批）。

**已确认决策（用户拍板）**：

| # | 决策 | 落地影响 |
|---|---|---|
| 1 | 落点 `laap/paper_trading/`（不改） | 新包路径固定 |
| 2 | 首版用**真实源** | market_source 真实源优先（akshare/Longbridge）+ stub fallback + `used_fallback` 标记 |
| 3 | 存储用 **SQLite** | ledger/decision_record 用 SQLite（对标 DSA `stock_analysis.db`） |
| 4 | 自进化跑**代码级** | P2 用 M4 受限递归（CodeEvolutionEngine + TrueRSIEngine）+ 交易适应度 OOS 门禁 |
| 5 | 新建 **`/v1/quant/*`** | api.py 新路由前缀 |

**原则**（对齐项目定位）：复用优先、最小闭环、真实源 + 降级、默认关 + 可回滚 + 人工审批（M1-M4 治理延续）。

---

## 1. 架构总览

### 1.1 新包结构（`laap/paper_trading/`）

```
laap/paper_trading/
├── __init__.py           # 包入口 + 导出
├── models.py             # 数据模型（dataclass + 枚举）
├── db.py                 # SQLite 持久化（schema + 连接管理）
├── ledger.py             # 最小 OMS（下单/成交/平仓/净值）
├── market_source.py      # 行情源（Live 真实优先 / Stub fallback）
├── settle.py             # 日终结算（MTM 净值）
├── decision_record.py    # 决策留痕 + 结果回填 + 教训提炼   ← 闭环 A 前半
├── memory_bridge.py      # 记忆桥接（沉淀/检索/注入 prompt） ← 闭环 A 后半
├── trade_fitness.py      # 交易适应度（收益/夏普/回撤）      ← 闭环 B 前半
├── backtest_runner.py    # 样本外回测 runner（时间切分 + OOS 门禁）
├── quant_evolution.py    # 代码级受限递归编排（接 M4）      ← 闭环 B 后半
└── paper_service.py      # 装配层（参考 DSA build_full_listener）
```

### 1.2 复用映射

| AGI 已有能力 | 文件/函数 | 闭环接入点 |
|---|---|---|
| 记忆-经验 | `UnifiedMemory.encode_experience(content, ..., context_triggers)` | 教训沉淀（A） |
| 记忆-检索 | `UnifiedMemory.retrieve_context / generate_memory_prompt / query` | 参与推理（A） |
| 代码级进化 | `CodeEvolutionEngine._improve_single`（scope_guard 钩子）+ `TrueRSIEngine`（受限递归） | 代码级自改进（B） |
| 进化治理 | `EvolutionAuditLog.record(mutation, decision, reason, meta)` | 进化审计（B） |
| 路由 | `api.py` 的 `handle_rsi_*` / `handle_evo_*` | 新 `/v1/quant/*` 参照 |
| 状态存储 | SQLite（对标 DSA）+ `state/evolution_audit.jsonl`（进化审计） | 账本/留痕/审计 |

---

## 2. 阶段 P0 — 最小真实交易循环（SQLite + 真实源）

> **目标**：让"交易决策→结果"有真实载体。约 1.5 人日（真实源 + SQLite 增加量）。

### 2.1 新文件 `laap/paper_trading/models.py`

| 符号 | 类型 | 字段（关键） | 说明 |
|---|---|---|---|
| `OrderStatus` | Enum | `pending / filled / canceled` | |
| `DecisionAction` | Enum | `buy / sell / hold` | |
| `PaperSignal` | dataclass | `symbol, action, quantity, trigger_price, ts, rationale` | 参考 DSA `DecisionSignal→PaperSignal` |
| `PaperOrder` | dataclass | `signal_id, status, fill_price, filled_ts, client_request_id` | |
| `PaperTrade` | dataclass | `order_id, symbol, side, quantity, entry_price, exit_price, pnl, pnl_pct, hold_days` | |
| `DecisionRecord` | dataclass | `trade_id, symbol, action, ts, rationale, basis_memories, risk_note, expected` | 闭环 A 载体 |
| `OutcomeRecord` | dataclass | `trade_id, outcome, lesson, lesson_type, verified` | 闭环 A 载体 |
| `PaperNetValue` | dataclass | `ts, cash, equity, total` | 净值快照 |

### 2.2 新文件 `laap/paper_trading/db.py`（决策 #3 SQLite）

| 符号 | 职责 | 验收点 |
|---|---|---|
| `DB_PATH = <LAAP_ROOT>/data/paper_trading.db` | 库路径 | 可注入（测试用 tmp） |
| `class PaperDB` | 连接管理 + schema 初始化 | |
| `__init__(db_path=None)` | 建库 + `_init_schema()` | 幂等建表 |
| `_init_schema()` | 建表：signals / orders / trades / net_values / decisions / outcomes / evolutions | 表结构可迁移 |
| `conn()` | 返回 sqlite3 连接（`check_same_thread=False` + row_factory） | 线程安全 |

**schema（核心表）**：
```
signals(id, symbol, action, quantity, trigger_price, ts, rationale)
orders(id, signal_id, status, fill_price, filled_ts, client_request_id UNIQUE)
trades(id, order_id, symbol, side, quantity, entry_price, exit_price, pnl, pnl_pct, hold_days)
net_values(ts, cash, equity, total)
decisions(trade_id, symbol, action, ts, rationale, basis_memories, risk_note, expected)
outcomes(trade_id, pnl_pct, hold_days, vs_expected, lesson, lesson_type, verified)
evolutions(mutation_id, decision, reason, meta_json, ts)   # 进化审计（对标 evolution_audit.jsonl）
```

### 2.3 新文件 `laap/paper_trading/ledger.py`

| 函数 | 职责 | 验收点 |
|---|---|---|
| `class PaperLedger` | 最小 OMS，SQLite 持久化 | |
| `__init__(db: PaperDB)` | 注入 db | |
| `submit_signal(signal, client_request_id=None) -> PaperOrder` | 下单，`client_request_id` 幂等（参考 DSA T-13） | 重复 id 返回同单 |
| `fill_order(order_id, fill_price) -> PaperTrade` | 成交 | pending→filled |
| `cancel_order(order_id) -> PaperOrder` | 撤单 | 仅 pending |
| `close_trade(trade_id, exit_price) -> PaperTrade` | 平仓，算 pnl/hold_days | pnl 正确 |
| `net_values() -> List[PaperNetValue]` | 净值序列 | 供适应度/回测 |
| `stats() -> Dict` | 持仓/订单/成交统计 | |

### 2.4 新文件 `laap/paper_trading/market_source.py`（决策 #2 真实源）

| 符号 | 职责 | 验收点 |
|---|---|---|
| `class MarketSource` | 抽象基类 `get_price(symbol, ts=None) -> (float, meta)` | |
| `class LiveMarketSource(MarketSource)` | **真实源优先**：akshare 轮询 + Longbridge WS（可选），返回 `used_fallback=False` | 真实价可查 |
| `class StubMarketSource(MarketSource)` | 合成行情（`used_fallback=True`），真实源失败/无 token 时降级 | 净值连续 |
| `resolve_source(...)` | 工厂：试 Live → 降级 Stub，记录降级原因 | `used_fallback` 正确标记 |

> **真实源注意（来自记忆）**：Longbridge WS 无 token、沙箱联网受限；首版 Live 源在**用户环境**真实跑，测试用 Stub 隔离网络。降级路径必须显式 `logger.warning` + `used_fallback` 标记，不可静默。

### 2.5 新文件 `laap/paper_trading/settle.py`

| 函数 | 职责 | 验收点 |
|---|---|---|
| `class Settlement` | 日终结算 | |
| `daily_settle(date, ledger, market) -> PaperNetValue` | 持仓 MTM 结算（参考 DSA，注意 T+1 可用量） | 净值连续 |

**P0 测试**：`tests/test_paper_db.py`（建表/幂等）、`tests/test_paper_ledger.py`（下单/成交/平仓/幂等/pnl）、`tests/test_market_source.py`（Stub 连续 / Live 降级 used_fallback）、`tests/test_paper_settle.py`（MTM）。

---

## 3. 阶段 P1 — 记忆闭环（规划文档 A-1 / A-2）

> **目标**：决策留痕 → 结果回填 → 教训沉淀 → 参与推理。约 1.5 人日。存储用 SQLite（决策 #3）。

### 3.1 新文件 `laap/paper_trading/decision_record.py`（A-1）

| 函数 | 职责 | 验收点 |
|---|---|---|
| `record_decision(db, symbol, action, rationale, basis_memories, risk_note, expected) -> DecisionRecord` | 决策留痕 → SQLite `decisions` 表 | 含 rationale + basis_memories |
| `close_position(db, ledger, trade_id, exit_price, market) -> OutcomeRecord` | 平仓回填 → SQLite `outcomes` 表 | vs_expected 正确 |
| `_derive_lesson(outcome) -> (lesson, lesson_type)` | 规则化教训（`short_term_chase` / `no_stop_loss` / `early_exit` 等） | lesson_type 稳定可检索 |

### 3.2 新文件 `laap/paper_trading/memory_bridge.py`（A-1/A-2）

| 函数 | 职责 | 复用 AGI 接口 | 验收点 |
|---|---|---|---|
| `lesson_to_experience(outcome) -> str` | outcome+lesson → 经验文本 | — | 含 symbol/lesson_type/结果 |
| `encode_lesson(memory, outcome) -> str` | 沉淀 → `encode_experience(content, context_triggers=[symbol, lesson_type])`，返回 episode_id | `encode_experience` | 落库可查 |
| `retrieve_for_symbol(memory, symbol) -> List[Dict]` | 同标的检索 | `query` / `retrieve_context` | 命中相关经验 |
| `inject_memory_prompt(memory, symbol, action) -> str` | 注入决策 prompt | `generate_memory_prompt` | 含历史教训 |
| `verify_lessons(db, memory, lesson_type, min_confirm=2) -> Dict` | 教训校验：SQLite `outcomes` 中同 lesson_type 累计 ≥min_confirm 次真实平仓才 `verified=True` | `query` | 未验证不参与推理 |

### 3.3 修改文件 `laap/paper_trading/ledger.py`（A-2 入口）

| 函数 | 改动 | 验收点 |
|---|---|---|
| `submit_signal(...)` | 可选参数 `memory: UnifiedMemory`；下单前 `inject_memory_prompt`，`rationale` 引用 `basis_memories` | 决策含记忆依据 |

### 3.4 修改文件 `laap_brain/api.py`（A 侧路由，`/v1/quant/*`）

| 新增 | 职责 | 参考 |
|---|---|---|
| `handle_quant_decision_record` | `POST /v1/quant/decisions` — 决策留痕 | `handle_rsi_improve` 模式 |
| `handle_quant_lessons` | `GET /v1/quant/lessons?symbol=&lesson_type=&verified=` | `handle_evo_audit` 模式 |

**P1 测试**：`tests/test_decision_record.py`、`tests/test_memory_bridge.py`。

**P1 验收**（对齐规划 3.5）：N 笔平仓后 ≥N 条经验记忆；新决策命中同标的/同 lesson_type 并注入；`trade_id → DecisionRecord → OutcomeRecord → lesson` 可追溯；未验证教训 `verified=False` 不参与推理。

---

## 4. 阶段 P2 — 代码级自进化闭环（决策 #4）

> **目标**：让**代码级受限递归（M4）**指向交易业绩，样本外纪律 + 人工审批。约 1.5 人日。
> **关键变化**：不用参数级 RSI，直接接 `CodeEvolutionEngine` + `TrueRSIEngine`（受限递归），把"改进策略/信号代码"纳入进化目标，交易适应度作为部署门禁。

### 4.1 核心设计：交易适应度作为 deploy 前门禁

现状 `CodeEvolutionEngine._improve_single` 流程：`scan → patch → SafetyGuard → sandbox test → (qa) → deploy/awaiting_approval`。它没有"交易业绩"概念。

**接入方式**：复用 M4 已有的 `scope_guard` 钩子模式，新增对称的 **`deploy_gate` 钩子**（默认 None，M1-M4 行为不变）：

```
CodeEvolutionEngine._improve_single 在 test_passed 之后、deploy 之前：
  _gate = getattr(self, "deploy_gate", None)
  if _gate is not None:
      ok, reason = _gate(mutation, self)
      if not ok: → mutation REJECTED, 审计落库 "oos_blocked", 返回
```

`QuantEvolutionGate` 注入 `deploy_gate`，对目标在 `laap/paper_trading/` 下的变更强制走样本外回测门禁。

### 4.2 新文件 `laap/paper_trading/trade_fitness.py`

| 函数 | 职责 | 验收点 |
|---|---|---|
| `_cumulative_return(net_values) -> float` | 累计收益 | |
| `_sharpe_ratio(net_values, rf=0.0) -> float` | 夏普比率 | 合理区间 |
| `_max_drawdown(net_values) -> float` | 最大回撤 | [0,1] |
| `compute_trade_fitness(trades, net_values) -> Dict` | `score = 0.4*收益_norm + 0.35*夏普_norm + 0.25*(1-回撤)` | score∈[0,1] |

> **双门槛**：代码级变更只接受"软件健康度测试不降（现有 379 项）+ 交易适应度 OOS 不劣化"。

### 4.3 新文件 `laap/paper_trading/backtest_runner.py`

| 函数 | 职责 | 验收点 |
|---|---|---|
| `class BacktestRunner` | 样本外回测 runner，用 ledger 在历史段回放 | |
| `split_series(dates, train=0.6, valid=0.2, oos=0.2) -> (train, valid, oos)` | **时间切分**（非随机） | 三段无重叠、按时间序 |
| `run_backtest(ledger_factory, params, split) -> Dict` | 在指定段回放策略（patch 前/后代码分别跑） | 返回 trade_fitness 分量 |
| `oos_gate(train_metrics, oos_metrics) -> bool` | OOS 不劣化门禁（如 `oos.cumret>=0` 且 `oos.sharpe>=train.sharpe*0.8`） | fail-closed |

### 4.4 新文件 `laap/paper_trading/quant_evolution.py`（闭环 B 后半）

| 符号 | 职责 | 复用 M4 | 验收点 |
|---|---|---|---|
| `class QuantEvolutionGate` | 交易适应度 + OOS 门禁，实现 `deploy_gate` 协议 | | |
| `__call__(mutation, engine) -> (ok, reason)` | 目标在 paper_trading 下 → 跑 backtest_runner + oos_gate；不满足即拒绝 | `backtest_runner` / `oos_gate` | fail-closed |
| `class QuantEvolutionEngine` | 代码级受限递归编排 | | |
| `__init__(code_evo_engine, runner, audit)` | 注入 CodeEvolutionEngine + BacktestRunner + EvolutionAuditLog | | |
| `attach()` | 给 `code_evo_engine` 装 `scope_guard`（M4 受限）+ `deploy_gate`（交易门禁） | `TrueRSIEngine` | 双守卫就位 |
| `evolve(max_mutations=1) -> List[Dict]` | 扫 paper_trading 业务代码 → auto_improve（auto_deploy=False，走人工审批） | `CodeEvolutionEngine.auto_improve` | 产出 awaiting_approval 提案 |
| `approve_and_deploy(mutation_id, approver) -> Dict` | 人工批准 → 部署 → 审计 | `approve_and_deploy` / `audit.record` | 审计落库 |

> **作用域扩展**：M4 `TrueRSIEngine.ALLOWED_DIRS` 需扩展 `laap/paper_trading/`（业务代码），但 `PROTECTED_SAFETY`/`PROTECTED_CORE` 不变——安全基座（code_evolution 等）仍永久只读。

### 4.5 修改文件 `laap_brain/api.py`（B 侧路由 `/v1/quant/*`）

| 新增 | 职责 | 参考 |
|---|---|---|
| `handle_quant_evolve` | `POST /v1/quant/evolve` — 触发一轮受限进化（产提案） | `handle_evo_audit` |
| `handle_quant_evolve_approve` | `POST /v1/quant/evolve/approve` — 人工批准（`{"mutation_id","approver"}`） | `handle_evo_deploy` |
| `handle_quant_evolve_reject` | `POST /v1/quant/evolve/reject` — 拒绝并回滚 | `handle_evo_rollback` |
| `handle_quant_evolve_audit` | `GET /v1/quant/evolve/audit` — 查询进化审计 | `handle_evo_audit` |

**P2 测试**：`tests/test_trade_fitness.py`、`tests/test_backtest_runner.py`（时间切分/OOS fail-closed）、`tests/test_quant_evolution.py`（双守卫/evolve 提案/OOS 拒绝/批准部署/审计）。

**P2 验收**（对齐规划 4.4）：
1. 一次代码级受限进化完整走通：扫描 paper_trading 业务代码 → 补丁 → 沙箱测试 → **样本外回测 OOS 不劣化** → 人工批准 → 部署 → 审计；
2. 落地后可回滚（git），回滚后 379 项不回归；
3. `trade_fitness` + OOS 结果进审计（SQLite evolutions 表 + `state/evolution_audit.jsonl`）；
4. 未满足 OOS 纪律的变更自动拒绝（fail-closed）；安全基座永久只读。

---

## 5. 阶段 P3 — 收尾验收（约 0.5 人日）

| 项 | 内容 |
|---|---|
| 端到端 | StubMarketSource 模拟 5 交易日：留痕→成交→平仓→教训沉淀→下次决策注入→代码级进化提案→OOS 门禁→人工批准，全链路跑通 |
| 回归 | `pytest tests -q --ignore=tests/test_mcp_tools.py` 失败数不增（基线 **379 passed / 0 failed**，4 个 pre-existing 环境失败除外） |
| 文档 | 更新 `docs/memory-evolution-closed-loop-plan.md` 实施进度 + 本清单勾选 |
| 门禁 | `scripts/check_quant_closure.py`（可选）：断言 SQLite decisions/outcomes/evolutions 与 audit 一致、闭环无断点 |

---

## 6. 关键约束（勿重踩，来自记忆）

1. 沙箱跑测试必须 `TMPDIR=/tmp`（挂载盘 SQLite 9p `disk I/O error`）；SQLite 库文件测试时注入 tmp 路径，勿碰挂载盘。
2. 真实 DB 核验先 `cp` 到 `/tmp`。
3. 涉全局单例的测试需注入隔离（DatabaseManager/get_db 模式）。
4. 真实源（Longbridge/akshare）在沙箱联网受限，测试一律用 Stub；Live 源仅在用户环境真实验证。
5. 提交前 `git show --stat` 核对改动量、警惕 CRLF 行尾噪音；`.snapshots/` 含 `.env` 备份，`git add -A -- ':!.snapshots'` 排除。

---

## 7. 阶段总览 v2

| 阶段 | 内容 | 新文件 | 测试 | 预计 |
|---|---|---|---|---|
| P0 | 最小真实交易循环（SQLite + 真实源优先） | models/db/ledger/market_source/settle | 4 个测试文件 | 1.5d |
| P1 | 记忆闭环（决策→经验→参与推理） | decision_record/memory_bridge | 2 个测试文件 | 1.5d |
| P2 | 代码级自进化闭环（M4 受限递归 + OOS 门禁） | trade_fitness/backtest_runner/quant_evolution | 3 个测试文件 | 1.5d |
| P3 | 收尾验收 | check_quant_closure.py（可选） | 端到端 + 回归 | 0.5d |

**总计约 5 人日，11 个新文件 + 2 处 api.py 修改，零新外部依赖。**
