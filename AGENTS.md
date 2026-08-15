# AGENTS.md — LAAP-AGI

本文件用于约束 LAAP-AGI 仓库的默认开发流程与全局行为准则，目标是减少重复沟通、减少返工，并让改动与项目现状保持一致。

如果本文件与仓库中的脚本、工作流、代码现状不一致，以实际可执行内容为准，并在相关改动中顺手修正文档，避免规则漂移。

---

## 1. 项目定位与全局原则

- **定位**：LAAP-AGI = 认知引擎（AGI 递归自我改进）+ 量化 paper 交易闭环。核心差异化是**记忆 × 自进化**。
- **全局哲学**：
  - **fail-closed**：外部依赖（数据源/LLM/行情）失败时，宁可静默不下单，不可凭降级数据乱下。
  - **契约单源**：同一业务语义只允许一个定义点，禁止平行实现。
  - **最小改动**：默认稳定性优先；非当前任务直接需要的重构/抽象/迁移一律克制。
  - **本地量化文件不进 git**：`laap/paper_trading/` 按 `.gitignore` 排除（NAS 不同步约定），量化代码/数据为本地资产。

---

## 2. 硬规则

- 遵循目录边界：
  - 认知/进化逻辑：`laap/agi/`、`laap/evolution/`、`laap_brain/`、`aris_brain/`、`psi_core/`
  - 量化 paper 交易：`laap/paper_trading/`（**本地，不进 git**）
  - 脚本/工具：`scripts/`；测试：`tests/`；文档：`docs/`；数据：`data/`
- 未经明确确认，不执行 `git commit`、`git tag`、`git push`。commit message 用英文，不添加 `Co-Authored-By`。
- 不写死密钥、账号、路径、模型名、端口或环境差异逻辑；新增配置项必须同步 `.env.example` 与相关文档。
- **契约单源**（禁止平行常量）：
  - 交易成本 → `laap/paper_trading/costs.py::DEFAULT_COSTS`
  - 交易适应度 → `laap/paper_trading/trade_fitness.py::FITNESS_WEIGHTS`
  - 可调参数 → `laap/paper_trading/quant_config.py`（运行时可调，调用方用 `qc.X` 属性访问）
- 修改用户可见能力 / API / 部署 / 通知 / 报告结构时，同步更新 `docs/CHANGELOG.md` 与相关 `docs/*.md`。
- 注释、docstring、日志文案以清晰准确为准，不强制英文，与文件语境一致。

## 2.1 死循环防范规则

- 写文件成功后直接用文字回复用户，禁止用 bash echo 或工具调用来"验证"写文件。Write/Edit 失败会直接报错——无报错即成功。
- 禁止 `echo "OK"`/`echo "DONE"` 等无意义命令占位；禁止无实际问题时连续 3 次以上同模式工具调用；禁止用工具调用"填补等待"。
- 若发现自己连续 3 次执行同模式工具调用，立即停止，转文字回复。

## 2.2 记忆与经验复用

- 会话开始时读取 `memory/memory/MEMORY.md` 及其引用文件（用户画像/量化背景/项目愿景/防工具刷屏等），前次会话教训必须载入。
- 量化项目有失败先例，改动要避免重蹈"有骨架、没闭环""契约漂移"的覆辙。

---

## 3. 边界与失败模式（全局行为准则）

> 本节是项目全局的行为底线，覆盖量化闭环与认知引擎。任何新增模块/流程都应遵守同类 fail-closed 原则。

### 3.1 数据源 / 网络（akshare、行情、K线）

| 场景 | 行为 |
|---|---|
| akshare / 网络失败 | 返回空 + `used_fallback=True`，管线静默（不因数据缺失乱下单）；不抛异常中断主流程 |
| 瞬态连接失败（RemoteDisconnected） | 用重试（`_with_retry`，指数退避默认 3 次）后仍失败才走兜底 |
| 个股资料双源 | `stock_individual_info_em` 优先 → `stock_profile_cninfo` 兜底 → 都失败返回 None + fallback |
| 数据质量 | 结果必须带 `data_quality/source/used_fallback` 诚实标记（real / synthetic / fallback） |

### 3.2 LLM / 判定（news_verifier）

| 场景 | 行为 |
|---|---|
| LLM 不可用 / 解析失败 | keyword 启发式低置信（≤0.5）+ `used_fallback=True`，**默认不自动下单**（fail-closed） |
| 真利好 | `genuine_bullish` 且 confidence ≥ `NEWS_MIN_CONFIDENCE`(0.7) 才 dispatch |
| RSI 超买（>70） | 置信度 ×0.8（追高降档），reasons 追加「追高风险」 |
| RSI 超卖（<30）+ 真利好 | 置信度 +0.05（超卖+真利好=买点） |
| 涨停（limit_utils 多信号） | `trade_action="wait"`，不开新仓 |
| 跌停/停牌 | 买入/卖出拒单 |
| D1 成本控制 | 仅对未判定过（非 fallback）的新新闻调 LLM；`_was_judged` 去重，`force` 参数可强制重判 |

### 3.3 交易执行（风控 / 成本 / T+1 / 涨跌停）

| 场景 | 行为 |
|---|---|
| 下单前风控门（risk_gate） | R1 止损距离 / R2 单票仓位 / R3 总仓位 / R4 单日亏损熔断 / R5 连亏停开仓——一票否决，拒绝落 `risk_rejections` 表 |
| 含费现金不足 | 含费二分下打量（B4）降档；不足 100 股整手 → hold |
| 重复 scan | `client_request_id=decision_id` 幂等，不重复下单 |
| T+1 | `PaperLedger.enforce_t1` 保证当日买入不可当日卖 |
| 交易成本 | ledger `fee_model`（默认对齐 `costs.DEFAULT_COSTS`）；买入扣佣金+过户费+滑点上调，卖出扣佣金+印花税+过户费+滑点下调，pnl 为净额 |

### 3.4 调度 / 轮询

| 场景 | 行为 |
|---|---|
| 盘前/盘后/午休 | NewsSignalWorker 非 A 股盘中时段（9:30-11:30 / 13:00-15:00）不轮询不下单 |
| 非交易日 | 复用 `QuantDailyScheduler._is_trading_day` 跳过 |
| 数据陈旧 | Worker 启动校验 K 线/新闻新鲜度，陈旧 → 告警跳过本轮（不静默跑旧数据） |
| 开关 | 各调度器默认关，显式 env（`LAAP_QUANT_DAILY`/`LAAP_TRSI_ENABLED`/`LAAP_NEWS_INTRADAY`）值必须为 `1` 才启用 |

### 3.5 回测引擎（方法学边界）

| 场景 | 行为 |
|---|---|
| 成交时点 | 信号 bar i 收盘确认 → **bar i+1 开盘成交**（无 OHLCV 用次日收盘近似），禁止同 bar 收盘自引用 |
| 默认成本 | `run_backtest` 默认带 `DEFAULT_COSTS`；显式 `{}` 才零成本 |
| 显著性 | 路径级 z（每随机路径一个 OOS 累计收益），禁止把日收益合并成池（自相关自由度虚高） |
| 报告口径 | `summary.pass_count` 与 `regime_stats.pass` 必须同口径（MTC 开时 = ok AND mtc_pass），`raw_pass` 另存裸 ok |
| 诚实负结果 | 不宣称实证通过；285 只大样本 FAIL 结论如实报告 |

### 3.6 开发环境 / 沙箱

| 场景 | 行为 |
|---|---|
| 挂载盘 SQLite | 挂载盘（9p）SQLite 写会 `disk I/O error` → 测试/临时操作用 `TMPDIR=/tmp`；**数据库写入操作在用户环境（本地盘）执行** |
| 联网受限 | 沙箱连不上真实数据源（eastmoney）→ 测试用 stub 注入；真实验收在用户环境 |
| 测试隔离 | akshare / LLM 全注入 stub；DB 用 tmp 路径；量化测试 `enforce_t1=False` 时注意 T+1 拒绝语义 |

---

## 4. 仓库速览

- 服务入口：`python -m laap_brain.api`（OpenAI 兼容端点 http://localhost:11546/v1）
- 核心模块：
  - `laap/agi/`：认知引擎（rsi_engine 参数自调优、code_evolution 代码进化、unified_memory 记忆）
  - `laap/evolution/`：M4 True RSI 受限递归（`true_rsi.py`）
  - `laap/paper_trading/`：量化 paper 交易闭环（**本地，不进 git**）
  - `laap_brain/`：API / 工具 / 装配
  - `aris_brain/`：认知桥 / 情感 / 目标引擎
  - `psi_core/`：PSI 内核
- 数据：`data/paper_trading.db`（paper 账本）、`data/watchlist_kline/kline.db`（真实 K 线）
- 量化文档：`docs/news-intel-closed-loop-implementation-plan.md`（闭环计划）、`docs/phase2-multi-factor-strategy-plan.md`（多因子）、`docs/rsi-paper-evidence-verification.md`（论文证据）

---

## 5. 常用命令

```bash
# 服务
python -m laap_brain.api                      # 启动（端口 11546）
# 测试（沙箱挂载盘需 TMPDIR=/tmp）
TMPDIR=/tmp python -m pytest tests -q
TMPDIR=/tmp python -m pytest tests/test_news_*.py tests/test_quant_api.py -q
# 量化工具
python scripts/check_paper_performance.py     # M5 观察期进度
python scripts/cleanup_paper_seed.py --apply  # 清理演示 seed
python scripts/run_news_eval_real.py          # D3 真实 LLM 判定抽查
# API
curl.exe -X POST http://127.0.0.1:11546/v1/quant/news/scan -d '{"symbol":"600519","auto_order":false}'
```

---

## 6. 默认工作流

1. 判断任务类型：`fix / feat / refactor / docs / chore / test / review`。
2. 先读现有实现、配置、测试、脚本、文档，再动手。
3. 识别改动边界：认知引擎 / 量化 paper / API / 文档 / 工作流 / AI 协作资产。
4. 命中高风险区域（配置语义、API/Schema、数据源 fallback、风控、调度、报告结构）时，先确认契约单源与 fail-closed 语义。
5. 只做最小改动，不夹带无关重构。
6. 文档与代码不一致时，以实际可执行内容为准，顺手修正文档。
7. 改完按验证矩阵执行；量化改动必须覆盖对应 E2E（判定/下单/扣费/留痕链路）。

---

## 7. 验证矩阵

- **量化/paper 改动**（`laap/paper_trading/`）：`pytest tests/test_news_*.py tests/test_quant_api.py tests/test_ledger_fees.py -q`；涉及回测引擎时跑 `tests/test_backtest_runner.py test_walkforward.py test_significance.py`。
- **API 改动**：`tests/test_quant_api.py` + 对应 handler 逻辑；重启服务后人工 curl 验证。
- **认知引擎改动**：对应 `test_*_engine*` 测试；涉及进化治理跑 `tests/test_true_rsi*.py`、`tests/test_evo_deploy_governance.py`。
- **文档/治理**：不强制代码测试，但核对命令/配置/文件名与实际一致；改动 AI 协作资产时检查 AGENTS.md 与软链接 CLAUDE.md 一致。
- **网络/三方依赖**：先跑离线确定性检查；确认 timeout/retry/fallback 降级路径成立；未做在线验证须写明原因。

---

## 8. 交付与发布

- 默认交付结构：改了什么 / 为什么这么改 / 验证情况 / 未验证项 / 风险点 / 回滚方式。
- `docs` 任务可写 `Docs only, tests not run`，但仍说明核对结果。
- 自动 tag 默认不触发（commit title 含 `#patch`/`#minor`/`#major` 才触发）。
- 用户可见变更优先 PR 合入并补齐 label 与验证说明。
- 量化本地文件（`laap/paper_trading/`）**不提交 git**；如需入库需显式决策并说明。
