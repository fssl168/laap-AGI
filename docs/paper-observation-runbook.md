# 真实执行留痕运行手册（item 4：LAAP_QUANT_DAILY=1，≥1 个月）

> 目标：产生**真实 paper 交易序列**（非回测），用真实成交业绩作为论文论点 C 证据。
> 前提：用户真实环境（有 akshare 或有 kline.db 数据源）、服务可常驻运行。

## 1. 启动

```bash
# 环境变量（服务启动前设置）
set LAAP_QUANT_DAILY=1
set LAAP_QUANT_DAILY_INTERVAL=86400   # 每日一次（秒）；测试可设 60 观察 tick
# 启动服务（main() 内自动 _start_quant_daily_scheduler）
python -m laap_brain.api
```

调度器接线已验证：`api.py:1516` → `_start_quant_daily_scheduler()`（`LAAP_QUANT_DAILY=1` 时启动 `QuantDailyScheduler`，daemon 线程，每 `INTERVAL` 秒 tick 一次 `QuantDailyPipeline.run`）。

## 2. 每个 tick 自动执行（并留痕）

1. **evolve_params**：确定性随机搜索（seed=42）→ 最优参数 + OOS 门禁
2. **apply_params_to_code**：M4 治理（SafetyGuard → 沙箱 → deploy_gate → 自我审核 `judge_proposal`）→ 默认 `awaiting_approval`（**不自动写 strategy.py**）
3. **run_daily_cycle**：真实 kline.db → 多因子信号 → TradingSelf `judge` → 成交（`paper_trading.db`）→ 净值快照
4. 全部落盘：`data/paper_trading.db`（signals/orders/trades/decisions/outcomes/net_values/evolutions）

## 3. 一个月后收集的证据（论点 C）

| 证据 | 来源 | 用途 |
|---|---|---|
| 真实净值序列 | `net_values` 表（日快照） | paper 权益曲线（月度） |
| 真实成交序列 | `trades`/`orders` 表（含 entry/exit/pnl/pnl_pct/hold_days） | 成交业绩、换手率、成本核算 |
| 决策留痕 | `decisions` 表（rationale 含 `[memory]`/`[self]`/`[benefit]`） | 决策可追溯性证据 |
| 教训沉淀 | `outcomes` 表 + UnifiedMemory | 学习闭环证据 |
| 数据质量 | `daily_cycle.data_quality`（real/qfq vs synthetic） | 诚实标注真实数据占比 |
| 自我审核统计 | `trading_self_observation_log.json` 持续追加 | TradingSelf 审核/弃权/反事实统计 |

**导出**：`python laap/paper_trading/export_real_data.py --base http://127.0.0.1:11546 --out real_data/month1/`

## 4. 合规与安全边界

- **全程 paper，无真实资金**；`fill_order`/`close_trade` 只改 SQLite 账本。
- **不自动部署代码**：apply 默认待人工审批，strategy.py 不被自动修改。
- **成本口径**：论文中成交业绩需叠加 A 股成本（佣金 0.025%+印花税 0.05%+滑点 0.1%）再报告超额。
- **故障处置**：tick 抛异常仅记日志不中断服务（`QuantDaily._loop` try/except）；缺数据标的自动降级合成并打 `used_fallback` 标记。

## 5. 验收标准（一个月）

- [ ] 观察日志 ≥20 条（交易日 ≥20）
- [ ] paper 成交 ≥10 笔且 decisions/outcomes 留痕完整
- [ ] 净值序列连续无断点
- [ ] 报告：净收益、夏普、最大回撤、换手、成本后超额 vs 买入持有；与回测对比（诚实说明差距）
