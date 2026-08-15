# LAAP paper_trading 接入 Aris — 使用指南

> 让 Aris（数字生命体）成为交易业务的**一等参与者**：覆盖学习 / 识别 / 采集 / 使用 / 决策五能力。
> 本文档为实际操作手册，配套实施方案见 `docs/paper_trading_aris_integration_plan.md`（Phase 1-3 已完成）。
> 版本：2026-08-16

---

## 1. 前提

```bash
# 1. 启动服务（daemon 已在运行时跳过）
python -m laap_brain.api        # 默认 127.0.0.1:11546

# 2. 关键开关（当前 fail-closed 安全默认）
PAPER_TRADING_AUTO_EXECUTE=0    # 0=只建议不下单；1=允许执行(仍需确认词)
```

- 执行边界三层纵深防御：
  1. `PAPER_TRADING_AUTO_EXECUTE=0` → 默认拒绝自动下单
  2. 二次确认词硬门槛 → 用户须说"确认执行/确认平仓"等
  3. `TradingSelf.judge` 审核 → 非 approve 一律拒绝（风控 R1-R5 一票否决）

---

## 2. 方式一：直接问 Aris（推荐，零门槛）

规则引擎已内置 **18 条 pt_* 交易规则**，用自然语言对话即可。

### 2.1 学习/查询（只读，无副作用）

| 你想知道 | 怎么说 | 命中规则 |
|---|---|---|
| 盈亏情况 | "我们最近交易怎么样" / "赚了还是亏" | `pt_net_value_rule` |
| 交易教训 | "有什么交易教训" / "学到什么" | `pt_lessons_rule` |
| 当前持仓 | "当前持仓如何" / "我的持仓" | `pt_portfolio_rule` |
| 最近信号 | "最近有什么信号" | `pt_signals_rule` |
| 风控记录 | "被风控拦过吗" / "风控拒绝记录" | `pt_risk_events_rule` |
| 绩效报告 | "绩效报告" / "收益报告" | `pt_performance` |
| 系统健康 | "系统健康" | `pt_health` |
| 账户 | "查看账户" / "账户列表" | `pt_account_list` |

### 2.2 决策（只给建议，不下单——当前开关=0）

| 你想做 | 怎么说 | 行为 |
|---|---|---|
| 问要不要买 | "帮我看下600519要不要买" | `pt_decide` → TradingSelf.judge 审核 → 给建议 |
| 问要不要卖 | "五粮液值得卖吗" | 同上 |

### 2.3 执行（需二次确认词）

| 你想做 | 怎么说 |
|---|---|
| 确认买入 | "确认执行 买入 600519 100股"（确认词硬门槛） |
| 确认平仓 | "确认平仓 600519" |

> ⚠️ 即使 `PAPER_TRADING_AUTO_EXECUTE=1`，没有"确认执行/确认平仓"等词也会被拒。

### 2.4 管理（复盘/治理）

| 你想做 | 怎么说 |
|---|---|
| 每日简报 | "今日交易简报" / "今天交易怎么样" |
| 进化提案 | "看下进化提案" / "策略改进" |

---

## 3. 方式二：直接调 API（自动化场景）

daemon 的 `/v1/chat/completions` 是 OpenAI 兼容入口，可把上述对话通过 API 发：

```bash
curl.exe -X POST http://127.0.0.1:11546/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"laap-core\",\"messages\":[{\"role\":\"user\",\"content\":\"今日交易简报\"}]}"
```

已有的量化端点（20+）也可直接用：

```bash
# 决策留痕 / 教训 / 净值 / 信号 / 风控
curl.exe http://127.0.0.1:11546/v1/quant/decisions
curl.exe http://127.0.0.1:11546/v1/quant/lessons
curl.exe http://127.0.0.1:11546/v1/quant/net_values
curl.exe http://127.0.0.1:11546/v1/quant/signals
curl.exe "http://127.0.0.1:11546/v1/quant/risk/rejections?symbol=600519"
curl.exe "http://127.0.0.1:11546/v1/quant/kline?symbol=600519&days=120"

# 进化治理
curl.exe http://127.0.0.1:11546/v1/quant/evolve/audit
```

---

## 4. 方式三：Python 直接调用（开发/测试）

### 4.1 走规则引擎（与对话一致）

```python
import sys; sys.path.insert(0, r"D:\laap-AGI")
from aris_brain.aris_rules_engine import get_engine

e = get_engine()
print(e.process("今日交易简报")["output"])                 # 简报
print(e.process("帮我看下600519要不要买")["output"])       # 决策建议
print(e.process("确认执行 买入 600519 100股")["output"])   # 执行（需确认词）
```

### 4.2 直接走 quant_bridge（跳过规则层）

```python
from laap.paper_trading.quant_bridge import get_bridge
b = get_bridge()

# 审核建议（永不下单）
b.use_decide("600519", "buy", 100)

# 执行（二次确认 + judge 审核）
b.use_execute("600519", "buy", 100, confirm_word="确认执行")

# 平仓
b.use_close("600519", 100, confirm_word="确认平仓")

# 事件注入（平仓/风控/结算 → 认知总线 → PSI 情绪）
b.sense_event("quant_trade_closed", payload={"symbol": "600519"}, pnl=-120.5)
```

---

## 5. 自动化（cron）

**工作日 15:30 自动执行**（已注册 Windows 计划任务 `LAAP_Memorize_Trading_Daily`）：
拉取当日交易状态 → 写语义记忆【交易日报 YYYY-MM-DD】→ 次日你问 Aris 它会记得（"昨天亏了，今天谨慎些"）。

手动触发一次：

```bash
python "%USERPROFILE%\AppData\Local\hermes\scripts\memorize_trading_daily.py"
```

---

## 6. 数据在哪

| 数据 | 位置 | 说明 |
|---|---|---|
| paper 账本 | `data/paper_trading.db` | 11 表：signals/orders/trades/net_values/decisions/outcomes/evolutions/news_items/news_verdicts/risk_rejections/news_summaries |
| Aris 语义记忆 | `aris_brain/laap_semantic_memory.json` | 教训双写 + 交易日报 |
| 进化治理审计 | `state/evolution_audit.jsonl` | 进化提案/决策/部署/回滚 |
| 回测/验证数据 | `data/watchlist_kline/kline.db` | 真实 K 线 |

---

## 7. 关键边界提醒

1. **当前是 paper 模拟账户**（非真实资金），账本在 `data/paper_trading.db`
2. **`PAPER_TRADING_AUTO_EXECUTE=0` 下 Aris 永不下单**——要授权改 `.env` 为 `1` 后重启 daemon（但仍需确认词）
3. **策略无泛化 alpha**（OOS 通过率 10-20%）——Aris 会如实汇报亏损，它是"受控模拟环境的功能验证"，不承诺收益
4. `laap/paper_trading/` 为**本地资产不进 git**（NAS 不同步约定）；入库的是规则层（`aris_brain/`）与测试（`tests/`）
5. 沙箱联网受限时数据源 fallback 到 stub（`used_fallback=True` 诚实标记）；真实行情验证需在用户环境执行

---

## 8. 快速验证清单

```bash
# 1. daemon 健康
curl.exe http://127.0.0.1:11546/health

# 2. 规则触发（应命中 pt_brief_rule）
python -c "import sys; sys.path.insert(0, r'D:\laap-AGI'); from aris_brain.aris_rules_engine import get_engine; print(get_engine().process('今日交易简报')['rule'])"

# 3. 零悬空契约（应输出 True）
python -c "import sys; sys.path.insert(0, r'D:\laap-AGI'); from aris_brain.rules_defs import DEFAULT_RULES, ToolRegistry; from aris_brain.rules_tools import register_default_tools; reg=ToolRegistry(); register_default_tools(reg); print(not [s.tool for r in DEFAULT_RULES for s in r.steps if s.tool not in reg.list()])"

# 4. 全量回归（基线 800 passed）
python -m pytest tests -q
```

---

*本文档配套：实施方案 `docs/paper_trading_aris_integration_plan.md`、差距分析 `docs/laap-alignment-gap-analysis.md`。*
