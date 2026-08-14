# True RSI 可行性评估与实现方案（文件+函数级）

> **评估日期**: 2026-08
> **评估对象**: `D:\laap-AGI` 的递归自我改进（RSI）体系
> **结论摘要**: 代码级进化引擎已存在且接入 `core.py`，但**闭环调度、硬隔离沙箱、适应度函数、治理审计四块缺失**；按 M1→M4 四阶段补齐后，True RSI 可达"能改进业务代码、永不改进安全代码"的受限形态。
> **关联文档**: `docs/laap-alignment-gap-analysis.md`（GAP-N 指出 code_evolution 等 7 模块零测试覆盖）

---

## 1. 现状盘点（代码实证）

### 1.1 已有组件与函数级清单

| 组件 | 文件 | 关键函数 | 状态 |
|---|---|---|---|
| 参数级 RSI | `laap/agi/rsi_engine.py` (846 行) | `RSIMetaEngine.suggest_improvements` / `apply_improvement` / `evaluate_improvement` / `full_improvement_cycle` | ✅ 运行中（参数自调优） |
| 进化编排 | `laap/agi/evolution_system.py` (265 行) | `EvolutionSystem.generate_proposal` / `evaluate_proposal` / `deploy_proposal` / `rollback` / `current_fitness` | ✅ 已接线 core |
| 代码进化 | `laap/agi/code_evolution.py` (986 行) | 见下表 | ✅ 已接线 core |
| 接线点 | `laap/agi/core.py` | L173 `EvolutionSystem`、L185 `CodeEvolutionEngine` | ✅ 已实例化 |

### 1.2 `code_evolution.py` 函数级清单（986 行）

| 类/函数 | 行 | 作用 | 评估 |
|---|---|---|---|
| `MutationType` (enum) | 51 | OPTIMIZE/REFACTOR/FIX_BUG/ADD_FEATURE/REMOVE_DEAD/IMPROVE_LOGGING/HARDEN_ERROR | ✅ |
| `MutationStatus` (enum) | 62 | DRAFT→ANALYZED→PATCHED→TESTING→TEST_PASSED→DEPLOYED/ROLLED_BACK/REJECTED | ✅ |
| `CodeTarget` (dataclass) | 75 | 目标定位（文件/函数/复杂度/当前代码/优化提示） | ✅ |
| `CodeMutation` (dataclass) | 89 | 变更提案（diff/test_results/fitness/风险分） | ✅ |
| `SafetyGuard.validate_mutation` | 133 | 黑名单目录 + 危险模式 + 30% 变更上限 + AST 语法校验 | ⚠️ 软隔离（见坎 1） |
| `CodeAnalyzer.__init__` | 195 | 初始化分析器 | ✅ |
| `CodeAnalyzer.scan_directory` | 200 | 扫目录找目标 | ✅ |
| `CodeAnalyzer.analyze_file` | 251 | 单文件分析 | ✅ |
| `CodeAnalyzer._analyze_function` | 285 | AST 分析函数 | ✅ |
| `CodeAnalyzer._cyclomatic_complexity` | 318 | 圈复杂度 | ✅ |
| `CodeAnalyzer._has_nested_loops` | 329 | 嵌套循环检测 | ✅ |
| `CodeAnalyzer._has_repeated_code` | 338 | 重复代码检测 | ✅ |
| `PatchGenerator.__init__` | 362 | `llm_generate_fn` 可选注入 | ✅ |
| `PatchGenerator.generate_patch` | 366 | 生成补丁 | ✅ |
| `PatchGenerator._rule_based_patch` | 411 | 规则化补丁（无 LLM 兜底） | ✅ |
| `PatchGenerator._generate_diff` | 471 | 生成 unified diff | ✅ |
| `SandboxTester.__init__` | 498 | `timeout=30, max_memory_mb=512` | ⚠️ 参数存在但未真实施加 |
| `SandboxTester.test_mutation` | 503 | 临时目录 + 应用补丁 + 测试 | ⚠️ 非真沙箱 |
| `SandboxTester._run_tests` | 559 | `subprocess.run(shell=True)` | 🔴 **shell=True 命令注入漏洞** |
| `SandboxTester._quick_validate` | 592 | 语法 + 导入校验 | ✅ |
| `GitIntegrator.__init__` | 642 | git 可用性检查 | ✅ |
| `GitIntegrator.deploy` | 652 | SafeRollback 快照 → 定向替换 → 分支提交 | ⚠️ 提交到 feature 分支但未合并 main |
| `GitIntegrator.rollback` | 739 | git revert + checkout main | ⚠️ 依赖 git revert 语义 |
| `GitIntegrator._manual_rollback` | 768 | 还原 original_code | ✅ |
| `GitIntegrator._branch_exists` | 781 | 分支探测 | ✅ |
| `CodeEvolutionEngine.__init__` | 806 | 组装 analyzer/patcher/tester/git | ✅ |
| `CodeEvolutionEngine.scan_targets` | 822 | 扫目标 | ✅ |
| `CodeEvolutionEngine.auto_improve` | 828 | **完整进化循环**（扫→补丁→测→比较→部署） | ✅ 但无调度器调用 |
| `CodeEvolutionEngine._improve_single` | 870 | 单目标进化（含 QA 质量门） | ✅ |
| `CodeEvolutionEngine.rollback_last` | 939 | 回滚最近一次 | ✅ |
| `CodeEvolutionEngine.stats` | 954 | 统计 | ✅ |
| `integrate_code_evolution` | 970 | 装配到 agent | ✅ |

### 1.3 接线现状（core.py）

```python
# core.py L173-188
self.evolution = EvolutionSystem(name=f"{self.name}-evo")
self.code_evolution = CodeEvolutionEngine(
    repo_root=_root,
    llm_fn=getattr(self.hermes, 'llm_generate_patch_for_target', None),
)
```

**关键事实**：`CodeEvolutionEngine` 已实例化、已注入 LLM 补丁函数，但**没有任何地方周期性调用 `auto_improve()`**——引擎是"装好了没启动"。

---

## 2. 可行性判断与四个坎

### 坎 1：沙箱是软隔离（🔴 最高风险）

`SandboxTester._run_tests`（`code_evolution.py:559`）用 `subprocess.run(cmd, shell=True)`，而 `SafetyGuard.BLACKLIST_PATTERNS` 只检查 `mutated_code` 字符串，**不检查 `test_commands`**。一个 mutation 可配合恶意 test_command 逃逸沙箱。

`max_memory_mb=512` 是**声明参数，未实际施加**（无 `resource.setrlimit` 或等价调用）。

### 坎 2：无闭环调度器

`auto_improve()` 存在但无调用者。需一个定时/事件驱动的调度循环。

### 坎 3：适应度函数未定义

`EvolutionSystem.current_fitness()` 返回历史最后值或默认 `0.5`，**无"更好"的度量标准**。代码 RSI 需要可测量的适应度信号。

### 坎 4：递归悖论（元问题）

`SafetyGuard` 和 `SandboxTester` 本身也是可修改代码。一旦进化引擎能改它们，安全假设坍塌。

---

## 3. 实现方案（四阶段，文件+函数级）

### M1 — 硬隔离沙箱（0.5 人日）【先行】

**目标**：把软隔离升级为硬隔离，消除 shell 注入与资源逃逸。

#### 修改文件：`laap/agi/code_evolution.py`

| 函数 | 改动 |
|---|---|
| `SandboxTester.__init__` (L498) | 增加 `restrict_resources: bool = True` 参数；初始化 `self.test_commands_whitelist` |
| `SandboxTester._run_tests` (L559) | 🔴 **`shell=True` → `shell=False`**（改用 `shlex.split(cmd)` 列表参数）；增加测试命令白名单校验（仅允许 `pytest`/`python -m pytest`/`python -m unittest` 固定前缀） |
| `SandboxTester._quick_validate` (L592) | 增加受限导入检查（`import` 白名单：仅 stdlib + laap 包） |
| `SandboxTester.test_mutation` (L503) | 沙箱目录增加只读挂载语义（源文件复制后 chmod 只读）；施加内存/CPU 限制 |

#### 新增函数

| 新增 | 作用 |
|---|---|
| `SandboxTester._apply_limits()` | 用 `resource.setrlimit`（POSIX）或 `psutil` 子进程限制（Windows）真正施加 `max_memory_mb`/超时 |
| `SandboxTester._validate_test_command(cmd)` | 白名单校验，拒绝任意 shell 命令 |
| `SandboxTester._audit_log(entry)` | 记录每次测试的完整参数/结果到审计日志 |

#### 修改文件：`laap/agi/code_evolution.py` — `SafetyGuard`

| 函数 | 改动 |
|---|---|
| `SafetyGuard.validate_mutation` (L133) | 增加"永久只读清单"校验：`SafetyGuard`/`SandboxTester` 自身文件（`code_evolution.py` 的 L113-633 段）禁止作为 mutation target |
| `SafetyGuard` 类常量 (L123) | `BLACKLIST_DIRS` 增加 `"laap/agi/code_evolution.py"` 自保护；`BLACKLIST_PATTERNS` 增加 `subprocess.*shell=True` 检测 |

### M2 — 闭环调度 + 适应度函数（1 人日）

**目标**：让引擎"跑起来"，并定义"更好"的度量。

#### 新增文件：`laap/agi/evolution_scheduler.py`

| 新增函数 | 作用 |
|---|---|
| `class EvolutionScheduler` | 周期调度器 |
| `EvolutionScheduler.__init__(engine, interval_seconds, fitness_fn)` | 注入引擎、周期、适应度函数 |
| `EvolutionScheduler.start()` / `stop()` | 后台线程启停（`threading.Thread(daemon=True)`） |
| `EvolutionScheduler._tick()` | 每周期：`scan_targets → auto_improve(max_mutations=1, auto_deploy=False)` |
| `EvolutionScheduler._compute_fitness()` | 组合适应度：测试通过率(0.4) + 平均响应延迟(0.3) + 记忆召回命中率(0.3) |

#### 新增文件：`laap/agi/fitness.py`

| 新增函数 | 作用 |
|---|---|
| `class FitnessEvaluator` | 适应度评估器 |
| `FitnessEvaluator.test_pass_rate()` | 运行 `pytest tests -q`，解析 passed/total |
| `FitnessEvaluator.avg_latency_ms()` | 从最近 N 次 `process_with_laap` 采样延迟 |
| `FitnessEvaluator.memory_recall_hit_rate()` | 抽样召回记忆，统计命中率 |
| `FitnessEvaluator.composite()` | 加权合成 `[0,1]` 分数 |

#### 修改文件：`laap/agi/core.py`

| 位置 | 改动 |
|---|---|
| L185 附近 | `CodeEvolutionEngine` 实例化后，创建 `EvolutionScheduler` 并 `start()`（默认关闭，`LAAP_EVO_ENABLED=1` 才启动） |

### M3 — 治理与可观测（0.5 人日）

**目标**：所有变更可审计、可回滚、需授权。

#### 新增文件：`laap/agi/evolution_audit.py`

| 新增函数 | 作用 |
|---|---|
| `class EvolutionAuditLog` | 审计日志 |
| `EvolutionAuditLog.record(mutation, decision, reason)` | 追加 JSON 记录（diff、测试结果、部署/回滚、时间戳）到 `state/evolution_audit.jsonl` |
| `EvolutionAuditLog.query(limit)` / `stats()` | 查询与统计 |
| `EvolutionAuditLog.cooldown_check(target)` | 冷却期校验（同一目标 N 小时内不重复改） |

#### 修改文件：`laap/agi/code_evolution.py`

| 函数 | 改动 |
|---|---|
| `CodeEvolutionEngine._improve_single` (L870) | 部署前：① 写审计日志；② 检查冷却期；③ `auto_deploy=True` 时要求外部授权（复用规则引擎的 pending 授权语义） |

#### 修改文件：`laap_brain/api.py`

| 位置 | 改动 |
|---|---|
| L873-875 rsi 路由附近 | 新增 `POST /v1/evo/deploy`（人工批准部署）、`GET /v1/evo/audit`（查审计）、`POST /v1/evo/rollback` |

### M4 — 受限递归（长期，谨慎）

**目标**：允许进化引擎改进"业务代码"，但**永久禁止**改进"安全代码"。

| 关键决策 | 说明 |
|---|---|
| SafetyGuard/SandboxTester 永久只读 | 从 M1 起固化，M4 也不放开 |
| 递归深度 ≤ 1 | 只允许"改进改进者"一层，禁止改进"改进者的改进者" |
| 范围限定 | 只优化 `laap/agi/` 下非核心、非安全文件 |

---

## 4. 实施顺序与验证清单

| 阶段 | 交付物 | 验证方式 |
|---|---|---|
| M1 | 硬沙箱 + 自保护 SafetyGuard | 单测：`tests/test_code_evolution.py`（新增）断言 `shell=False`、恶意 command 被拒、自保护文件不可作为 target |
| M2 | Scheduler + Fitness | 单测：`tests/test_evolution_scheduler.py`（新增）断言 tick 产出 mutation、fitness 在 [0,1] |
| M3 | Audit + 授权 API | 单测：`tests/test_evolution_audit.py`（新增）断言 JSONL 追加、冷却期生效 |
| M4 | 受限递归 | 集成测试 + 人工评审 |

**回归纪律**：每阶段后 `pytest tests -q` 失败数不增（当前基线 277 passed）。

---

## 5. 风险与最终判断

| 维度 | 判断 |
|---|---|
| 技术可行性 | **高**——80% 基础设施已存在（code_evolution.py 已接线 core） |
| 安全可行性 | **当前低**——沙箱软隔离，必须先 M1 |
| 现实收益 | **中**——参数 RSI 已覆盖大部分收益，代码 RSI 边际递减 |
| 推荐路径 | **先上锁（M1+M3）再通电（M2）**；M4 谨慎、受限、可放弃 |

> **一句话结论**：项目离 True RSI 比它自己声称的近得多——引擎已接好线，只差"通电"和"上锁"。但一个没有硬隔离的自修改系统不是进化，是风险。**先 M1 硬沙箱 + M3 审计治理，比实现"能改自己代码"重要一个数量级。**

---

## 6. 实施进度（2026-08 已实现 M1–M3）

| 阶段 | 状态 | commit | 交付物 | 测试 |
|---|---|---|---|---|
| M1 硬隔离沙箱 | ✅ 完成 | `640fd87` | SandboxTester shell=False + 命令白名单 + shell 元字符拒绝 + 资源限制 + 审计日志；SafetyGuard PROTECTED_FILES 自保护 + shell=True 注入拦截 | +28 项单测 |
| M2 闭环调度 | ✅ 完成 | `9fa88c4` | evolution_scheduler.py（周期 tick，daemon 线程，LAAP_EVO_ENABLED=1 开启）+ fitness.py（组合适应度：测试通过率 0.4/延迟 0.3/记忆召回 0.3）+ core.py 接线 | +8 项单测 |
| M3 治理审计 | ✅ 完成 | `ed37ea5` | evolution_audit.py（JSONL 审计 + 冷却期）+ code_evolution 全决策点接入 + API `/v1/evo/audit` `/v1/evo/status` `/v1/evo/rollback` | +9 项单测 |
| M4 受限递归 | ✅ 完成 | `90e7707` | TrueRSIEngine（laap/evolution/true_rsi.py）：作用域限定（仅 laap/agi/ 非核心非安全）+ 递归深度≤1 + 安全基座/核心永久只读 + 守卫注入 CodeEvolutionEngine | +24 项集成测试 |

**测试基线**: 277 → **322 passed / 0 failed**（M1+M2+M3 共 +45 项单测）；M4 后 **370 passed / 0 failed**（+24 项集成测试，`tests/test_true_rsi.py`）。

**启用方式**: `LAAP_EVO_ENABLED=1` 环境变量开启进化调度器（默认关闭）；
`LAAP_EVO_INTERVAL=3600` 控制 tick 周期；审计日志在 `state/evolution_audit.jsonl`；
部署仍默认不自动（`auto_deploy=False`），可通过 `/v1/evo/rollback` 人工回滚。

---

## 6.1 审计补强（2026-08-14 复核报告落地）

按 §3 方案复核后补齐实施差距，测试基线 322 → **346 passed / 0 failed**（+19 项治理单测，另 5 项 EVO API 测试默认跳过）：

| 缺口 | 补强内容 | 落地位置 |
|---|---|---|
| M3: `POST /v1/evo/deploy` 缺失 | 新增人工批准部署 API：`{"mutation_id": "<id>", "approver": "<可选>"}` → 批准落审计 → 部署 → 落 deployed 审计；未知 id 409/not_found、非 test_passed 状态 409/not_approvable、失败 409/deploy_failed | `laap_brain/api.py` `handle_evo_deploy` + 路由注册；`code_evolution.py` `approve_and_deploy` |
| M3: `auto_deploy` 无授权检查 | `_improve_single` 中 `auto_deploy=True` 但 `mutation.approved=False` → 返回 `awaiting_approval` 并写审计，不部署 | `code_evolution.py` Step 5 授权门 |
| M3: evo 端点各自新建引擎 | 引擎单例 `_get_code_evolution_engine()`：优先复用调度器持有的引擎，保证 mutations 历史跨 `/v1/evo/*` 可见（原实现每次新建引擎 → rollback/deploy 永远找不到历史 mutation） | `laap_brain/api.py` |
| M1: `_quick_validate` 无 import 白名单 | 新增 `_validate_imports`：AST walk 校验 import，仅允许 stdlib + `laap` 前缀，拒绝相对导入/任意第三方包（`import pandas`、`from . import x` 均拦截） | `code_evolution.py` `_validate_imports` + `_quick_validate` 接入 |
| M1: 沙箱无只读语义 | `test_mutation` 写入 mutated code 后对沙箱内文件 `chmod 0o444`（测试子进程不能改写被测试文件） | `code_evolution.py` `test_mutation` |
| M1: `restrict_resources` 参数缺失 | `SandboxTester.__init__` 增加 `restrict_resources: bool = True`；`_apply_limits` 尊重该开关（False 时跳过，测试/降级用） | `code_evolution.py` `SandboxTester` |
| 顺带修复 | `code_evolution.py` 3 处 `class _Dumm<LOCAL_PATH_REDACTED>` 损坏字符串（工具脱敏误写盘）→ 恢复合法 `class _Dummy:`/`_SandboxTest:` 伪类包装 | `code_evolution.py` L186/L558/L749 |

**新增测试**: `tests/test_evo_deploy_governance.py`（19 项）— 授权检查、approve_and_deploy 全分支（成功/未知 id/错误状态/部署失败）、import 白名单（stdlib/laap 放行、第三方/相对/函数体内拦截）、沙箱只读、`/v1/evo/deploy` 路由注册与 handler 校验。

**运维要点**: 服务进程重启后 mutations 历史清空（内存态），`approve_and_deploy` 只能批准**本次进程内**产生的 mutation；跨进程的审计追溯走 `state/evolution_audit.jsonl` 只读查询。调度器启用时引擎单例与 `/v1/evo/*` 共用，tick 产生的 `awaiting_approval` 提案可直接用 deploy API 批准。

---

## 6.2 M4 受限递归实施（2026-08-15 commit `90e7707`）

按 §3 M4 方案落地，测试基线 346 → **370 passed / 0 failed**（+24 项集成测试，`tests/test_true_rsi.py`）。

| 交付物 | 说明 |
|---|---|
| `laap/evolution/true_rsi.py` | `TrueRSIEngine` 受限递归编排层，复用 M1-M3 的 analyzer/patcher/tester/audit/git |
| `laap/evolution/__init__.py` | M4 包入口（不恢复已废弃的 `laap/evolution/rsi.py`/`aevo`） |
| 守卫注入 | `CodeEvolutionEngine.scope_guard` 钩子（默认 None，M1-M3 行为不变）+ `auto_improve`/`_improve_single` 增加 `depth` 参数 |
| `tests/test_true_rsi.py` | 24 项集成测试 |

**四道约束落地**：

1. **作用域限定** — 仅允许 `laap/agi/` 下非核心、非安全文件；`laap_brain/`、`psi_core/`、`aris_brain/` 等一律拒绝。
2. **永久只读** — `PROTECTED_SAFETY`（code_evolution / evolution_system / rsi_engine / evolution_scheduler / evolution_audit / fitness）+ `PROTECTED_CORE`（core / safety / security_system / `__init__`）在任何递归深度均不可作为 target；目录穿越试探（`laap/agi/../rsi_engine.py`）也会被 base-name 命中只读清单拦截。
3. **递归深度 ≤ 1** — 唯一可递归目标 `laap/evolution/true_rsi.py`（改进者自身）；深度 0 允许第一层递归，改进测试通过后递归配额耗尽，深度 1 再改被拒（禁止"改进者的改进者"）；改进失败不消耗配额。
4. **不自动部署** — `improve()` 恒以 `auto_deploy=False` 产出提案，走 `/v1/evo/deploy` 人工批准；批准部署 `true_rsi.py` 提案时同步消耗递归配额。

**验证**（`tests/test_true_rsi.py` 24 项）：永久只读 10 项、作用域外 4 项（含目录穿越）、业务代码放行 1 项、递归深度 4 项、治理审计 2 项、接线回归 2 项、stats 1 项。守卫异常按 fail-closed 拒绝处理（守卫自身抛错 → mutation 被拒，不落入补丁生成）。
