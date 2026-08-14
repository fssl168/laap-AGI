# LAAP-AGI 问题汇总报告

> **报告版本**: v1.0
> **审计日期**: 2026-08（本会话）
> **审计范围**: 仓库根目录 `D:\laap-AGI`（main 分支，本地工作树）
> **审计方法**: 静态侦察（目录/文件清单、关键模块阅读、grep 交叉验证、git 历史）+ 动态验证（docker-compose YAML 解析、pytest 全量基线）
> **配套文档**: 修复实施细节见《LAAP 实施修订业务参考手册》（`docs/laap-revision-manual.md`），本报告只做问题定义与优先级裁定。

---

## 0. 修订执行状态（2026-08 更新）

本报告发布后，修订手册 R1–R8/R10/R13–R15 已按批次执行完毕，R12 完成了僵尸模块清理子集。状态总览：

| 编号 | 严重度 | 标题 | 状态 |
|---|---|---|---|
| ISSUE-001 | P0 | docker-compose.yml 无效 YAML | ✅ 已修复（R1，commit `2bdbf94`） |
| ISSUE-002 | P0 | 许可状态冲突 | ✅ 已修复（R2，方案 A，commit `d3c02b7`） |
| ISSUE-003 | P0 | 双 API 入口重复 | ✅ 已修复（R3，commit `d889be3`） |
| ISSUE-004 | P1 | 测试基线不绿 | ✅ 已修复（R4，commit `f16faf0`；245 passed / 0 failed） |
| ISSUE-005 | P1 | 依赖三处分叉 | ✅ 已修复（R5，commit `f16faf0`） |
| ISSUE-006 | P1 | 悬空模块引用 | ✅ 已修复（R6，commit `9363c18`） |
| ISSUE-007 | P1 | API 默认 0.0.0.0 无认证 | ✅ 已修复（R7，commit `27884f1`；默认 127.0.0.1 + 可选 Bearer） |
| ISSUE-008 | P1 | 端口约定矛盾 | ✅ 已修复（R8，统一 11546，commit `d889be3`） |
| ISSUE-009 | P1 | 敏感/运行时文件 | ✅ 已修复（R9，commit `27884f1`） |
| ISSUE-010 | P2 | 测试分散 | ✅ 已修复（R10，commit `4398116`） |
| ISSUE-011 | P2 | 巨型单文件 | ⏳ 待办（R11，需按手册"先补测试再拆"纪律） |
| ISSUE-012 | P2 | 功能重叠模块 | 🟡 部分完成（R12 僵尸清理 commit `b177476`；认知总线/记忆/情感收敛待办） |
| ISSUE-013 | P2 | laap-enterprise 打包缺口 | ✅ 已修复（R13，commit `9363c18`） |
| ISSUE-014 | P2 | 根目录脚本混杂 | ✅ 已修复（R14，commit `9363c18`） |
| ISSUE-015 | P2 | pytest 配置缺失 | ✅ 已修复（R4，commit `f16faf0`） |
| ISSUE-016 | P3 | 包元数据不完整 | ✅ 已修复（R15，commit `f16faf0`） |

测试基线演进：194（初始基线, 2 网络失败）→ 189（默认排除网络）→ **245 passed / 0 failed**（R10 纳入 56 个此前未被收集的测试）。

---

## 0. 严重度定义

| 级别 | 定义 | 处理时限 |
|---|---|---|
| **P0 阻断** | 部署/分发/合规会直接失败，或存在两份并行实现导致行为分叉 | 立即 |
| **P1 高** | 影响正确性、安全性、可维护性或 CI 健康 | 本周 |
| **P2 中** | 技术债、工程整洁度、扩展性风险 | 本月 |
| **P3 低** | 建议项、体验优化 | 规划期 |

---

## 1. 问题总览表

| 编号 | 严重度 | 类别 | 标题 | 关键位置 |
|---|---|---|---|---|
| ISSUE-001 | P0 | 部署 | `docker-compose.yml` 为无效 YAML + 端口映射错误 + 服务自指 | `docker-compose.yml:41,93,96,112,121` |
| ISSUE-002 | P0 | 合规 | 许可状态四处冲突（AGPL / BUSL-1.1 / Apache 2.0 / 分层策略） | `LICENSE`、`pyproject.toml:12`、`README.md:23,58`、`LICENSING.md` |
| ISSUE-003 | P0 | 架构 | 双 API 入口重复运行，Docker 部署的是功能子集版 | `aris_brain/laap_brain_api.py` vs `laap_brain/api.py` |
| ISSUE-004 | P1 | 质量 | 测试基线不绿：20 failed / 174 passed | `tests/`（根因：缺 pytest-asyncio、matplotlib；网络测试依赖活服务） |
| ISSUE-005 | P1 | 工程 | 依赖声明三处分叉（requirements / pyproject / Dockerfile） | `requirements.txt`、`pyproject.toml:13-22`、`Dockerfile:30` |
| ISSUE-006 | P1 | 文档 | 悬空模块引用（版本矩阵指向不存在的模块） | `VERSIONS.yaml:80-102` |
| ISSUE-007 | P1 | 安全 | API 默认绑定 `0.0.0.0` 且无认证 | `laap_brain/api.py:869`、`aris_brain/laap_brain_api.py:18-19` |
| ISSUE-008 | P1 | 一致性 | 端口约定矛盾（11530 vs 11546） | `laap_brain/api.py:861`、`Dockerfile:57`、`README.md:318-320`、`.env.example:31` |
| ISSUE-009 | P1 | 安全 | 敏感/运行时文件未纳入版本控制策略 | `_wx_login_out.txt`、`aris_chat_history.json`、`aris_mode.json`（git 未跟踪） |
| ISSUE-010 | P2 | 工程 | 测试分散在包内 9 处，默认 pytest 不收集 | `aris_brain/test_*.py`、`laap/agi/test_*.py`、`hermes-integration/test_mcp_tools.py` |
| ISSUE-011 | P2 | 工程 | 巨型单文件模块（6 个 70KB+，最大 85KB） | `laap/agi/v5_upgrade.py`、`aris_brain/aris_lm_v5.py` 等 |
| ISSUE-012 | P2 | 架构 | 功能重叠模块（认知总线/记忆/情感/进化多处实现） | `cognitive_bus.py` ×2、记忆 ×6、情感 ×3 |
| ISSUE-013 | P2 | 打包 | `laap-enterprise` 未被根 pyproject 收录，安装方式不明 | `pyproject.toml:33-39`、`laap-enterprise/` |
| ISSUE-014 | P2 | 整洁 | 根目录 17 个 `_*.py` 工作脚本混杂 | 仓库根 `_*.py` |
| ISSUE-015 | P2 | 质量 | pytest 配置缺失（`network` mark 未注册、asyncio 插件未装） | `pyproject.toml:44-46`、`tests/test_memorize_market.py:72` |
| ISSUE-016 | P3 | 工程 | 包元数据不完整（无 urls / classifiers / 作者邮箱） | `pyproject.toml` |

---

## 2. 问题明细

### ISSUE-001 【P0 阻断】docker-compose.yml 为无效 YAML

**证据**：`docker-compose.yml` 中多处混用 `//` 作注释（YAML 只认 `#`），已实测 `yaml.safe_load()` 解析抛错：

```yaml
41:      - "127.0.0.1:11546:11546"   //这样处理是适应避免VPS的端口被利用
82:      # 运行真正的 MCP 服务器（SSE 模式），不是 HTTP API
93:      - "127.0.0.1:11546:11546"      //这样处理是适应避免VPS的端口被利用
112:      - hermes-net // 这里要跟随hermes agent的网络，需要自定义
121:  hermes-net:  // 这里要跟随hermes agent的网络，需要自定义
```

**连带错误**：
1. `docker-compose.yml:93`：`laap-mcp` 服务端口映射为 `11546:11546`，但该服务实际运行在 `11547`（`docker-compose.yml:90` 的 `--port 11547`，且健康检查 `docker-compose.yml:102` 探测 `localhost:11547/sse`）——端口映射错位。
2. `docker-compose.yml:96`：`LAAP_API_BASE=http://laap-mcp:11547` 把 MCP 服务的 API 地址指向**它自己**；MCP 客户端应调用的是 Brain API（`http://aris:11546`）。
3. `docker-compose.yml:121`：`networks.hermes-net.external: true` 要求外部网络必须预先存在，否则 `docker compose up` 直接失败。

**影响**：`docker compose up -d`（README 主推的部署方式）**必然失败**；即使修掉注释，端口错位也会导致 MCP 服务健康检查失败。

**修复指向**：见手册 R1。

---

### ISSUE-002 【P0 阻断】许可状态四处冲突

**证据**（交叉验证）：

| 来源 | 声明 | 位置 |
|---|---|---|
| `LICENSE`（根目录） | **AGPL-3.0** 全文 | `LICENSE:1-5`（"GNU AFFERO GENERAL PUBLIC LICENSE Version 3"） |
| `pyproject.toml` | `license = "BUSL-1.1"` | `pyproject.toml:12` |
| `README.md` | 徽章 **Apache 2.0**；铭牌 "Apache 2.0 (Community)" | `README.md:23,58` |
| `LICENSING.md` v1.1 | 分层策略：L2 核心引擎 BSL 1.1→2030 转 Apache；L3 psi_core **Apache 2.0**；L1 理论 CC BY-SA 4.0；L4/L5 商业 | `LICENSING.md:13-36,104-116` |
| `LICENSE.BSL` | BSL 1.1 文本 | 存在 |
| `COMMERCIAL_LICENSE.md` v1.0 | 商业授权 | 存在 |

git 历史显示许可已变更 4 次（`3b03d77` 分层策略 → `63bb996` AGPL → `255f111/5eee2d8/3b15a6e` AGPL 标准文本），当前**文件系统状态与任何单一声明都不完全一致**：`LICENSING.md` 声称 `LICENSE` 对应 Apache 2.0（层级 3），但实际 `LICENSE` 是 AGPL-3.0。

**影响**：对外分发/开源许可声明自相矛盾，法律风险高；pip 元数据（BUSL-1.1）与仓库许可文件（AGPL）冲突，平台审核（如 PyPI）可能拒绝或引发投诉。

**修复指向**：见手册 R2（需权利人拍板唯一事实源）。

---

### ISSUE-003 【P0 阻断】双 API 入口重复运行

**证据**：

| 维度 | `aris_brain/laap_brain_api.py` | `laap_brain/api.py` |
|---|---|---|
| 行数 | 563 | 888 |
| 入口身份 | Dockerfile CMD（`Dockerfile:65`）、README 快速开始（`README.md:379`） | pyproject console script `laap-brain`（`pyproject.toml:29`） |
| 默认端口 | 11530（docstring `:14`） | 11530（`main()` L861） |
| `process_with_laap` | `L105` | `L172` |
| 工具调用路由（`laap.agi.tool_router`） | **无** | 有（`L394-470`） |
| RSI 端点（`/v1/rsi_*`） | **无** | 有（`L750-805`） |
| 自动记忆沉淀（Step 1.5） | 无 | 有（`L222-291`） |
| LLM 链尾兜底 / SSE 流式 | 部分 | 有 |

两文件同名函数列表几乎重合：`_get_psi_adapter`、`process_with_laap`、`handle_chat_completions`、`handle_models`、`handle_health`、`handle_cognitive_state`、`handle_recall_memory`、`handle_reflect`、`handle_express`、`handle_bootstrap`、`handle_get/set_personality`、`handle_get_bond`、`handle_root`、`main`。

**影响**：Docker/README 实际跑的是**功能子集版**（无工具路由、无 RSI），而开发调试多用 `laap_brain.api`；对同一缺陷的修复必须改两份，必然分叉（历史上已分叉出工具路由/RSI 等差异）。这是当前架构层面最危险的问题。

**修复指向**：见手册 R3。

---

### ISSUE-004 【P1 高】测试基线不绿（20 failed / 174 passed）

**证据**：本机实测 `python -m pytest tests -q` → `20 failed, 174 passed, 19 warnings in 19.60s`。失败分组与根因：

| 失败测试 | 数量 | 根因 |
|---|---|---|
| `tests/test_api_security.py` | 6 | `pytest.mark.asyncio` 未知 → **pytest-asyncio 未安装**（`pyproject.toml:26` 已声明 dev extra，但当前环境未装）；`asyncio_mode=auto` 失效 |
| `tests/test_laap_api.py` | 5 | 同上（异步 handler 测试） |
| `tests/test_laap_tools.py` | 3 | 同上 |
| `tests/test_candidate_chart.py` / `test_kline_chart.py` | 4 | **matplotlib 未安装**（`_kline_chart.py:41` `ModuleNotFoundError`），且依赖根目录工作脚本 |
| `tests/test_memorize_market.py` / `test_record_watchlist.py` | 2 | 网络测试依赖**活 API**（`localhost:11546`），服务未启动即 urllib 失败；`pytest.mark.network` 未注册 |

**影响**：任何 CI 接入都会红灯；安全测试（9 个）实际从未通过，安全加固无法被验证。

**修复指向**：见手册 R4（装插件、注册 mark、网络测试 fixture 化）。

---

### ISSUE-005 【P1 高】依赖声明三处分叉

**证据**：

| 声明处 | 内容 |
|---|---|
| `requirements.txt:13-16` | flask、requests、numpy、aiohttp |
| `pyproject.toml:13-22` | hermes-agent、aiohttp、pyyaml、python-dotenv、psutil、numpy（**无 flask/requests**） |
| `Dockerfile:30` | `pip install flask requests numpy aiohttp`（第三份） |

**影响**：`pip install -e .`（pyproject）装出的环境缺 flask/requests；`pip install -r requirements.txt` 缺 hermes-agent/psutil/dotenv；Docker 镜像与两者都不同。同一项目三种安装结果，依赖漂移必然发生。

**修复指向**：见手册 R5（建议 pyproject 为唯一事实源）。

---

### ISSUE-006 【P1 高】悬空模块引用（VERSIONS.yaml）

**证据**：`VERSIONS.yaml:80-102` 的 `integration_points` 中 4 处引用的模块**均不存在**（已用 `Test-Path` 验证）：

| 声明 | 引用路径 | 实际状态 |
|---|---|---|
| `HermesChannel` | `aris_brain.hermes_channel` | ❌ 不存在 |
| `HermesToolBridge` | `laap.agent_core.hermes_tool_bridge` | ❌ `laap/agent_core/` 目录不存在；实际有 `laap/agi/hermes_integration.py` |
| `AGCognitiveBridge` | `laap_brain.agi_bridge` | ❌ 不存在 |
| `HandshakeProtocol` | `laap.handshake` | ❌ 不存在 |

**影响**：版本矩阵是集成排障的权威依据，指向不存在的模块会误导排查（与 ISSUE-003 双 API 同源——文档记录的是"设想架构"，代码是"实际架构"）。

**修复指向**：见手册 R6。

---

### ISSUE-007 【P1 高】API 默认绑定 0.0.0.0 且无认证

**证据**：
- `laap_brain/api.py:869`：`host = "0.0.0.0"`（注释自认"兼容现有部署"，建议设 `LAAP_HOST=127.0.0.1`）；
- `aris_brain/laap_brain_api.py:18-19`：`api_key: laap-brain (any value, not checked)` —— **任何值都不校验**；
- 所有 `/v1/*` 端点无认证、无限流（仅 `handle_chat_completions` 有消息数/长度上限 `L372-380`，`handle_recall_memory` 有 limit 上限 `L581`）。

**影响**：若部署到公网/局域网主机（Docker 已绑定 127.0.0.1 属良好实践，但裸机默认 0.0.0.0），认知状态、记忆、人格、RSI 端点全部暴露且可被任意调用，且 RSI 端点（`/v1/rsi_improve`、`/v1/rsi_full_cycle`）可被远程触发自我改进。

**修复指向**：见手册 R7。

---

### ISSUE-008 【P1 高】端口约定矛盾

**证据**：

| 位置 | 端口 |
|---|---|
| `laap_brain/api.py:861-865` `main()` 默认值 / env `LAAP_PORT` | **11530** |
| `laap_brain/config.py:85` `AO_PORT` 默认 | **11530** |
| `Dockerfile:57` `ENV LAAP_PORT=11546`、`CMD --port 11546` | **11546** |
| `docker-compose.yml:41` 端口映射 | **11546** |
| `README.md:318-320` 表格：`LAAP_API_BASE=...11546`，`LAAP_PORT=11530` | 两值并存 |
| `.env.example:31-32` `LAAP_PORT=11546` | **11546** |
| `mcp_server/laap_mcp_server.py:35` 默认 `LAAP_API_BASE=...11546` | **11546** |

**影响**：裸机 `python -m laap_brain.api` 起在 11530，但 MCP 客户端/文档/健康检查都打 11546 → "服务起来了但连不上"。

**修复指向**：见手册 R8（统一 11546）。

---

### ISSUE-009 【P1 高】敏感/运行时文件游离于版本控制策略之外

**证据**：`git status` 未跟踪项：
- `_wx_login_out.txt`（4KB）—— 含微信扫码登录输出与二维码链接（`https://liteapp.weixin.qq.com/q/...`），属**潜在凭证/隐私泄漏面**；
- `aris_chat_history.json` —— 对话历史；
- `aris_mode.json` —— 运行模式状态。

`.gitignore:59-62` 只忽略 `_gen_*.py`/`_build_*.py`，未覆盖上述文件；且 `_wx_login_out.txt` 一旦被 `git add -A` 误提交即泄漏。

**影响**：隐私/凭证泄漏风险；运行状态与源码边界不清。

**修复指向**：见手册 R9。

---

### ISSUE-010 【P2 中】测试分散在包内

**证据**：`tests/` 之外 9 个测试文件：`aris_brain/test_express.py`、`aris_brain/test_rules.py`、`aris_brain/psi_semiotics/test_comprehensive.py`、`hermes-integration/test_mcp_tools.py`、`laap/agi/test_affective_engine.py`、`test_consciousness_integrator.py`、`test_memory_system.py`、`test_meta_cognitive.py`、`test_unified_memory.py`。

`pyproject.toml:45` `testpaths = ["tests"]` → 上述测试在 CI 中**静默不执行**，其覆盖率是虚的。

**修复指向**：见手册 R10。

---

### ISSUE-011 【P2 中】巨型单文件模块

**证据**（按字节排序，Top 10）：

| 文件 | 大小 |
|---|---|
| `laap/agi/v5_upgrade.py` | 85 KB |
| `aris_brain/aris_lm_v5.py` | 78 KB |
| `aris_brain/aris_rules_engine.py` | 73 KB |
| `aris_brain/aris_cognitive_bridge.py` | 73 KB |
| `laap/agi/causal.py` | 71 KB |
| `laap/agi/world_model.py` | 65 KB |
| `aris_brain/laap_integrator.py` | 61 KB |
| `laap/agi/analogical.py` | 56 KB |
| `laap/agi/cognitive_bus.py` | 49 KB |
| `laap/agi/core.py` | 48 KB |

**影响**：单文件数千行，定位、评审、合并冲突成本高；与 ISSUE-012 叠加后维护风险显著。

**修复指向**：见手册 R11。

---

### ISSUE-012 【P2 中】功能重叠模块

**证据**（同名/同职责多处实现）：

| 领域 | 实现点 |
|---|---|
| 认知总线 | `aris_brain/cognitive_bus.py`（20KB）、`laap/agi/cognitive_bus.py`（49KB）、`laap/agi/cognitive_bus_sync.py`（30KB）、`aris_brain/aris_cognitive_bridge.py` |
| 记忆 | `aris_brain/laap_semantic_memory.py`、`laap_memory_hierarchy.py`、`aris_episodic_memory.py`、`memory_store.py`、`memory_bridge.py` + `laap/agi/memory_system.py`、`unified_memory.py` |
| 情感 | `aris_brain/aris_emotion_engine.py`、`emotional_engine.py` + `laap/agi/affective_engine.py` |
| 进化/自改进 | `laap/agi/evolution_engine.py`、`evolution_system.py`、`code_evolution.py`、`v5_upgrade.py`、`meta_learning.py`、`rsi_engine.py` |

另注意 `.gitignore:78` 忽略了 `aris_brain/laap_semantic_memory.json` —— 语义记忆的**运行时数据**在 git 之外，若该 JSON 是唯一持久化点，备份/迁移会丢记忆（与 ISSUE-009 同类风险）。

**影响**：同一概念两套实现，修复一处另一处不生效；认知状态可能不一致（双引擎竞态）。

**修复指向**：见手册 R12（需先建调用图，逐个收敛）。

---

### ISSUE-013 【P2 中】laap-enterprise 打包缺口

**证据**：
- `pyproject.toml:33-39` `[tool.setuptools.packages.find]` include 仅 `laap / laap_brain / aris_brain / psi_core / mcp_server`，**不含 `laap_enterprise`**；
- `laap-enterprise/` 自带独立 `pyproject.toml` + `LICENSE.md`（商业授权语义），模块含 `federation.py`、`rbac.py`、`telemetry.py`、`audit_logger.py`、`license_manager.py`。

**影响**：`pip install -e .` 装不到企业模块，但 README/许可文件把企业功能列为"商业版"，安装/分发路径缺失。

**修复指向**：见手册 R13。

---

### ISSUE-014 【P2 中】根目录工作脚本混杂

**证据**：根目录 17 个 `_*.py`：`_kline_chart.py`、`_candidate_chart.py`、`_market_summary_demo.py`、`_memorize_*`（3 个）、`_morning_score.py`、`_short_term_pick.py`、`_watchlist_*`（3 个）、`_latest_llm_papers.py`、`_search_papers_demo.py`、`_read_and_memorize.py`、`_record_watchlist.py`、`_aris_speak.py`、`_psi_probe.py`、`_query_stock_demo.py`。

与 `tests/test_kline_chart.py`、`test_candidate_chart.py` 有直接依赖；`.gitignore:59-62` 的 `_gen_*.py`/`_build_*.py` 模式与这些 `_*.py` 命名空间冲突。

**影响**：仓库根混乱，新人无法区分"产品代码"与"个人工作流脚本"。

**修复指向**：见手册 R14。

---

### ISSUE-015 【P2 中】pytest 配置缺失

**证据**：
- `pyproject.toml:44-46` 仅 `asyncio_mode = "auto"` + `testpaths`，无 `markers` 注册；`tests/test_memorize_market.py:72`、`test_record_watchlist.py:71` 使用 `@pytest.mark.network` → 运行期 `PytestUnknownMarkWarning`（19 个 warning 之一）；
- `dev` extra 声明了 pytest-asyncio 但环境未安装（ISSUE-004 根因）。

**修复指向**：见手册 R4/R10。

---

### ISSUE-016 【P3 低】包元数据不完整

**证据**：`pyproject.toml` 无 `[project.urls]`、无 classifiers、无作者邮箱；`license` 字段格式与 PEP 639 建议不一致（裸字符串）。

**影响**：PyPI 展示信息缺失；工具链（如 `pip`、`hatch`）解析 license 字段告警。

**修复指向**：见手册 R15。

---

## 3. 优先级裁定与修复顺序建议

```
第 1 波（P0，1-2 人日）：ISSUE-003（双 API 收敛）→ ISSUE-001（docker-compose）→ ISSUE-008（端口，随 003 一起）
第 2 波（P1 合规，0.5 人日）：ISSUE-002（许可，需权利人确认）
第 3 波（P1 质量，1 人日）：ISSUE-004 + ISSUE-005 + ISSUE-015（测试与依赖基线）
第 4 波（P1 安全，0.5 人日）：ISSUE-007 + ISSUE-009
第 5 波（P2，2-3 人日）：ISSUE-006 + ISSUE-010 + ISSUE-014 + ISSUE-013 + ISSUE-016
第 6 波（P2 代码健康，3-5 人日，可拆多 PR）：ISSUE-011 + ISSUE-012
```

> 每项的具体文件路径、函数/类锚点、修订步骤与验证方式，见《LAAP 实施修订业务参考手册》。
