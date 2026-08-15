# 实证证据标准复核报告（item 1-5）

> 复核人：项目经理 · 日期：2026-08-15
> 方式：逐项对照用户清单，用工作区实际文件/数据/测试结果验证，不沿用此前叙述。

---

## 总览

| item | 要求 | 状态 | 证据 |
|---|---|---|---|
| 1 | 全 A 股或 ≥200 标的 × ≥500 天，OOS ≥60 天，Bonferroni/FDR | ✅ **已完成**（补拉后 208+ 只） | `real_data/universe/` + `walkforward_universe_full_*.json` |
| 2 | 佣金+印花税+滑点+T+1 后再算超额 | ✅ **已完成**（T+1 显式守卫已补） | 引擎 `costs`/`entry_bar` + 测试 |
| 3 | 牛/熊/震荡分段分别报告 | ✅ **已完成** | 全宇宙报告 `regime_stats` |
| 4 | `LAAP_QUANT_DAILY=1` 持续 ≥1 个月，真实 paper 交易序列 | ❌ **未完成（需真实环境 + 时间）** | 运行手册已备、调度器已接线；1 个月运行待用户执行 |
| 5 | 诚实负结果作为工程贡献 | ✅ **已完成** | `docs/paper-honest-negative-framing.md` |

---

## item 1：扩大样本 + 多重检验 —— ✅ 已完成（复核中补拉到 ≥200）

**证据（复核时实测，非转述）**：
- `real_data/universe/_meta.json`：目标 200 → 首轮 **193 只 ≥500 天**（3 只 <500：001280/600930/001391，均不足 500 交易日）。
- **复核发现缺口**：193 < 200，不满足"≥200 只"。已用 `--offset 200 --n 100` 补拉沪深300剩余成分 → **最终 285 只 ≥500 天**（3 只短样本除外），远超 ≥200 要求。
- OOS 窗口：`config.test = 80` ≥ 60 天 ✓（全宇宙报告均 80 天）。
- 多重检验：`config.mtc = "bonferroni"` ✓；实现 `_two_sided_p`（erfc，无 scipy）+ `_mtc_pass_flags`（bonferroni / fdr BH q=0.05）✓；测试 `test_mtc_bonferroni_stricter` / `test_mtc_fdr_flags` ✓。
- **最终全宇宙结果（285 只 × 500+ 天，成本 + Bonferroni）**：
  - long_only：**1/285 过门禁（0.4%）**，中位 z=-0.21，bull excess **-31.05%**、range -2.45%、bear **+11.16%**
  - long_short：**0/285（0%）**，中位 z=-0.29，bull excess **-35.05%**、range -3.46%、bear **+13.23%**
  - 判定：**FAIL**（双族合计 1/570 通过，在 Bonferroni 假阳性预期之内；唯一一致模式 = 熊市段少亏/做空超额）

## item 2：交易成本 + T+1 —— ✅ 已完成

**证据**：
- 引擎 `_run_multi_factor` 新增 `costs={commission:0.00025, stamp:0.0005, slippage:0.001}`，4 处现金变动点套用（开多/平多/开空/回补），成本后算 `excess`（`walkforward` 的 excess = 成本后 OOS 收益 − 买入持有）✓。
- T+1：复核时发现此前仅注释声称"日线粒度天然满足"，**无显式守卫** → 已补 `entry_bar` 守卫（开仓 bar 当日不可平）+ 无回归测试 `test_t1_guard_no_regression` ✓。
- 真实执行层 T+1：`ledger.enforce_t1` 已有 4 项显式测试（`test_paper_enhancements.py`：当日拒平/绕过/次日可平/锁定持仓）✓。
- 测试：`test_costs_reduce_returns` / `test_costs_zero_equals_none` ✓。

## item 3：跨周期分段 —— ✅ 已完成

**证据**：`walkforward` 按 OOS 窗买入持有 ±5% 分 bull/range/bear，逐段报告通过率/mean_oos/excess；全宇宙报告 `regime_stats` 完整（如 long_only：bull n=94 excess -33.63%、range n=40 -2.82%、bear n=59 +12.19%）✓；测试 `test_regime_class` ✓。

## item 4：真实执行留痕 ≥1 个月 —— ❌ 未完成（唯一未完成项）

**诚实状态**：
- ✅ 已备：`docs/paper-observation-runbook.md`（LAAP_QUANT_DAILY=1 启动/收集/导出/验收清单）；调度器已接 `api.py:1516`（`_start_quant_daily_scheduler`，`LAAP_QUANT_DAILY=1` 时启动）；单次 pipeline 已在真实 kline.db 上验证跑通（gate_blocked 正确回退、data_quality 诚实标记）。
- ❌ 未做：**持续运行 ≥1 个月并产生真实 paper 交易序列**——需要用户真实环境常驻服务 + 真实时间，会话内无法完成。这是论文"论点 C（真实成交业绩）"的唯一缺口。

## item 5：诚实负结果作为贡献 —— ✅ 已完成

**证据**：`docs/paper-honest-negative-framing.md`（工程/系统贡献定位、措辞红线、可引摘要句、审稿应答）。

---

## 复核中新增/修复

| 项 | 修复 |
|---|---|
| item 1 缺口 | `fetch_universe.py` 加 `--offset`；补拉沪深300剩余成分至 ≥200 只 |
| item 2 T+1 | 引擎补显式 `entry_bar` 守卫 + 无回归测试 |
| item 4 | 状态如实标注"未完成"，附运行手册与验收清单 |

## 剩余待办（需用户侧）

1. item 4（唯一未完成）：真实环境启用 `LAAP_QUANT_DAILY=1` 运行 ≥1 个月，按 runbook 收集真实 paper 成交业绩（论点 C）。
2. ~~补拉后重跑全宇宙 walk-forward~~ ✅ 已完成：285 只 × 500+ 天 × 双族 × 成本 × Bonferroni → long_only 1/285、long_short 0/285，结论不变（FAIL，无稳定 alpha）。
