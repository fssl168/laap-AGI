# paper_trading × Aris 使用手册

> ARIS 认知体系 × paper_trading 全流程接入 — 使用指南
> 版本: v0.1(方案已确认, P1 感知接入实施中)
> 日期: 2026-08-16

---

## 1. 当前能力矩阵

| 能力 | 状态 | 触发方式 | 说明 |
|---|---|---|---|
| **交易状态查询** | 🚧 P1 | 问 Aris"最近交易怎么样" | 净值/持仓/盈亏 |
| **交易教训召回** | 🚧 P1 | 问 Aris"有什么交易教训" | 语义记忆召回,含标的/类型 |
| **交易信号感知** | 🚧 P1 | 认知总线自动注入 | 盈利→valence+ / 亏损→valence- |
| **交易建议** | 📋 规划 P2 | 问 Aris"XX要不要买" | **只建议,不自动下单**(用户已确认) |
| **下单执行** | 📋 规划 P2 | 用户明确确认后 | 需 TradingSelf 审核 + 二次确认 |
| **每日简报** | 📋 规划 P3 | 每日 15:30 cron | 净值/盈亏/教训/明日关注 |
| **进化治理** | 📋 规划 P3 | 问 Aris"进化提案" | 列提案 → 批准/拒绝 |

> 🚧 = 实施中 / 📋 = 已规划 / ✅ = 可用

---

## 2. 快速上手(对话示例)

### 2.1 查询交易状态
```
用户: 最近交易怎么样?
Aris: 当前账户净值 ¥1,024,531(累计 +2.45%)。
      持仓: 贵州茅台 200股(浮盈+3.1%)、五粮液 300股(浮亏-2.4%)...
```

### 2.2 查询交易教训
```
用户: 交易上学到什么教训了吗?
Aris: 记得 3 条教训:
      • [止损]600519 跌破止损位 1445.94 离场,教训是趋势破位要果断
      • [止盈]300750 止盈触发 398.19 卖出,预期管理准确
      • [风控]隆基绿能弱势空头+亏损财报,止损执行正确
```

### 2.3 交易建议(P2 后)
```
用户: 帮我看看五粮液要不要加仓?
Aris: 建议:观望(不买入)。
      依据: MACD 空头、浮亏-48.9%,记忆中有 2 条同标的负面教训。
      风险提示: 当前处于弱势,止损位 67.77。
      注: 我只给建议,不下单。如需执行请明确说"确认买入"。
```

### 2.4 每日简报(P3 后)
```
用户: 今日交易简报
Aris: 📊 2026-08-16 交易简报
      净值: ¥1,024,531(+0.32%)
      今日交易: 无
      持仓: 3 只(茅台/五粮液/平安)
      教训: 无新增
      明日关注: 五粮液止损挂单 75.12 待确认
```

---

## 3. 设计约束(用户确认 2026-08-16)

| 约束 | 值 |
|---|---|
| 执行边界 | **ARIS 只建议**,`paper_trading_auto_execute=false` |
| 数据范围 | 3 标的:600519 / 000001 / 000858 |
| 认知注入 | 交易事件**只影响情绪**(valence/arousal),**不动能力判断**(competence/certainty) |
| 简报频率 | 每日 15:30 cron |

---

## 4. 技术接入点

### 4.1 新增 Aris 工具(只读, P1)
`aris_brain/rules_tools.py` → `register_default_tools()` 末尾:

| 工具名 | 功能 | 数据源 |
|---|---|---|
| `quant_status` | 净值/持仓/盈亏总览 | `/v1/quant/net_values` + trades |
| `quant_lessons` | 教训列表 | `/v1/quant/lessons` |
| `quant_portfolio` | 持仓明细 | DB trades 未平仓 |
| `quant_signals` | 最近信号 | `/v1/quant/signals` |

### 4.2 认知总线事件(P1)
`cognitive_bus.py` 增加交易事件源,5 类事件:
`QUANT_SIGNAL / QUANT_TRADE_CLOSED / QUANT_RISK_TRIGGERED / QUANT_DAILY_SETTLE / QUANT_EVOLUTION_PROPOSED`
→ 只改 valence/arousal,不改 competence/certainty。

### 4.3 教训双写(P1)
`memory_bridge.py::encode_lesson` 增加第二写:
UnifiedMemory(已有) + `laap_semantic_memory.json`(标签 `【交易教训】`+标的+类型)
→ Aris 的 `recall_fact_rule` 可召回交易经验。

### 4.4 动作工具(P2, 只建议)
`tool_quant_decide` / `tool_quant_execute` / `tool_quant_close`:
- 全部走 `TradingSelf.judge()` 审核
- `auto_execute=false`: 只输出建议,不触碰 DB 写路径
- 用户明确确认词("确认买入/确认卖出")后才执行
- 审计写 `risk_rejections` 表

### 4.5 每日简报(P3)
- 规则:`quant_daily_brief_rule`(触发"今日交易简报/今天交易怎么样")
- cron: 15:30 no_agent 脚本 → 拉取 quant 状态 → 写入语义记忆
  `【交易日报 YYYY-MM-DD】...` → Aris 次日自动感知

### 4.6 Hermes 接入 LAAP（custom provider 配置）

Hermes 通过 LAAP-AGI 本体接入点连接（模型 `laap-core`），触发规则引擎全部 pt_* 技能：

**配置位置**：`%LOCALAPPDATA%\hermes\config.yaml`

**custom_providers 条目**（`add_laap_provider.py` 可自动写入）：
```yaml
custom_providers:
  - name: laap-agi
    base_url: http://localhost:11546/v1
    api_key: laap-brain
    api_mode: chat_completions
    models:
      laap-core: { name: laap-core, context_length: 120000 }
      laap-qre:  { name: laap-qre }
      laap-rules:{ name: laap-rules }
    model: laap-core
```

**顶部 model 段**（关键：provider 必须带 `custom:` 前缀，否则 base_url 解析为空导致连接失败）：
```yaml
model:
  default: laap-core
  provider: custom:laap-agi     # 必须有 custom: 前缀
  base_url: http://localhost:11546/v1
  aliases:
    laap: custom:laap-agi/laap-core
```

> ⚠️ **故障记录（2026-08-16）**：原配置 `provider: laap-agi`（无 `custom:` 前缀）
> 导致 Hermes 走 `PROVIDER_REGISTRY` 找不到条目 → base_url 解析为空 → 连接失败。
> 修复为 `custom:laap-agi` 后，端到端验证通过（可触发 pt_brief/pt_decide/pt_lessons 等技能）。

**验证**：Hermes 内问"今日交易简报"，应返回 `engine=rules:pt_brief_rule` 的真实简报。

### 4.7 微信消息频道接入 LAAP

微信频道（`platforms.weixin`，腾讯 iLink Bot API）跟随 `model.default` 走 LAAP 本体，使微信里的对话触发全部 pt_* 技能。

**配置**（`%LOCALAPPDATA%\hermes\config.yaml`）：
```yaml
model:
  default: laap-core              # 微信频道模型 = model.default
  provider: custom:laap-agi       # 必须有 custom: 前缀
  base_url: http://localhost:11546/v1
platforms:
  weixin:
    enabled: true
    token: <WEIXIN_TOKEN>
    allow_from: <你的微信ID@im.wechat>
```

**生效步骤**：
```bash
hermes gateway restart    # 使新配置生效
hermes gateway list       # 确认 gateway 运行
# 日志应显示: [Weixin] Connected account=... base=https://ilinkai.weixin.qq.com
```

**验证**（2026-08-16 实测）：微信消息"交易怎么样"→ `engine=rules:pt_net_value_rule` 返回真实净值；"当前持仓如何"→ 返回真实持仓（如 `600114: 100股 @ 30.02`）；"今日交易简报"→ 结构化简报。

**附带能力**：网关同时注册了 `laap_brain` MCP server（12 个工具：laap_cognitive_state / laap_recall_memory / laap_bootstrap / laap_reflect / laap_express / laap_rsi_* 等），Hermes 可调用。

> ⚠️ **故障记录（2026-08-16）**：model 段曾被外部改为 `provider: deepseek` + `base_url: http://localhost:11546/v1`（provider 名与 URL 不匹配）→ 微信消息无法触发 LAAP 技能。修复为 `provider: custom:laap-agi` + `default: laap-core` 后，微信链路验证通过。

---

## 5. 安全边界

- ✅ 只读工具无副作用(状态/教训/持仓/信号)
- ✅ 动作工具默认不执行,需 judge 通过 + 用户二次确认
- ✅ 全部操作审计留痕(risk_rejections / evolutions 表)
- ✅ 真实 OOS 负 alpha 如实汇报,不包装

---

## 6. 故障排查

| 症状 | 排查 |
|---|---|
| Aris 回答"交易"相关走 fallback | 规则未注册 → 重启 LAAP(新进程加载规则) |
| 教训召回不到 | 检查 `【交易教训】` 是否在语义记忆(recall 验证) |
| 情绪无变化 | 认知总线事件源未启动 → 查 `start_background()` |
| 简报未生成 | cron 副本路径/权限 → 查 cron list + 手动跑脚本 |
| HTTP 400 (laap-core) | config.yaml `model.default` 应为 deepseek-v4-flash |

---

## 7. 里程碑

- [ ] P1 感知接入(只读工具 + 认知事件 + 教训双写)
- [ ] P2 使用接入(建议工具 + judge 审核)
- [ ] P3 管理闭环(简报 + 进化治理 + 15:30 cron)

每阶段完成 → 验收 → 用户确认 → 进入下一阶段。

---

*ARIS × paper_trading 使用手册 · 2026-08-16 · v0.1*
