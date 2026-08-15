# 复核报告：新闻情报×LLM+RSI×研报×自动下单 闭环实施计划

> 复核日期：2026-08-15
> 复核对象：`docs/news-intel-closed-loop-implementation-plan.md`
> 复核方式：对照当前仓库实际状态逐项核验（接口存在性 / akshare 数据源 / 测试基线 / 成本口径），并评估方法学与运营风险。
> 结论：**计划质量高、与现状吻合、可批准实施**。原 1 个必须先修项（交易成本口径冲突）已通过抽单源 `costs.py` 解决；其余为需注意的运营/方法学项，已随用户 4 项决策固化进计划。

---

## 一、总体判断

这是一份高完成度的实施计划：背景/目标/成功标准/架构/文件级拆分/测试/验收/假设/工作量全齐，且**引用的现有接口经我逐项验证全部真实存在**。数据源（akshare）4 个接口经源码验证全部存在。设计遵循项目一贯的 fail-closed + 向后兼容哲学（FeeModel 默认 None、RiskGate 只在新闻管线挂载、db 用 `CREATE TABLE IF NOT EXISTS`）。**建议批准实施**，但先处理下述"必须先修"项。

---

## 二、核验通过的项（实测证据）

| 计划引用 | 实测 |
|---|---|
| `PaperClosedLoop.decide_and_trade` | ✓ `paper_service.py:41` |
| `ledger.submit_signal`（client_request_id 幂等） | ✓ `ledger.py:43` |
| `QuantDailyScheduler._is_trading_day` | ✓ `daily_pipeline.py` |
| `llm_refine.parse_params`（鲁棒 JSON 解析） | ✓ `llm_refine.py:38` |
| `HermesIntegration.llm_call` / `LAAPLLMIntegration.llm_call` | ✓ `hermes_integration.py` / `llm_integration.py` |
| akshare `stock_news_em / stock_individual_info_em / stock_profile_cninfo / stock_research_report_em` | ✓ 全部存在（.venv akshare 0.5.33） |
| `market_source` 注入风格、`STRATEGY_PARAMS.atr_stop_mult` 等 | ✓ |
| ledger 目前无成本/fee 概念 | ✓ 确认（grep 无 cost/fee/stamp/commission）——计划新增 FeeModel 是正确的 |

测试基线：当前 pytest 收集 **632 个测试**（5 deselected，1 个 test_mcp_tools 环境错误）。

---

## 三、必须先修（🔴 高）

### 1. 交易成本口径冲突 — ✅ 已解决（2026-08-15）
原冲突：计划 B2 用金策成本（印花税 0.1%），backtest_runner.DEFAULT_COSTS 用现行 A 股（印花税 0.05%）。**已抽单源** `laap/paper_trading/costs.py`：`DEFAULT_COSTS = {commission:0.00025, stamp:0.0005, slippage:0.001}`，backtest_runner 改从 costs 导入（`backtest_runner.DEFAULT_COSTS` 仍可访问，向后兼容），计划 B2/§4.3/§6 费率全部对齐该单源（金策 0.1% 标注为旧口径不采用，min_commission/transfer_fee 默认 0）。148 项量化测试通过无回归。

### 2. LLM 调用成本/限流未估算（运营风险）

盘中每 30 分钟 × watchlist（数百只）× 新闻拉取 + LLM 判定 → LLM 调用量巨大，DEEPSEEK 费用可能不可控。计划无成本估算、无限流/降频策略。

**建议**：① 先 `fetch_stock_news` 过滤，**仅当该标的有新新闻时才调 LLM**（无新闻不判定）；② 轮询频次可配（`LAAP_NEWS_INTERVAL` 已计划，建议默认调大或按标的优先级）；③ 285 只全量盘中扫描成本高，建议先对自选股子集启用。

---

## 四、需澄清/修正（🟡 中）

### 3. 测试基线数字过时
计划多处引用"442 passed"（§4.3/§4.8/§7）。当前实际基线 **632 collected**（含预存环境失败）。需更新，避免实施时误以为"442 即全量"。

### 4. DSA / 金策智算参考路径 — 已授权且核验通过
原复核时路径不可访问；用户已授权（2026-08-15），现**已连接并逐项核验**：计划 §2 引用的 DSA 8 文件 + 7 关键函数、金策智算 8 文件 + 10 关键函数**全部真实存在**（`MenxiaSheng`/`HuBuRevenue`/`bing_bu_war._is_limit_up`/`_is_market_session_time`/`SLTPCalculator`/`AISignalWorker` 等）。计划参考点准确。**注意**：金策 `constants.py` 确认 `STAMP_DUTY=0.001`（印花税 0.1%）——与 backtest_runner `DEFAULT_COSTS`（0.05%）的冲突是真实的，见 §三.1。

### 5. LLM 真假判定缺评估机制（方法学缺口）
核心是"LLM 识别真利好/假消息"，但计划无 ground truth、无准确率评估。对论文可信度和系统可信度都是缺口。**建议**：实施时留一个 20-50 条新闻的人工抽查集（人工标注 vs LLM 判定），至少报告一次一致性；或在计划中显式记录"判定未经人工评估"作为局限。

---

## 五、低优先级 / 澄清（🟢）

6. **工作量**：9 新文件 + 2 修改 + 8 测试文件 + 5 API 路由，5 人日偏紧但复用度高、可接受。建议 P0 先落地数据层（news_intel/fees/limit_utils/quant_config），在用户环境跑通 S1 验收，再投入 LLM 判定层（P1）。
7. **"卖出最佳时间"语义**：A5 已诚实落地为"卖出价位计划（供日终 sell 分支参照）"，非盘中自动卖。合理折中，但需在需求/论文里明确，避免"自动卖出"期望落差。
8. **akshare 列结构**：`stock_research_report_em` 的具体列名（A6 已标注"plan 阶段实测"）。建议 P0 先跑一次真实接口确认列名再定解析。
9. **`_is_market_session_time` / NewsSignalWorker / news_verifier / research_strategy** 均为计划要新建的（金策借鉴/本仓库新建），当前"缺失"是预期的，非计划错误。

---

## 六、建议的批准方式

**有条件批准**，实施顺序：

1. **先修**：成本口径统一（方案 A 或 B），并同步更新计划 §2.2 B2 / §6 的费率表。
2. **P0**：`quant_config` + `fees` + `limit_utils` + `news_intel`，用户环境跑 S1 验收（`fetch_stock_news/profile/research_reports` 对 600519 返回真实数据），确认 akshare 列结构。
3. **P1-P2**：`news_verifier`（LLM+RSI）→ `research_strategy` + `risk_gate`。
4. **P3-P4**：`news_pipeline`（自动下单闭环 + Worker）→ db schema + API 路由 + 回归。
5. 全程 LLM 判定先 `auto_order=False` 手动观察，确认判定质量后再开自动。

---

## 附：本次核验证据
- 接口存在性：grep 验证（见 §2）
- akshare 源码：`.venv/Lib/site-packages/akshare/`（v0.5.33）
- 成本口径：`backtest_runner.py:32-35 DEFAULT_COSTS`
- 测试基线：`pytest tests --collect-only`（632 collected）
- 参考项目路径：当前挂载不可访问（`D:\leanpython\daily_stock_analysis` / `D:\projects\jin-ce-zhi-suan`）
