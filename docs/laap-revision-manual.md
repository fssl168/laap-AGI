# LAAP 实施修订业务参考手册（文件+函数级）

> **手册版本**: v1.0
> **配套文档**: 《LAAP-AGI 问题汇总报告》（`docs/laap-issue-report.md`）——本手册按报告中的 ISSUE 编号给出可执行修订方案。
> **定位**: 面向维护者的"改哪里、改成什么、怎么验证"的操作手册；每个修订项给出**文件路径 + 行号 + 函数/类锚点 + 具体动作 + 验证命令 + 回归风险**。
> **基线事实**: 本手册的行号基于 2026-08 当前工作树（main 分支）；`pytest tests -q` 基线 = **174 passed / 20 failed**；`docker-compose.yml` 当前无法通过 YAML 解析。

---

## 0. 通用执行纪律（每个修订项都适用）

1. **工作分支**：每项在独立分支（`fix/xxx`）上进行，遵循仓库现有 Conventional Commits 中文风格（`fix:`/`feat:`/`chore:`/`docs:`/`security:`/`sync:`）。
2. **改前备份**：涉及 `docker-compose.yml`、`pyproject.toml`、`LICENSE*`、API 入口的改动，先 `git stash` 或复制备份。
3. **改后必验**：每个修订项至少执行一次"验证"小节中的命令，并把结果写进 commit message。
4. **回归纪律**：P0/P1 修订完成后跑一次 `python -m pytest tests -q`，禁止让失败数高于修订前基线。
5. **环境**：本机需先补齐开发依赖（R4 的前置动作），否则异步测试无法验证。

---

## 1. 修订路线图

| 阶段 | 修订项 | 目标 | 工作量（人日） |
|---|---|---|---|
| Phase 0 基线 | — | 建分支、记录测试基线、grep 索引 | 0.5 |
| Phase 1 部署阻断 | R1、R3、R8 | `docker compose up` 可用；单一 API 入口；端口统一 | 1–1.5 |
| Phase 2 合规 | R2 | 许可唯一事实源（需权利人确认） | 0.5 |
| Phase 3 质量基线 | R4、R5、R15 | 测试全绿；依赖单一化；元数据完整 | 1 |
| Phase 4 安全与卫生 | R7、R9、R14 | 默认安全绑定；敏感文件治理；脚本归档 | 0.5–1 |
| Phase 5 文档校准 | R6、R13 | 悬空引用清零；企业包可安装 | 0.5 |
| Phase 6 代码健康 | R10、R11、R12 | 测试集中；巨型文件拆分；重叠收敛 | 3–5（可拆多 PR） |

**执行状态（2026-08）**：Phase 0–5 已全部完成；Phase 6 完成 R10 与 R12 的僵尸清理子集（删除 11 个零引用模块）。**待办**：R11 巨型文件拆分（`laap/agi/v5_upgrade.py` 85KB、`aris_brain/aris_lm_v5.py` 78KB、`aris_brain/aris_rules_engine.py` 73KB、`aris_brain/aris_cognitive_bridge.py` 73KB、`laap/agi/causal.py` 71KB——其中仅 `aris_rules_engine` 与 `causal`/`world_model` 有测试覆盖，按纪律"先补测试再拆"）；R12 剩余收敛（认知总线 `cognitive_bus.py` 双份、记忆 6 处实现、情感 3 处实现的调用图收敛）。

依赖关系：R3（API 收敛）先行，R8（端口）随 R3 一起；R4 需要 R5 的依赖声明先落地；R10 可在 R4 之后独立做；R11/R12 相互独立但都依赖 R4 提供回归保障。

---

## 2. 修订项明细

---

### R1 修复 docker-compose.yml（对应 ISSUE-001）

**现状**：`docker-compose.yml` 无法通过 YAML 解析；`laap-mcp` 端口映射错位、API 地址自指、依赖外部网络。
**目标**：`docker compose config -q` 通过；`docker compose up -d` 一键可用。

**涉及文件与锚点**：

| 位置 | 现状 | 修订动作 |
|---|---|---|
| `docker-compose.yml:41` | `- "127.0.0.1:11546:11546"   //这样处理是适应避免VPS的端口被利用` | 删除行尾 `//...` 注释，保留 `- "127.0.0.1:11546:11546"` |
| `docker-compose.yml:93` | `- "127.0.0.1:11546:11546"      //这样处理是适应避免VPS的端口被利用` | 改为 `- "127.0.0.1:11547:11547"`（对齐 `:90` 的 `--port 11547` 与 `:102` 健康检查） |
| `docker-compose.yml:96` | `LAAP_API_BASE=http://laap-mcp:11547` | 改为 `LAAP_API_BASE=http://aris:11546`（MCP 客户端要调用的是 Brain API，不是自己） |
| `docker-compose.yml:112` | `- hermes-net // 这里要跟随hermes agent的网络，需要自定义` | 删除 `//` 注释；与 `:121` 一起决策网络 |
| `docker-compose.yml:121` | `hermes-net:  // ...` + `external: true` | 二选一：① 删除 `external: true`，让 compose 自建 `hermes-net`（推荐，零外部依赖）；② 保留 external 并在 README 写明需先 `docker network create hermes-net` |
| `docker-compose.yml:75-80` | `deploy.resources.limits.memory: 2G` | 保留（非 swarm 下仅警告）；若想本地生效可改为顶层 `mem_limit`（可选） |

**修订步骤**：
1. 全局删除 `//` 行尾注释（仅 `docker-compose.yml`，共 5 处：L41、L93、L112、L121，以及 `:41` 上方 L82 处无注释无需动）。
2. 按上表修正 `laap-mcp` 端口与 `LAAP_API_BASE`。
3. 网络方案选①（自建网络），删除 `external: true`。
4. 检查 `docker-compose.override.yml` 是否也有同类注释问题（当前文件 518 字节，需复查）。

**验证**：
```bash
python -c "import yaml; yaml.safe_load(open('docker-compose.yml', encoding='utf-8')); print('YAML OK')"
docker compose config -q   # 无输出即配置合法
docker compose up -d       # 两服务 healthy
curl http://localhost:11546/health && curl http://localhost:11547/sse
```

**回归风险**：低。仅部署编排改动；若选①，外部调用方需改用 compose 网络名 `hermes-net`（compose 会自建同名网络，名称不变）。

**工作量**：0.25 人日。

---

### R2 统一许可体系（对应 ISSUE-002）

**现状**：`LICENSE`=AGPL-3.0、`pyproject.toml:12`=BUSL-1.1、README 徽章=Apache 2.0、`LICENSING.md`=分层策略，四处不一致。
**目标**：确立**唯一事实源**，其余文件与之对齐。**本项必须先由权利人（Lorry）拍板**，手册提供两个候选方案：

**方案 A（推荐）：以 `LICENSING.md` 分层策略为事实源**（它对层级覆盖最完整）
- 该策略下根 `LICENSE` 应对应**层级 3（psi_core，Apache 2.0）**；git 历史显示 AGPL 是中间态（`63bb996` 起），最终 README 铭牌已写 Apache 2.0 (Community)。
- 动作清单：
  1. 将根 `LICENSE` 全文替换为 **Apache License 2.0 标准文本**（Apache 官方全文）。
  2. `pyproject.toml:12`：`license = "BUSL-1.1"` → `license = "Apache-2.0"`，并加注释说明核心引擎另受 `LICENSING.md` 分层约束（PEP 639 下用 `license-files` 列出 `LICENSING.md`、`LICENSE.BSL`、`COMMERCIAL_LICENSE.md`）。
  3. `README.md:594`（项目结构注释 `LICENSE # Apache 2.0`）已正确，无需改；`README.md:23` 徽章已是 Apache 2.0，保留。
  4. `LICENSE.BSL`（BSL 1.1 文本）与 `COMMERCIAL_LICENSE.md` 保留不动。
  5. 在 `LICENSING.md` 末尾新增"变更历史"小节，记录 4 次许可变更（`3b03d77`→`63bb996`→`255f111/5eee2d8/3b15a6e`→本次对齐）。

**方案 B：以 AGPL-3.0 为事实源**
- 保留 `LICENSE` 不变，重写 `LICENSING.md` 各层级引用（层级 3 改为 AGPL），`pyproject.toml:12` 改 `license = "AGPL-3.0"`，README 徽章改 AGPL。此方案与 README 现有宣传（Apache 2.0 Community）冲突，不推荐。

**涉及文件**：`LICENSE`、`LICENSE.BSL`（不动）、`LICENSING.md`、`COMMERCIAL_LICENSE.md`（不动）、`pyproject.toml`、`README.md`（核对）。

**验证**：
```bash
grep -rn "AGPL\|BUSL\|Apache" --include="*.md" --include="*.toml" . | grep -v ".venv"   # 逐条核对一致性
python -m build 2>/dev/null || pip install -e . && pip show laap | Select-String License
```

**回归风险**：低（纯文档/元数据），但**法律风险需权利人确认**——本手册不构成法律意见。

**工作量**：0.5 人日（含决策评审）。

---

### R3 收敛双 API 入口（对应 ISSUE-003）

**现状**：`aris_brain/laap_brain_api.py`（563 行，子集）与 `laap_brain/api.py`（888 行，全功能）并存；Docker/README 跑子集版。
**目标**：`laap_brain/api.py` 为**唯一实现**；`aris_brain/laap_brain_api.py` 降级为兼容包装或删除；Docker/README 全部指向唯一入口。

**决策依据**：`laap_brain/api.py` 功能超集（工具路由 `L394-470`、RSI 端点 `L750-805`、自动记忆 `L222-291`、LLM 兜底 `L112-166`、SSE 流式 `L501-526`），且是 pyproject 官方 console script（`pyproject.toml:29` `laap-brain`）。

**修订步骤（函数级）**：

1. **改 `laap_brain/api.py::main()`（L860-884）端口与默认绑定**：
   - L861-865：`port = 11530` → 统一为 `int(os.environ.get("LAAP_PORT", "11546"))`（配合 R8）。
   - L869：`host = "0.0.0.0"` → `os.environ.get("LAAP_HOST", "127.0.0.1")`（配合 R7；如确需局域网暴露，由部署方显式设 `LAAP_HOST=0.0.0.0`）。
   - 更新 docstring（L8-12）中的端口示例。

2. **把 `aris_brain/laap_brain_api.py` 改为兼容包装（或删除）**：
   - 方式 A（推荐，最小改动）：文件头部保留，内容替换为：
     ```python
     """DEPRECATED: 统一入口为 laap_brain.api，本文件仅为兼容保留。"""
     from laap_brain.api import *          # noqa: F401,F403
     from laap_brain.api import main       # noqa: F401
     ```
   - 方式 B：直接删除文件，并同步改所有引用（见步骤 3）。
   - 删除前必须 grep 确认无其他 import：`grep -rn "laap_brain_api" --include="*.py" .`

3. **改部署引用**：
   - `Dockerfile:65`：`CMD ["python", "aris_brain/laap_brain_api.py", "--port", "11546"]` → `CMD ["python", "-m", "laap_brain.api", "--port", "11546"]`。
   - `README.md:379`：`python aris_brain/laap_brain_api.py --port 11546` → `python -m laap_brain.api --port 11546`。
   - `README.md:534`（项目结构）删除/标注 `laap_brain_api.py` 为 deprecated。
   - `laap-quickstart.sh`：grep `laap_brain_api` 并同步（若引用）。

4. **行为核对**：确认 `laap_brain/api.py` 覆盖了子集版全部端点：子集版 16 个函数（`_get_psi_adapter` L44、`get_laap_engine` L81、`process_with_laap` L105、`handle_*` 12 个、`main` L600）——全功能版全部具备（除 `get_laap_engine`，若子集版该函数被外部引用，在包装层补导出）。

**涉及文件**：`laap_brain/api.py`、`aris_brain/laap_brain_api.py`、`Dockerfile`、`README.md`、`laap-quickstart.sh`（视引用）。

**验证**：
```bash
python -m laap_brain.api --port 11546   # 启动后
curl http://localhost:11546/health       # 200 且 engines_loaded 字段一致
python -m pytest tests/test_laap_api.py tests/test_api_security.py -q   # 全绿
python -c "import aris_brain.laap_brain_api as m; print(m.main)"        # 包装导入可用（方式 A）
```

**回归风险**：**中**。收敛后行为统一为全功能版：工具路由启用后，`/v1/chat/completions` 在带 `tools` 参数时可能返回 `tool_calls`（原子集版不会），下游 Hermes 需能处理工具调用；RSI 端点新增暴露面（配合 R7 加认证）。建议收敛后做一次 Hermes 联调冒烟。

**工作量**：0.5–1 人日。

---

### R4 恢复测试基线（对应 ISSUE-004、ISSUE-015）

**现状**：`pytest tests -q` = 20 failed / 174 passed。根因：① pytest-asyncio 未安装；② matplotlib 未安装；③ 网络测试依赖活 API。
**目标**：无 pytest-asyncio/matplotlib 类环境错误；网络测试默认跳过而非失败。

**修订步骤**：

1. **安装插件**（本机 + CI）：
   ```bash
   pip install "pytest>=8.0" "pytest-asyncio>=0.23"
   ```
   这将修复 `tests/test_api_security.py`（6 失败）、`tests/test_laap_api.py`（5）、`tests/test_laap_tools.py`（3）共 14 个异步测试。验证 `pyproject.toml:44` `asyncio_mode = "auto"` 生效：`pytest tests/test_laap_api.py -q`。

2. **注册 `network` mark 并默认排除**（`pyproject.toml:44-46` `[tool.pytest.ini_options]`）：
   ```toml
   markers = ["network: tests that require a live LAAP API at localhost:11546"]
   addopts = "-m 'not network'"
   ```
   这修复 `test_memorize_market.py:72`、`test_record_watchlist.py:71` 的 `PytestUnknownMarkWarning`，并让 CI 默认跳过网络测试。

3. **网络测试 fixture 化**（新增 `tests/conftest.py`）：
   ```python
   import pytest, urllib.request
   @pytest.fixture
   def laap_api_live():
       try:
           urllib.request.urlopen("http://localhost:11546/health", timeout=2)
           return True
       except Exception:
           pytest.skip("LAAP API not running at localhost:11546")
   ```
   在 `tests/test_memorize_market.py::test_memorize_recall_roundtrip`（L72 附近）与 `tests/test_record_watchlist.py::test_memory_recall_roundtrip`（L71 附近）加入参 `laap_api_live`，替换裸 urllib 调用。

4. **matplotlib 归属决策**（二选一）：
   - 若 R14 把 `_kline_chart.py`/`_candidate_chart.py` 归档为"工作脚本"：将 `tests/test_kline_chart.py`、`test_candidate_chart.py` 一并迁入归档目录，并加 `skipif`（matplotlib 缺失时跳过而非失败）；
   - 若保留：把 `matplotlib` 加入 dev extra（`pyproject.toml:26`）并在 README 标注。
   - 推荐前者（脚本不是产品代码）。

**验证**：
```bash
pip install "pytest>=8.0" "pytest-asyncio>=0.23"
python -m pytest tests -q     # 期望：0 failed；网络测试 skipped
python -m pytest tests -m network -q   # 本地起服务后应通过
```

**回归风险**：低。

**工作量**：0.5–1 人日。

---

### R5 依赖声明单一化（对应 ISSUE-005）

**现状**：`requirements.txt`、`pyproject.toml:13-22`、`Dockerfile:30` 三份依赖声明不一致。
**目标**：`pyproject.toml` 为唯一事实源。

**修订步骤**：

1. **先做依赖使用审计**（禁止凭印象删依赖）：
   ```bash
   grep -rn "^import flask\|^from flask" --include="*.py" . | grep -v ".venv|__pycache__"
   grep -rn "^import requests\|^from requests" --include="*.py" . | grep -v ".venv|__pycache__"
   ```
   - `requests` 确认被 `mcp_server/laap_mcp_server.py:32` 使用 → **必须保留**；
   - `flask` 若零使用 → 从 `requirements.txt` 删除并在手册记录；若有使用，移入 `pyproject.toml`。

2. **统一到 pyproject**：
   - `pyproject.toml:13-22` dependencies 补齐审计后的全集（建议：`aiohttp`、`pyyaml`、`python-dotenv`、`psutil`、`numpy`、`hermes-agent`、`requests`（若审计命中）、`flask`（若审计命中））。
   - `requirements.txt` 整体替换为：
     ```
     # 依赖唯一事实源：pyproject.toml
     # 安装: pip install -e .
     -e .
     ```
   - `Dockerfile:30`：`RUN pip install --no-cache-dir flask requests numpy aiohttp && pip install --no-cache-dir -e . 2>/dev/null || true` → 简化为 `RUN pip install --no-cache-dir -e .`；删除 `|| true`（掩盖安装失败）。

3. **版本约束对齐**：`VERSIONS.yaml:30` `psutil >=5.9,<7` 与 `pyproject.toml:21` `psutil>=5.9,<8` 不一致——以 pyproject 为准（git 历史 `d37e101` 已放宽到 `<8`），更新 VERSIONS.yaml。

**验证**：
```bash
python -m venv /tmp/laap-verify && /tmp/laap-verify/Scripts/pip install -e .   # 全新环境可装
/tmp/laap-verify/Scripts/python -m laap_brain.api --port 11546                 # 可启动
docker build -t laap-aris .                                                    # 镜像构建成功（不再 || true）
```

**回归风险**：**中**。hermes-agent 为外部包，安装失败将阻断整体安装——先在当前环境 `pip install hermes-agent` 验证可解析（`VERSIONS.yaml` 约束 0.18.x）。

**工作量**：0.5 人日。

---

### R6 校准悬空引用与文档（对应 ISSUE-006）

**现状**：`VERSIONS.yaml:80-102` 4 处 `integration_points` 指向不存在的模块。
**目标**：版本矩阵中的每个引用路径经 `Test-Path` 验证存在。

**修订步骤**：

1. 逐条处理 `VERSIONS.yaml:80-102`：
   - `HermesChannel` → `aris_brain.hermes_channel`：**不存在**。先 grep 确认 `HermesChannel` 类实际位置（候选：`aris_brain/cognitive_bus.py` 或 `laap_brain/integrator.py` 内的通道实现）；找到后改路径，找不到则删除该 entry。
   - `HermesToolBridge` → `laap.agent_core.hermes_tool_bridge`：改为实际存在的 `laap/agi/hermes_integration.py`（或其中的真实类名）。
   - `AGCognitiveBridge` → `laap_brain.agi_bridge`：**不存在**。grep 确认实际桥接位置（候选 `laap/agi/hermes_integration.py` / `laap_brain/integrator.py`），改路径或删除。
   - `HandshakeProtocol` → `laap.handshake`：**不存在**。grep `handshake` 找真实实现；无则删除。
2. 对每个 entry 增加 `verified: <date>` 字段与 `status` 复核。
3. 建立**引用校验脚本**（建议 `scripts/check_doc_refs.py`）：解析 `VERSIONS.yaml`、`README.md` 项目结构块中的 `模块路径` 与 `*.py` 相对路径，逐个 `Path.exists()` 校验，纳入 CI（可选 Phase 6）。
4. README 项目结构（L528-596）同步修正：补 `laap_brain/tools.py`、`tests/` 实际 18 个文件、双 API 收敛后的入口说明。

**涉及文件**：`VERSIONS.yaml`、`README.md`、新增 `scripts/check_doc_refs.py`。

**验证**：
```bash
python scripts/check_doc_refs.py    # 输出全部引用校验结果，0 悬空
grep -rn "hermes_channel\|agent_core\|agi_bridge\|laap.handshake" --include="*.py" . | grep -v ".venv"
```

**回归风险**：低。

**工作量**：0.5 人日。

---

### R7 API 安全加固（对应 ISSUE-007）

**现状**：`laap_brain/api.py:869` 默认绑定 `0.0.0.0`；全部端点无认证。
**目标**：默认仅本机可达；可选 API Key 校验；错误响应不泄露内部异常。

**修订步骤**：

1. **默认绑定收紧**：`laap_brain/api.py::main()` L869 `host = "0.0.0.0"` → `os.environ.get("LAAP_HOST", "127.0.0.1")`；同步 `README.md:314-326`（端口表上方）与 `.env.example` 增加 `LAAP_HOST` 示例注释。

2. **可选 API Key 中间件**（在 `laap_brain/api.py::create_app()` L839-857 注册）：
   ```python
   @web.middleware
   async def auth_middleware(request, handler):
       key = os.environ.get("LAAP_API_KEY", "")
       if not key:
           return await handler(request)            # 未配置 key 时保持兼容（默认开放，仅本机）
       auth = request.headers.get("Authorization", "")
       if auth == f"Bearer {key}":
           return await handler(request)
       return web.json_response({"error": "unauthorized"}, status=401)

   app = web.Application(middlewares=[auth_middleware])
   ```
   在 README 与 `.env.example` 注明：`LAAP_API_KEY` 一旦配置，所有 `/v1/*` 与 `/health` 之外端点均需 `Authorization: Bearer <key>`。

3. **异常回显审计**：grep 所有 `handle_*` 的异常分支，确认对外返回仅 `"internal error"`（现实现已基本满足：如 `handle_cognitive_state` L568、`handle_recall_memory` L606）；将 `handle_bootstrap` L699-705 的兜底响应中不暴露堆栈。

4. **若保留 `aris_brain/laap_brain_api.py`（R3 方式 A）**：其中 `main()`（L600）同样收紧绑定；docstring L18-19 的 `api_key: laap-brain (any value, not checked)` 文案删除或改为真实校验说明。

**涉及文件**：`laap_brain/api.py`、`aris_brain/laap_brain_api.py`（若保留）、`README.md`、`.env.example`。

**验证**：
```bash
LAAP_API_KEY=test123 python -m laap_brain.api --port 11546 &
curl -s http://localhost:11546/health | head -c 50                          # 200
curl -s http://localhost:11546/v1/models | head -c 50                        # 401
curl -s -H "Authorization: Bearer test123" http://localhost:11546/v1/models | head -c 50  # 200
python -m pytest tests/test_api_security.py -q   # 现有 9 个安全测试全绿
```

**回归风险**：低-中。改默认绑定后，依赖局域网访问的部署需显式设 `LAAP_HOST`；配置 `LAAP_API_KEY` 后所有客户端需带 token（README 必须同步，避免"改了密钥客户端全挂"）。

**工作量**：0.5 人日。

---

### R8 端口约定统一（对应 ISSUE-008）

**现状**：11530（代码默认）vs 11546（Docker/文档/.env）并存。
**目标**：全项目统一 **11546**（Docker/README/.env.example/MCP 已用 11546，取多数派）。

**修订步骤**：

| 位置 | 动作 |
|---|---|
| `laap_brain/api.py:861-865` `main()` | 默认端口 `11530` → `int(os.environ.get("LAAP_PORT", "11546"))` |
| `laap_brain/config.py:85` `AO_PORT` | 默认 `11530` → `11546`（或在代码注释标注"历史遗留，仅 AO 服务使用"；需 grep `AO_PORT` 调用方后决定） |
| `README.md:318-320` | 表格 `LAAP_PORT` 值改为 `11546` |
| `aris_brain/laap_brain_api.py:14`（若保留） | docstring `Start on :11530` → `:11546` |
| `mcp_server/laap_mcp_server.py:35` | 已用 11546，不动 |

**验证**：
```bash
grep -rn "11530" --include="*.py" --include="*.md" --include="*.yml" --include="*.yaml" --include="*.toml" . | grep -v ".venv"   # 除历史注释外应为 0
python -m laap_brain.api &  # 启动日志显示 11546
```

**回归风险**：低。唯一风险是外部已有调用方写死 11530——README 发布说明中标注端口变更。

**工作量**：0.25 人日（与 R3 合并执行）。

---

### R9 敏感/运行时文件治理（对应 ISSUE-009）

**现状**：`_wx_login_out.txt`（微信登录输出，含二维码 URL）、`aris_chat_history.json`、`aris_mode.json` 均未跟踪、未忽略。
**目标**：敏感文件不进入仓库；运行时状态与源码边界清晰。

**修订步骤**：

1. **处置 `_wx_login_out.txt`**：内容为一次性微信登录输出（`_wx_login_out.txt:1-5`），建议直接删除；如确需保留样本，移入 `logs/`（已被 `.gitignore:20` 忽略）。
2. **`aris_chat_history.json` / `aris_mode.json`**：判定为运行时状态 → 若逻辑上属于 `state/`（被忽略），将代码写路径改到 `state/` 下；否则直接加入 `.gitignore`。
3. **扩展 `.gitignore`**（在 L59-62 临时文件段之后新增）：
   ```gitignore
   # ========== 会话/运行时产物 ==========
   _wx_*.txt
   aris_chat_history.json
   aris_mode.json
   ```
4. **历史泄漏检查**：`git log --all --name-only --pretty=format: | Select-String "_wx_login_out|token|secret"` 确认无敏感文件曾入库；若有，走 `git filter-repo`（高风险操作，需单独评审）。

**验证**：
```bash
git status --porcelain        # 干净或仅预期变更
git check-ignore _wx_login_out.txt aris_chat_history.json aris_mode.json   # 全部命中
```

**回归风险**：低。注意：若 `aris_chat_history.json` 被运行时代码依赖（grep 确认），删除/移动前需同步代码写路径。

**工作量**：0.25 人日。

---

### R10 测试集中化（对应 ISSUE-010）

**现状**：9 个测试文件散落在包内，`testpaths=["tests"]` 不收集。
**目标**：所有测试收归于 `tests/`，CI 全量执行。

**修订步骤**（迁移映射）：

| 原位置 | 目标位置 |
|---|---|
| `aris_brain/test_express.py` | `tests/test_aris_express.py` |
| `aris_brain/test_rules.py` | `tests/test_aris_rules.py` |
| `aris_brain/psi_semiotics/test_comprehensive.py` | `tests/test_psi_semiotics.py` |
| `hermes-integration/test_mcp_tools.py` | `tests/test_mcp_tools.py` |
| `laap/agi/test_affective_engine.py` | `tests/test_agi_affective_engine.py` |
| `laap/agi/test_consciousness_integrator.py` | `tests/test_agi_consciousness_integrator.py` |
| `laap/agi/test_memory_system.py` | `tests/test_agi_memory_system.py` |
| `laap/agi/test_meta_cognitive.py` | `tests/test_agi_meta_cognitive.py` |
| `laap/agi/test_unified_memory.py` | `tests/test_agi_unified_memory.py` |

每个文件迁移时检查：
- 头部 `sys.path` 注入（包内测试通常注入根目录/aris_brain）——保留即可，但改为相对 `Path(__file__).resolve().parent.parent` 解析；
- 断言是否依赖"包内相对路径"（如 `state/` 目录），统一改为 pytest `tmp_path` fixture；
- `laap/agi/test_*.py` 若依赖 `laap.agi` 包内部导入，迁移后 `import laap.agi.X` 应仍可用（包已安装或根目录在 path）。

**验证**：
```bash
python -m pytest tests -q --collect-only | tail -5   # 收集数应增加 ≥9
python -m pytest tests -q                             # 全绿或仅网络 skipped
```

**回归风险**：中。包内测试可能隐含"从包目录运行"的假设；逐个迁移、逐个跑。

**工作量**：0.5–1 人日。

---

### R11 巨型文件拆分（对应 ISSUE-011）

**现状**：6 个 70KB+ 文件（最大 `laap/agi/v5_upgrade.py` 85KB）。
**目标**：优先拆分**被测试覆盖**的文件，拆后原导入路径零破坏。

**通用拆分模板**（每个文件独立 PR）：
1. 建函数/类清单：`grep -n "^def \|^class \|^    def " <file>`。
2. 按内聚分组 → 子模块（例：`aris_brain/aris_rules_engine.py` 拆为 `rules_defs.py` 规则表、`rules_engine.py` Engine、`rules_api.py` 门面）。
3. 原文件保留为**薄门面**：`from .rules_api import *` + `__all__` 完整导出，确保 `from aris_rules_engine import process, get_engine` 等既有导入不破坏。
4. 每拆一个文件立即跑其关联测试。

**建议拆分顺序（按回归保障从高到低）**：

| 文件 | 拆分建议 | 回归保障 |
|---|---|---|
| `aris_brain/aris_rules_engine.py`（73KB） | 规则定义表 / Engine 类 / `process()`+`get_engine()` 门面 | `tests/test_aris_rules.py`（迁移后）+ `test_rules_engine_security.py` |
| `laap/agi/core.py`（48KB） | `AGIAgent` 保持，模块装配段拆到 `assembler.py` | `tests/test_laap_agi.py` |
| `laap/agi/world_model.py`（65KB） | 各 WorldModel 后端拆到 `world_models/` | `tests/test_laap_agi.py` |
| `laap/agi/causal.py`（71KB） | `CausalEngine` 与规则模型拆开 | `tests/test_laap_agi.py` |
| `laap/agi/v5_upgrade.py`（85KB） | 无测试覆盖 → **拆分优先级最低**，或先补测试再拆 | 无 |

**验证**：拆分后 `python -m pytest tests -q` 失败数不增；`grep -rn "from <原模块> import" .` 全部可用。

**回归风险**：**高**（大文件无测试覆盖的部分是盲区）。纪律：只拆有测试覆盖的文件；无覆盖文件先补冒烟测试。

**工作量**：每文件 0.5–1 人日，总计 3–5 人日（可跨多个迭代）。

---

### R12 重叠模块收敛（对应 ISSUE-012）

**现状**：认知总线/记忆/情感/进化均有 ≥2 套实现；`aris_brain/laap_semantic_memory.json` 运行时数据被 `.gitignore:78` 忽略。
**目标**：每个领域单一事实源，先建调用图、再定事实源、最后删副本。

**分领域收敛方案**：

| 领域 | 候选事实源 | 待收敛副本 | 收敛动作 |
|---|---|---|---|
| 认知总线 | `laap/agi/cognitive_bus.py`（49KB，功能最全） | `aris_brain/cognitive_bus.py`（20KB）、`laap/agi/cognitive_bus_sync.py`、`aris_brain/aris_cognitive_bridge.py`（桥接层，可保留） | ① grep 各调用方；② 若 `aris_brain/cognitive_bus.py` 仅被 `aris_brain/` 内部用，改为 re-export `laap.agi.cognitive_bus` 或按调用方改写；③ 跑全量测试 |
| 记忆 | 按调用方划分：语义记忆 `aris_brain/laap_semantic_memory.py`（API 层 L130-146 直接调用）；AGI 侧 `laap/agi/unified_memory.py` | `laap_memory_hierarchy.py`、`memory_store.py`、`memory_bridge.py`、`memory_system.py` | 先输出调用矩阵（`grep -rn "import laap_memory_hierarchy\|import memory_store\|import memory_bridge\|import memory_system"`），识别僵尸模块（零调用）后删除；被调用的按统一接口收敛 |
| 情感 | `aris_brain/aris_emotion_engine.py`（API/PSI 链路用） | `emotional_engine.py`、`laap/agi/affective_engine.py` | 同上：调用矩阵 → 僵尸模块删除 → 接口统一 |
| 进化/自改进 | `laap/agi/rsi_engine.py`（有 API 端点引用） | `evolution_engine.py`、`evolution_system.py`、`code_evolution.py`、`meta_learning.py`、`v5_upgrade.py` | 同上 |

**附加项**：`.gitignore:78` 的 `aris_brain/laap_semantic_memory.json`——若它是语义记忆唯一持久化点，需评估：① 保留忽略但在备份脚本显式包含；或 ② 将记忆数据路径迁移到 `state/`（已被忽略）并统一命名，避免"记忆丢失"事故。

**验证**：收敛前后 `pytest tests -q` 失败数不增；`python -m laap_brain.api` 冒烟（对话触发语义记忆与情感路径）。

**回归风险**：**高**。每个领域一个独立工作项 + 独立 PR；禁止一次性批量删除。所有"删除"前必须输出零调用证明。

**工作量**：每领域 0.5–1 人日，总计 2–4 人日。

---

### R13 laap-enterprise 打包修复（对应 ISSUE-013）

**现状**：`laap-enterprise/` 有独立 `pyproject.toml` + `LICENSE.md`，但根 pyproject 不含 `laap_enterprise`，安装方式无文档。
**目标**：企业包可独立安装；README 说明许可与安装边界。

**修订步骤**：
1. **保持独立包**（推荐，商业授权语义）：验证 `laap-enterprise/pyproject.toml` 可构建；README 增加：
   ```markdown
   ## 企业版（商业授权）
   安装: pip install ./laap-enterprise
   使用需商业授权（见 COMMERCIAL_LICENSE.md），模块: federation / rbac / telemetry / audit_logger / license_manager
   ```
2. 若希望随主包安装：在根 `pyproject.toml:33-39` include 增加 `"laap_enterprise", "laap_enterprise.*"`——**不推荐**，会稀释商业边界。
3. 检查 `laap-enterprise/tests/test_enterprise.py` 是否被根 testpaths 收集（`tests/` 只指根目录，不会收集——确认无需收集或迁移）。

**验证**：
```bash
pip install ./laap-enterprise && python -c "import laap_enterprise.rbac, laap_enterprise.federation"
```

**回归风险**：低。

**工作量**：0.25 人日。

---

### R14 根目录工作脚本归档（对应 ISSUE-014）

**现状**：17 个 `_*.py` 混杂在仓库根，与 `tests/test_kline_chart.py`、`test_candidate_chart.py` 有依赖。
**目标**：归档到 `scripts/`（个人工作流）或 `tools/`（产品化工具），根目录只留产品代码。

**修订步骤**：
1. 逐文件判定归属：
   - **看板/K线/行情类**（`_kline_chart.py`、`_candidate_chart.py`、`_market_summary_demo.py`、`_memorize_*`、`_morning_score.py`、`_short_term_pick.py`、`_watchlist_*`、`_query_stock_demo.py`）→ `scripts/market/`；
   - **记忆/语音/论文类**（`_memorize_*`、`_read_and_memorize.py`、`_record_watchlist.py`、`_aris_speak.py`、`_latest_llm_papers.py`、`_search_papers_demo.py`、`_psi_probe.py`）→ `scripts/ops/`。
2. `git mv` 归档后，修复内部相对路径引用（这些脚本多依赖 `watchlist_kline_store.py`、`aris_brain/` 的 `sys.path` 注入——把注入路径改为相对脚本位置的解析）。
3. 同步迁移 `tests/test_kline_chart.py`、`test_candidate_chart.py` 的 import 路径（或按 R4 决策一并归档）。
4. `.gitignore:59-62`：`_gen_*.py`/`_build_*.py` 模式保留（归档后根目录不再有 `_*.py` 冲突）。
5. 根目录散落的非 `_` 文件（`watchlist_kline_store.py`、`aris_chat.py`、`aris_mode.py`、`aris_voice.py`）→ 判定产品化后移入 `laap/` 或 `aris_brain/`，否则一并进 `scripts/`。

**验证**：`git status` 干净；迁移后脚本冒烟（如 `python scripts/market/_morning_score.py --help`）；`pytest tests -q` 失败数不增。

**回归风险**：中（脚本间相对路径依赖）。建议用 `git mv` 保留历史，且归档与测试迁移同 PR。

**工作量**：0.5–1 人日。

---

### R15 包元数据补全（对应 ISSUE-016）

**现状**：`pyproject.toml` 无 urls/classifiers/作者邮箱；license 字段格式过时。
**目标**：PEP 621/639 合规，PyPI 展示完整。

**修订步骤**（`pyproject.toml`）：
1. `[project]` 增加：
   ```toml
   authors = [{ name = "Lorry Jovens", email = "<维护者邮箱>" }]
   classifiers = [
     "Programming Language :: Python :: 3",
     "Programming Language :: Python :: 3.11",
     "Programming Language :: Python :: 3.12",
     "Programming Language :: Python :: 3.13",
     "License :: OSI Approved :: Apache Software License",
     "Topic :: Scientific/Engineering :: Artificial Intelligence",
   ]
   [project.urls]
   Homepage = "https://laap-agi.netlify.app"
   Repository = "https://github.com/fssl168/laap-AGI"
   Documentation = "https://github.com/fssl168/laap-AGI/blob/main/README.md"
   ```
2. license 字段按 R2 方案 A 改为 `license = "Apache-2.0"` + `license-files` 说明（含 `LICENSING.md`、`LICENSE.BSL`）。
3. `keywords`、`requires-python`（已有 `>=3.11,<3.14`，保留）。

**验证**：`python -m build` 无警告；`pip show laap` 输出 urls/classifiers。

**回归风险**：低。

**工作量**：0.25 人日。

---

## 3. 修订项依赖与批次合并建议

| 批次 | 修订项 | 合并理由 |
|---|---|---|
| Batch 1 | R3 + R8 | 端口与入口强耦合，一次 PR 收敛 |
| Batch 2 | R1（独立） | 部署阻断 |
| Batch 3 | R4 + R5 + R15 | 质量基线（先依赖后测试） |
| Batch 4 | R7 + R9 | 安全主题 |
| Batch 5 | R6 + R13 + R14 | 文档与整洁 |
| Batch 6 | R10 | 测试集中 |
| Batch 7 | R11（逐文件）| 巨型文件拆分 |
| Batch 8 | R12（分领域）| 重叠收敛 |
| Batch 9 | R2 | 合规（权利人确认后随时可插队） |

## 4. 提交前检查清单（Checklist）

- [ ] `python -m pytest tests -q` 失败数 ≤ 修订前基线（当前 20，目标逐批下降至 0）
- [ ] `git status --porcelain` 仅包含本批预期文件
- [ ] 无新 `_wx_*` / token / secret 文件出现在工作树
- [ ] 改动的模块导入面零破坏：`grep -rn "<改动模块>" --include="*.py" .` 全部可解析
- [ ] 涉及端口/绑定/密钥的改动已同步 README 与 `.env.example`
- [ ] commit message 符合仓库风格：`fix:` / `chore:` / `docs:` / `security:` + 中文描述

## 5. 附录：关键函数锚点索引

| 文件 | 函数/类 | 行号 |
|---|---|---|
| `laap_brain/api.py` | `process_with_laap()` | 172 |
| `laap_brain/api.py` | `handle_chat_completions()` | 359 |
| `laap_brain/api.py` | `handle_recall_memory()` | 571 |
| `laap_brain/api.py` | `handle_rsi_status/improve/full_cycle()` | 750 / 766 / 796 |
| `laap_brain/api.py` | `create_app()` | 839 |
| `laap_brain/api.py` | `main()`（端口 L861-865、绑定 L869） | 860 |
| `aris_brain/laap_brain_api.py` | `process_with_laap()` | 105 |
| `aris_brain/laap_brain_api.py` | `main()` | 600 |
| `laap_brain/config.py` | `setup_dirs()` / `reload_config()` | 120 / 133 |
| `laap_brain/config.py` | `AO_PORT` 常量 | 85 |
| `laap/agi/core.py` | `AGIAgent` | 100 |
| `pyproject.toml` | license / dependencies / scripts / packages.find / pytest | 12 / 13-22 / 28-30 / 33-39 / 44-46 |
| `docker-compose.yml` | 端口映射 / 网络 / LAAP_API_BASE | 41,93 / 112,121 / 96 |
| `Dockerfile` | 依赖安装 / ENV / CMD | 30 / 57 / 65 |
| `VERSIONS.yaml` | integration_points | 80-102 |
| `.gitignore` | 临时文件段 / 语义记忆 JSON | 59-62 / 78 |
| `tests/test_memorize_market.py` | `@pytest.mark.network` | 72 |
| `tests/test_record_watchlist.py` | `@pytest.mark.network` | 71 |
