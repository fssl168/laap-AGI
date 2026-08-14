# LAAP 项目实施对齐差距分析（第二轮重新评估 v4.0）

> **评估日期**: 2026-08（第二轮全面重新评估 — 第三轮实施后、外部同步后）
> **评估对象**: `D:\laap-AGI` main 分支（HEAD `f74d85e`，含外部 NAS 同步变更）
> **基线事实**: `pytest tests -q` = **277 passed / 5 deselected / 1 warning**；工作树干净；docker-compose.yml 通过 YAML 解析；`python -m laap_brain.version_check` = LAAP 1.0.0 + Hermes 0.18.2 compatible
> **配套文档**: 《问题汇总报告》·《修订手册》·《差距分析 v3.0》（上一轮）

---

## 1. 项目现状总览（第二轮实测）

| 维度 | v3.0 基线 | 当前实测 | 变化 |
|---|---|---|---|
| 测试 | 277 passed / 0 failed | **277 passed / 0 failed**（回归修复后） | 曾因外部脚本移动触发 4 errors+1 failed，本轮修复（`f74d85e`） |
| API 入口 | `laap_brain.api` 单一 | ✅ 保持（`laap_brain_api.py` 53 行兼容包装） | 无 |
| 端口 | 11546 统一 | ✅ 保持（api.py L880 / compose 41 / Dockerfile / .env 一致） | 无 |
| 代码规模 | — | **213 个 .py / 59,159 行**（排除 venv/archive/build） | 基线建立 |
| 巨型文件 | 已拆 6 个 | **仍有 7 个 800+ 行核心模块**（最大 `laap_integrator.py` 1293 行） | 新发现（见 GAP-N） |
| 脚本目录 | ops=8 / market=12 | **ops=7 / market=15**（外部把 4 个论文类脚本移入 market） | 外部变更已同步 |
| CI | 双 job（单元+E2E） | ✅ 保持 | 无 |
| 测试文件 | 27 | **30 个**（+3 冒烟套件） | 无 |
| VERSIONS | version_check 策略 | ✅ 实测 compatible | 无 |

---

## 2. 计划 vs 实施对齐矩阵（R1–R15 复核）

| 修订项 | 计划目标 | 复核状态 | 备注 |
|---|---|---|---|
| R1 docker-compose | 合法 YAML + 端口修正 | ✅ 保持 | `yaml.safe_load` 通过，services: aris/laap-mcp |
| R2 许可统一 | LICENSE→Apache 2.0 | ✅ 保持 | 4 个许可文件齐全（PEP 639） |
| R3 API 收敛 | 单一入口 | ✅ 保持 | — |
| R4 测试基线 | 环境错误清零 | ✅ 保持（本轮修复 1 处外部回归） | — |
| R5 依赖单一化 | pyproject 唯一事实源 | ✅ 保持 | hermes-agent 0.18.2 |
| R6 悬空引用 | VERSIONS 清零 | ✅ 保持 | version_check 实测通过 |
| R7 安全加固 | 绑定收紧+认证 | ✅ 保持 | — |
| R8 端口统一 | 11546 | ✅ 保持 | 零残留 11530 |
| R9 敏感文件 | 凭证治理 | ✅ 保持 | gitignore 覆盖 4 类状态文件 |
| R10 测试集中 | 全部收归 tests/ | ✅ 保持 | 30 个文件 |
| R11 巨型文件拆分 | 6 个 70KB+ 拆分 | ✅ 完成（上轮） | 本轮确认无僵尸子模块 |
| R12 重叠收敛 | 三领域审计 | ✅ 完成（上轮） | CausalLink 缺陷已修 |
| R13 企业包 | 可安装 | ✅ 保持 | 零第三方依赖（GAP-G 已闭环） |
| R14 脚本归档 | 根目录整洁 | 🟡 **部分回退风险** | 外部同步移动脚本后测试路径曾回归（已修）；见 GAP-M |
| R15 元数据 | PEP 621/639 | ✅ 保持 | — |

**对齐率**：16/16 项保持达成；本轮发现 1 个**外部同步引入的回归**（已修复）与 1 个**新覆盖缺口**（GAP-N）。

---

## 3. 本轮新发现（第二轮评估）

| 发现 | 严重度 | 说明 | 处置 |
|---|---|---|---|
| 外部脚本移动导致测试回归 | **P0** | NAS 同步将 `_read_and_memorize.py` 从 `scripts/ops/` 移入 `scripts/market/`，`tests/test_read_and_memorize.py` 仍硬编码旧路径 → **4 errors + 1 failed**（277→272） | ✅ 已修（`f74d85e`：测试路径同步，回归 277/0 恢复） |
| 测试覆盖缺口 | **P1** | **7 个 800+ 行核心模块零测试覆盖**（合计 8,376 行）：`laap_integrator.py`(1293)、`analogical.py`(1289)、`code_evolution.py`(986)、`aris_goal_engine.py`(901)、`state_snapshot.py`(935)、`cognitive_bus.py`(1126)、`aris_emotion_engine.py`(892) | 待办（GAP-N） |
| 修订手册 R14 文档过时 | P3 | 手册 L504 仍描述论文类脚本归 `scripts/ops/`，与外部移动后现状不符 | ✅ 已修（补充现状注记） |

---

## 4. 剩余差距清单（按优先级）

| 编号 | 严重度 | 差距 | 位置 | 建议动作 | 工作量 |
|---|---|---|---|---|---|
| GAP-C | P2 | docker-compose 未实机验证 | `docker-compose.yml` | 部署环境 `docker compose config -q && up -d` + 健康检查 | 0.5 人日（环境依赖） |
| **GAP-N** | **P1** | **7 个核心模块零测试覆盖** | `laap_integrator.py`(1293)、`analogical.py`(1289)、`code_evolution.py`(986)、`aris_goal_engine.py`(901)、`state_snapshot.py`(935)、`cognitive_bus.py`(1126)、`aris_emotion_engine.py`(892) | 按 R11 纪律"先补测试再拆"：为每个模块补冒烟测试（实例化/核心方法/降级路径），覆盖后再评估是否拆分 | 2–4 人日 |
| GAP-H | P3 | 网络测试依赖人工 daemon | tests 网络组 | 已有 fixture 优雅跳过；可选并入 CI E2E（已建） | 0 |
| GAP-K | P3 | 第三方警告 1 条 | hermes-agent pydantic_settings | 非本项目代码，可忽略/上游反馈 | 0 |
| GAP-M | P3 | 外部同步与本地测试/文档的耦合风险 | `scripts/` 目录 + `tests/` | 外部 NAS 同步会移动脚本并可能再次引入测试路径回归；建议：① 测试路径改为运行时探测（脚本存在性检查）而非硬编码；② 将 `scripts/` 归类决策文档化（已部分完成） | 0.25 人日 |

---

## 5. 残余风险评估（第二轮）

| 风险 | 等级 | 说明 | 缓解 |
|---|---|---|---|
| 外部同步破坏测试 | **中** | NAS 同步已 2 次影响仓库（移动脚本、删除文件）；无守护机制 | GAP-M：测试路径运行时探测；CI E2E job 已能捕获网络层回归 |
| 核心模块无测试盲区 | **中** | 7 个 800+ 行模块（认知总线/情感/规划/类比/状态快照）无任何回归保障，未来修改可能静默破坏 | GAP-N（补冒烟测试） |
| Docker 部署未实测 | 中 | 本机无 docker CLI | GAP-C（部署环境验证） |
| 巨型文件维护 | 中 | 7 个 800+ 行文件仍在（虽已 <70KB 阈值） | GAP-N 测试覆盖后再评估拆分 |
| 历史文档引用漂移 | 低 | 理论文档已加注记；R14 手册已补现状 | 维护纪律 |

---

## 6. 建议下一步（优先级排序）

1. **GAP-N（P1）**：为 7 个无覆盖核心模块补冒烟测试——优先级：`cognitive_bus.py`（1126 行，被 agi_subscriber/psi_core_bridge/core 引用）→ `aris_emotion_engine.py`（892 行，6 处调用）→ `state_snapshot.py` → `aris_goal_engine.py` → `rsi_engine.py` → `code_evolution.py` → `laap_integrator.py`/`analogical.py`。
2. **GAP-M（P3）**：测试脚本路径改为运行时探测，抵御外部同步回归。
3. **GAP-C**：部署环境 docker 实机验证（环境就绪即可）。
4. **GAP-H/K**：维持现状（非本项目可控项）。

---

## 7. 结论

> **16/16 项修订目标保持对齐**；测试基线 **277/0** 稳定（CI 双 job 护航）；
> 本轮发现并修复 1 个外部同步引入的 P0 回归（测试路径），
> 新识别 1 个 P1 差距（GAP-N：7 个核心模块 8,376 行零测试覆盖）；
> 无 P0 级阻断项，剩余工作以补测试为主（2–4 人日），外部同步耦合风险建议以 GAP-M 机制化化解。
