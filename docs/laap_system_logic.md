# LAAP 系统逻辑图

## Living Agent Application Protocol (LAAP) — 架构总览

> LAAP = Living Agent Application Protocol,代号 **Aris**(爱丽丝)。
> 一个运行在 `D:\laap-AGI` 的本地认知引擎,以「数字生命体」形态与用户交互,
> 同时充当用户的**个人记忆库**与**量化交易子系统**。

---

## 1. 系统分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                     接入层 (Interface)                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ Hermes   │  │  OpenAI  │  │  MCP     │  │ 微信/QQ  │    │
│  │ MCP 挂载 │  │ 兼容 API │  │ Wrapper  │  │ 语音桥   │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
├─────────────────────────────────────────────────────────────┤
│                     服务层 (API Layer)                       │
│   laap_brain/api.py  ←→  aris_brain/laap_brain_api.py       │
│   (OpenAI 兼容: /v1/chat /v1/reflect /v1/recall_memory ...)  │
│   Port: 11546 (127.0.0.1, 安全加固: 限流/输入上限/错误脱敏)    │
├─────────────────────────────────────────────────────────────┤
│                   认知层 (Cognitive Layer)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ 规则引擎  │ │ 工具路由  │ │ PSI 意识 │ │ 认知总线  │       │
│  │ RulesEngine│ │ToolRouter │ │ 桥接器   │ │Cognitive │       │
│  │ aris_rules│ │laap/agi/ │ │ psi_jspace│ │  Bus     │       │
│  │ _engine.py│ │tool_router│ │ _bridge  │ │          │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
├─────────────────────────────────────────────────────────────┤
│                   引擎层 (Engine Layer)                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐    │
│  │ V12.5 量子引擎│ │ 潜意识引擎    │ │ LLM 兜底          │    │
│  │ ArisV12Engine│ │ QuantumSub-  │ │ Agnes-2.5-flash  │    │
│  │ +MarkovChain │ │ conscious    │ │ (cpk- key)       │    │
│  │ V12 (语义核)  │ │ (5s 后台线程) │ │                  │    │
│  └──────────────┘ └──────────────┘ └──────────────────┘    │
├─────────────────────────────────────────────────────────────┤
│                   记忆层 (Memory Layer)                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │
│  │ 语义记忆      │ │ PSI 状态      │ │ 演化审计       │        │
│  │ laap_semantic│ │ psi_state    │ │ evolution_   │        │
│  │ _memory.json │ │ .json        │ │ audit.jsonl  │        │
│  │ (bge-small-zh│ │ (需求/情感/   │ │ (True RSI    │        │
│  │  512维嵌入)  │ │  唤醒/循环)   │ │  M1-M3)      │        │
│  └──────────────┘ └──────────────┘ └──────────────┘        │
├─────────────────────────────────────────────────────────────┤
│                   子系统层 (Subsystems)                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │
│  │ paper_trading│ │ 行情采集      │ │ DSA 集成      │        │
│  │ (量化闭环)    │ │ (K线/大盘)    │ │ (18 工具路由) │        │
│  └──────────────┘ └──────────────┘ └──────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 请求处理流程 (Request Flow)

```
用户消息
   │
   ▼
┌───────────────────────────────────────────────┐
│ 1. 认知桥 Cognitive Bridge                     │
│    - 识别意图 / 上下文 / 用户画像               │
└───────────────────────────────────────────────┘
   │
   ▼
┌───────────────────────────────────────────────┐
│ 2. 规则引擎 RulesEngine                        │
│    - 任务类触发词匹配                           │
│    - check_status / feeling_rule / self_intro  │
│    - recall_fact / remember_fact / ...         │
└───────────────────────────────────────────────┘
   │ 命中 (engine: rules:*)
   │                    │ 未命中
   ▼                    ▼
┌───────────────┐  ┌────────────────────────────┐
│ 执行工具 →     │  │ 3. 工具路由 ToolRouter      │
│ 返回结果       │  │    - tools 参数匹配         │
└───────────────┘  │    - DSA 18 工具 / 搜索 /    │
                   │      记忆读写                │
                   └────────────────────────────┘
                   │ 命中 (engine: agi:tool_router)
                   │                    │ 未命中
                   ▼                    ▼
              ┌─────────────────────────────────┐
              │ 4. LLM 兜底 LongForm             │
              │    - Agnes-2.5-flash 开放话题     │
              │    (engine: llm:*)              │
              └─────────────────────────────────┘
                   │
                   ▼
              ┌─────────────────────────────────┐
              │ 5. 记忆写入 + PSI 更新            │
              │    - reflect 语义记忆             │
              │    - cognitive_state 推进循环     │
              └─────────────────────────────────┘
```

---

## 3. 核心引擎详解

### 3.1 规则引擎 (RulesEngine)

| 规则 | 触发词 | 输出 |
|---|---|---|
| `check_status` | 状态/你在干嘛/health | 状态叙述(循环/情感/需求) |
| `feeling_rule` | 感受/心情/how do you feel | 口语化感受回答 |
| `self_intro_rule` | 你是谁/自我介绍 | 人格叙述(适合TTS) |
| `my_journey_rule` | 你的历程/最近发生什么 | 记忆+PSI 统计回顾 |
| `remember_fact_rule` | 记住/记下来 | 写入语义记忆 |
| `recall_fact_rule` | 记得/回忆/跟你说过 | 语义召回 |

### 3.2 V12.5 量子引擎

```
ArisV12Engine (语义核)
├── 精确匹配 (87 条响应库)
├── V12DenseKernel 量子核语义匹配
│   └── 预编码稠密向量 → 余弦相似度 ≥ 0.30
├── 字符重叠回退
└── 语言回退

MarkovChainV12 (潜意识生成)
└── 6话题×15句语料 → 双字滑窗分词
    → 词转移矩阵 → 温度采样 (coherence 0.77-0.89)
```

### 3.3 潜意识引擎 (QuantumSubconscious)

- 后台 daemon 线程,间隔 **5 秒**
- 自动加载 V12.5 引擎(`_init_engine`)
- 每 tick 生成直觉 → 写入状态
- **必须 `start_background()` 才运行**(仅 `load_all()` 不启动线程)

### 3.4 PSI 意识空间

```
psi_state.json 状态维度:
├── needs: competence / certainty / relatedness
├── valence (情绪效价) / arousal (唤醒度)
├── attention_focus: task | explore | social
├── cognitive_cycle (对话循环, 每轮 +1)
└── energy (能量, /10)
```

---

## 4. 记忆系统

```
laap_semantic_memory.json
├── memories[]: {id, text, timestamp, embedding, meta}
├── 嵌入: BAAI/bge-small-zh (512维, 本地)
├── 检索: 余弦相似度 + 时间权重
│         (今天+0.10 / 昨天+0.06 / 前天+0.03)
└── 写入要求: write → recall 验证闭环
```

---

## 5. 子系统: paper_trading 量化闭环

```
行情数据 (腾讯fqkline / akshare 800天)
   │
   ▼
策略引擎 (14维多因子: 趋势/RSI/ATR仓位/止盈止损/量能)
   │
   ▼
BacktestRunner (多因子回测)
   │
   ▼
param_evolver (网格→随机→遗传, seed固定可复现)
   │
   ▼
OOS 门禁 (60/20/20 切分 + walk-forward)
   │
   ▼
决策留痕 (/v1/quant/decisions) → 交易执行
```

⚠️ 诚实结论: 真实 A 股 OOS 回测通过率 10-20%,**无泛化 alpha**——
论文定位为「受控模拟环境功能验证」, 真实数据不入论文。

---

## 6. 安全加固基线

| 项 | 措施 |
|---|---|
| 网络 | API 默认绑 127.0.0.1(`--host` / `LAAP_HOST`) |
| 限流 | recall limit ≤50 / messages ≤50 / 200K 字符 |
| 错误 | 18 处 `str(e)` → 通用 "internal error" |
| 规则 | 危险命令 subprocess/eval/exec 白名单拦截 (51 测试) |

---

## 7. 关键文件地图

```
D:\laap-AGI\
├── laap_brain/
│   ├── api.py                 # 主 API (OpenAI 兼容)
│   └── integrator.py          # HermesIntegrator
├── aris_brain/
│   ├── laap_brain_api.py      # 实际服务入口 (service_manager 用)
│   ├── laap_integrator.py     # LaapIntegrator (load_all+start_background)
│   ├── aris_rules_engine.py   # 规则引擎
│   ├── aris_v12_5_engine.py   # V12.5 量子引擎
│   ├── aris_subconscious.py   # 潜意识引擎
│   ├── cognitive_bus.py       # 认知总线
│   └── psi_jspace_bridge/     # PSI 意识桥接
├── laap/agi/
│   └── tool_router.py         # 工具路由
├── mcp_server/
│   ├── laap_service_manager.py# 启停管理 (start/stop/status)
│   └── laap_mcp_wrapper.py    # Hermes MCP 挂载
├── scripts/
│   ├── ops/                   # ARIS 运维脚本
│   └── market/                # 行情脚本
├── data/
│   ├── paper_trading.db       # 量化数据
│   └── watchlist_kline/       # 自选股K线
└── .env                       # LAAP_PORT / DEEPSEEK_API_KEY
```

---

## 8. 服务生命周期

```
Hermes 启动
   │
   ▼
MCP wrapper (laap_mcp_wrapper.py)
   │ import 时触发
   ▼
laap_service_manager.py start
   │ (health 幂等: 在跑就跳过)
   ▼
aris_brain/laap_brain_api.py --port 11546
   │ (LAAP venv python, 127.0.0.1)
   ▼
预热 30-60s → engines_loaded: true
   │
   ├── 规则引擎 / 工具路由 / 潜意识线程
   ├── V12.5 引擎 + PSI 桥接
   └── 记忆库就绪
```

⚠️ **生命周期跟随 MCP**: Hermes 退出 → LAAP 被 stop。
若需常驻,去掉 wrapper 的 cleanup stop 逻辑。

---

*本文档由 doc-engine 渲染 · LAAP System Logic Map · 2026-08-16*
