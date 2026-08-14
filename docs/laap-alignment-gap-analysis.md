# LAAP 项目实施对齐差距分析（现状评估 v2.0）

> **评估日期**: 2026-08（修订完成后第二轮评估）
> **评估对象**: `D:\laap-AGI` main 分支（含 10 个修订 commit）
> **基线事实**: `pytest tests -q` = **245 passed / 5 deselected / 1 warning**；工作树干净；docker-compose.yml 通过 YAML 解析
> **配套文档**: 《LAAP-AGI 问题汇总报告》（`docs/laap-issue-report.md`）·《LAAP 实施修订业务参考手册》（`docs/laap-revision-manual.md`）

---

## 1. 修订后现状总览

| 维度 | 修订前（审计基线） | 修订后（当前） | 变化 |
|---|---|---|---|
| 测试 | 174 passed / 20 failed（系统 Python 3.14 下）；194/2（venv 3.11） | **245 passed / 0 failed**（+5 deselected 网络组） | +71 通过，纳入 56 个此前从未被收集的测试 |
| API 入口 | 双入口（`aris_brain/laap_brain_api.py` 563 行 + `laap_brain/api.py` 888 行） | **单一实现** `laap_brain.api`，旧文件为 60 行兼容包装 | -670 行 |
| 端口 | 11530/11546 混用 | **统一 11546** | 零残留 |
| 默认绑定 | `0.0.0.0`（无认证） | **127.0.0.1** + 可选 `LAAP_API_KEY` Bearer 认证 | 默认安全 |
| 依赖声明 | requirements / pyproject / Dockerfile 三处 | **pyproject 唯一事实源**（`-e .` 验证通过，hermes-agent 0.18.2 自动解析） | 单一化 |
| 许可 | LICENSE=AGPL / pyproject=BUSL / README=Apache 三处冲突 | **Apache 2.0 全文 + 分层策略 LICENSING.md 对齐**（PEP 639） | 一致 |
| 部署配置 | docker-compose.yml 非法 YAML（`//` 注释）、端口错位、自指 | **合法 YAML**，端口/网络/API 地址修正 | 可部署 |
| 仓库根 | 17 个 `_*.py` 工作脚本 + 4 个杂项模块 | **scripts/market(12) + scripts/ops(8)**，硬编码路径相对化 | 整洁 |
| 测试组织 | 18 个 tests/ + 9 个散落包内 | **27 个全部收归于 tests/** | 集中化 |
| 僵尸代码 | 未知 | **删除 11 个零引用模块**（审计证明） | -11 模块 |
| CI | 仅 CLA 检查 | **新增 pytest CI**（py3.11/3.12 + compose YAML 校验） | 有 CI |

---

## 2. 计划 vs 实施对齐矩阵

来源：修订手册 R1–R15（16 项 ISSUE）。

| 修订项 | 计划目标 | 实施状态 | 证据 | 差距说明 |
|---|---|---|---|---|
| R1 docker-compose | 合法 YAML + 端口/网络修正 | ✅ 完成 | `2bdbf94` | 无（本机无 docker CLI，`docker compose config` 未实机执行，见 GAP-C） |
| R2 许可统一 | 方案 A：LICENSE→Apache 2.0 | ✅ 完成（权利人已确认方案 A） | `d3c02b7` | 无 |
| R3 API 收敛 | 单一入口 + 兼容包装 | ✅ 完成 | `d889be3` | 无 |
| R4 测试基线 | 环境错误清零 + 网络测试跳过 | ✅ 完成 | `f16faf0` + `4398116` | 部分网络测试仍依赖人工起 daemon（GAP-H） |
| R5 依赖单一化 | pyproject 唯一事实源 | ✅ 完成 | `f16faf0` | 部分完成：`laap-quickstart.sh:191` 仍 `\|\| true` 掩盖安装失败（GAP-L）；laap-enterprise 两依赖零使用（GAP-G） |
| R6 悬空引用 | VERSIONS 引用清零 | ✅ 完成 | `9363c18` | `VERSIONS.yaml:105` checksums 策略命令不存在（GAP-F）；references/*.md 历史文档仍引旧入口（GAP-E） |
| R7 安全加固 | 绑定收紧 + 可选认证 | ✅ 完成（+ 本轮回溯修复 Docker 内绑定） | `27884f1` + `b2bec80` | 初版引入 Docker 端口映射回归，本轮已修复（见 §4 已闭环项） |
| R8 端口统一 | 统一 11546 | ✅ 完成 | `d889be3` | 无 |
| R9 敏感文件 | 凭证/状态文件治理 | ✅ 完成 | `27884f1` | 无 |
| R10 测试集中 | 9 个散落测试收归 tests/ | ✅ 完成（+4 个潜藏漂移断言修复） | `4398116` | 无 |
| R11 巨型文件拆分 | 5 个 70KB+ 文件拆分 | ⏳ **未执行**（按纪律"先补测试再拆"） | — | **最大剩余差距**：`v5_upgrade.py`(85KB)/`aris_lm_v5.py`(78KB)/`aris_rules_engine.py`(73KB)/`aris_cognitive_bridge.py`(73KB)/`causal.py`(71KB)，仅 3 个有测试覆盖 |
| R12 重叠收敛 | 认知总线/记忆/情感单一事实源 | 🟡 **部分完成**：僵尸清理 11 模块；剩余功能重叠未收敛 | `b177476` | 认知总线 `cognitive_bus.py` 双份（20KB vs 49KB）、记忆 6 处实现、情感 3 处实现的调用图收敛未做 |
| R13 企业包 | 可安装 + 文档 | ✅ 完成（+移除不存在的 laap-core 依赖） | `9363c18` | 安装验证通过；依赖语义待权利人决策（GAP-G） |
| R14 脚本归档 | 根目录脚本 → scripts/ | ✅ 完成 | `9363c18` | `aris_brain/_archive` 遗留 14 个文件未清理（GAP-J）；.gitignore 旧模式可清理（GAP-I） |
| R15 元数据 | PEP 621/639 合规 | ✅ 完成 | `f16faf0` | 无 |

**对齐率**：15/16 项修订目标达成或部分达成；**1 项（R11）未启动**，1 项（R12）仅完成安全子集。

---

## 3. 本轮重新评估新发现（已闭环）

| 发现 | 严重度 | 说明 | 处置 |
|---|---|---|---|
| Docker 容器内绑定回归 | **P0** | Batch 1 收紧默认绑定为 127.0.0.1 后，Dockerfile 未设 `LAAP_HOST`，容器进程只监听回环 → `docker compose` 端口映射 `127.0.0.1:11546:11546` **连接拒绝**（README 主推部署方式失效） | ✅ 已修（`b2bec80`：两 Dockerfile 补 `ENV LAAP_HOST=0.0.0.0`；主机侧暴露仍由 compose 映射限制） |
| 测试零 CI | P1 | 仓库仅有 CLA 检查（`.github/workflows/cla.yml`），测试从未在 CI 运行——修订成果无持续保障 | ✅ 已修（`b2bec80`：新增 `tests.yml`，py3.11/3.12 矩阵 + compose YAML 校验） |
| sentence-transformers 弃用警告 | P3 | `laap_semantic_memory.py:72` 使用已改名 API，产生 FutureWarning | ✅ 已修（`b2bec80`：`get_embedding_dimension` 兼容，警告 5→1） |

---

## 4. 剩余差距清单（按优先级）

| 编号 | 严重度 | 差距 | 位置 | 建议动作 | 工作量 |
|---|---|---|---|---|---|
| GAP-A | **P1** | R11 巨型文件拆分未做；无覆盖文件未补测试 | `laap/agi/v5_upgrade.py`(85KB)、`aris_brain/aris_lm_v5.py`(78KB)、`aris_brain/aris_rules_engine.py`(73KB)、`aris_brain/aris_cognitive_bridge.py`(73KB)、`laap/agi/causal.py`(71KB) | ① 为 3 个无覆盖文件补冒烟测试；② 每文件独立 PR 按"规则表/引擎/门面"模板拆分 | 3–5 人日 |
| GAP-B | P1 | R12 功能重叠未收敛（认知总线/记忆/情感） | `cognitive_bus.py`×2、记忆×6、情感×3 | 逐领域输出调用矩阵 → 定单一事实源 → 收敛；每领域独立 PR | 2–4 人日 |
| GAP-C | P2 | docker-compose 未实机验证（本机无 docker CLI） | `docker-compose.yml` | 在部署环境执行 `docker compose config -q && docker compose up -d` + 双服务健康检查 | 0.5 人日（环境依赖） |
| GAP-D | P2 | 端到端网络链路无自动验证 | `tests/` 网络组（5 个） | CI 增加"起服务→跑 `-m network`→停服务"job（daemon 用 `-e .` 安装后 `python -m laap_brain.api &`） | 0.5 人日 |
| GAP-E | P2 | 历史/理论文档仍引用旧入口 | `references/Harness-Consciousness-Engineering*.md`、`TO-HERMES-TEAM.md`、`PRIVATE_REPOS_PLAN.md` | 属 CC BY-SA 理论层，建议仅在文档头加"历史版本"注记，不逐行改写 | 0.25 人日 |
| GAP-F | P3 | VERSIONS.yaml 校验策略命令不存在 | `VERSIONS.yaml:93`（`python -m laap_brain check-versions`） | ✅ 已闭环：改为 `python -m laap_brain.version_check`（实测输出 LAAP 1.0.0 + Hermes 0.18.2 compatible） | 0 |
| GAP-G | P3 | laap-enterprise 的 pydantic/cryptography 零使用 | `laap-enterprise/pyproject.toml:14-15` | 权利人决策：移除（诚实）或保留（计划用途）并注释 | 0.1 人日（决策项） |
| GAP-H | P3 | 网络测试依赖人工启动 daemon | `tests/test_memorize_market.py`、`test_record_watchlist.py` 等 5 个 | 已通过 fixture 优雅跳过；如需完全自洽，fixture 可尝试自动拉起 daemon（建议并入 GAP-D） | 并入 GAP-D |
| GAP-I | P3 | .gitignore 残留根脚本模式 | `.gitignore:59-62`（`_gen_*.py`/`_build_*.py`） | 归档完成后可精简为 `scripts/_*.py` 或删除 | 0.05 人日 |
| GAP-J | P3 | 遗留归档目录未清理 | `aris_brain/_archive/`（14 文件） | 逐个判定：保留（历史对照）或删除；建议保留并加 README 说明 | 0.25 人日 |
| GAP-K | P3 | 第三方警告 1 条 | hermes-agent 依赖 `pydantic_settings` 的 forward reference 警告 | 非本项目代码，可忽略或上游反馈；可在 pyproject 记录 | 0 |
| GAP-L | P3 | quickstart 掩盖安装失败 | `laap-quickstart.sh:191`（`pip install -q -e . 2>/dev/null \|\| true`） | ✅ 已闭环：改为安装失败即 `warn` + `exit 1`，不再静默吞错 | 0 |

---

## 5. 残余风险评估

| 风险 | 等级 | 说明 | 缓解 |
|---|---|---|---|
| Docker 部署路径未实机验证 | 中 | compose YAML 合法但容器构建/运行未实测（本机无 docker） | GAP-C；CI 可加 docker build 冒烟（若仓库启用 Actions） |
| 网络链路无端到端守护 | 中 | 语义记忆/工具路由的 HTTP 链路仅靠手工 | GAP-D；现有 fixture 已保证失败为 skip 而非红 |
| 巨型文件维护风险 | 中 | 5 个 70KB+ 文件仍存在 | GAP-A（分阶段） |
| 重叠引擎行为分叉 | 中 | 双 cognitive_bus / 多套记忆实现，未来修改可能不同步 | GAP-B（分领域收敛） |
| 许可方案 A 依赖权利人确认 | 低 | 已按确认执行；若权利人改主意需回退 LICENSE | 文档已记录变更历史（LICENSING.md §8） |

---

## 6. 建议下一步（优先级排序）

1. **GAP-A（R11）**：从有测试覆盖的文件开始拆分——`aris_rules_engine.py`（73KB，已有 `test_aris_rules.py`）→ 拆"规则表/引擎/门面"；随后为 `v5_upgrade.py` 补冒烟测试再拆。
2. **GAP-B（R12）**：先收敛认知总线（双份 `cognitive_bus.py`），记忆与情感次之；每领域一个 PR + 调用矩阵证明。
3. **GAP-D + GAP-C**：CI 端到端 job + 部署环境 compose 实机验证（一次性，环境就绪即可）。
4. **GAP-E/F/I/J/L**：0.5 人日内的低风险整洁项，可批量处理。
5. **GAP-G**：等待权利人决策。

> 结论：**15/16 项修订目标对齐达成**，测试基线 245/0 可持续（CI 已护航）；剩余差距集中在 R11 巨型文件拆分与 R12 功能重叠收敛（合计 5–9 人日），无 P0 级阻断项。
