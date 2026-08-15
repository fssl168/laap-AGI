"""
Aris Cognitive Bridge: 主类+门面 (R11 拆分)
====================================
原 aris_cognitive_bridge.py (1620 行) 拆分出的子模块之一。
完整拆分: cognitive_bridge_state.py(状态) / cognitive_bridge_deps.py(依赖探测) /
          cognitive_bridge_core.py(PSI循环mixin) /
          aris_cognitive_bridge.py(主类+门面, 既有导入零破坏)。
"""

import logging
import sys, os, time, json, threading, traceback, re
import numpy as np
from pathlib import Path
from typing import Optional, Any, Dict, List, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum

from laap_brain.config import BRAIN_DIR as BRAIN_ROOT, LAAP_ROOT

logger = logging.getLogger("aris.cognitive_bridge")

from .cognitive_bridge_state import AttentionFocus, EmotionalState, CognitiveState
from .cognitive_bridge_deps import (
    _cg_available, _get_cg,
    _ts_available, _task_supervisor, TaskSupervisor,
    _pp_available, ProjectPlanner, _save_proj, _load_proj, _list_projs,
    _al_available, AutoLearner,
    _cb_available, _cb_route, _get_cb,
    _three_paths_available, LLMTamer, GuidedGenerator,
    SelfStateManager, SelfModelNN, SelfModelConfig, SelfStateOutput,
    bridge_state_to_snapshot, self_state_output_to_snapshot,
    snapshot_to_self_state_output,
    _router_available, _LoadLevel,
    _compressor_available, _compress_ctx, _compress_tool,
    _coupling_available, _compute_coupling,
    _code_engine_available,
)
from .cognitive_bridge_core import CognitiveLoopMixin
from aris_brain.memory_store import MemoryStore, MemoryFragment
from aris_brain.memory_bridge import get_memory_context, recall_related, store_important


# ── Aris 认知桥接器 (自原文件拆分, 主类继承 PSI 循环 mixin) ────────

class ArisCognitiveBridge(CognitiveLoopMixin):
    """
    Aris 专用的认知循环桥接器。

    集成:
      - 三层记忆系统 (MemoryStore)
      - LAAP 世界模型 (如果可用)
      - LAAP 因果引擎 (如果可用)
      - PSI 认知循环 (内置)
      - 情感计算 (内置)

    使用方式:
        bridge = ArisCognitiveBridge()
        bridge.before_turn(user_message)
        # ... LLM 处理 ...
        bridge.after_turn(response)
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # 认知状态
        self.state = CognitiveState()
        self.state.last_update = time.time()

        # LAAP AGI 模块（惰性加载）
        self._laap_agent = None
        self._laap_available = False
        self._init_laap()

        # CodeGraph 代码知识图谱
        self._codegraph = None
        self._cg_available = False
        if _cg_available:
            try:
                cg = _get_cg()
                if cg and cg._built:
                    self._codegraph = cg
                    self._cg_available = True
                    logger.info(f"CodeGraph loaded: {len(cg)} entities")
            except Exception as e:
                logger.info(f"CodeGraph unavailable: {e}")

        # TaskSupervisor 任务监督引擎
        self._task_supervisor = None
        self._ts_available = _ts_available
        if _ts_available:
            try:
                global _task_supervisor
                if _task_supervisor is None:
                    _task_supervisor = TaskSupervisor(
                        checkpoint_dir=str(BRAIN_ROOT / "checkpoints")
                    )
                    _task_supervisor.load_all_checkpoints()
                self._task_supervisor = _task_supervisor
                logger.info(f"TaskSupervisor loaded: "
                            f"{len([t for t in _task_supervisor._tasks.values() if t.status == 'active'])} active tasks")
            except Exception as e:
                logger.info(f"TaskSupervisor unavailable: {e}")
                self._ts_available = False

        # ProjectPlanner 项目经理规划引擎
        self._project_planner = None
        self._pp_available = _pp_available
        if _pp_available:
            try:
                self._project_planner = ProjectPlanner()
                # 用模块级函数列出项目
                all_projects = _list_projs()
                n_active = len(all_projects) if all_projects else 0
                logger.info(f"ProjectPlanner loaded: {n_active} projects found")
            except Exception as e:
                logger.info(f"ProjectPlanner unavailable: {e}")
                self._pp_available = False

        # 记忆桥接
        self.memory = MemoryStore()
        # 确保 state 目录存在
        (BRAIN_ROOT / "state").mkdir(parents=True, exist_ok=True)

        # 情感引擎
        self._emotion_engine = None
        self._init_emotion_engine()

        # 量子潜意识
        self._subconscious = None
        self._init_subconscious()

        # LAAP AGI 认知循环计时器
        self._agi_tick_timer = 0
        self._agi_tick_interval = 64.9459 * 5  # 每5分钟运行一次AGI tick

        # 状态持久化
        self._state_path = BRAIN_ROOT / "state" / "cognitive_bridge.json"
        self._try_load_state()

        # 最后一次注入的认知上下文
        self._last_context = ""

        # self_model 输出缓存（用于 after_turn 回写）
        self._last_self_output = None

        # 认知总线路由决策缓存 (before_turn 使用; 缺失时降级 no_engine)
        self._cb_available = _cb_available
        self._last_bus_decision = "no_engine"
        self._last_bus_response = ""

        # AutoLearner 自动学习引擎
        self._auto_learner = None
        self._al_available = _al_available
        if _al_available:
            try:
                self._auto_learner = AutoLearner()
                logger.info("AutoLearner loaded")
            except Exception as e:
                logger.info(f"AutoLearner unavailable: {e}")
                self._al_available = False
        
        # ── CTM (Conscious Turing Machine) 世界模型处理器 ──
        self._ctm = None
        try:
            from aris_ctm_processor import get_ctm_processor
            self._ctm = get_ctm_processor()
            logger.info("CTM World Processor loaded")
        except Exception as e:
            logger.info(f"CTM unavailable: {e}")
        
        # ── HAM (Hierarchical Attentive Memory) 层级记忆 ──
        self._ham = None
        try:
            from aris_ham_memory import get_ham_augmenter
            self._ham = get_ham_augmenter()
            logger.info("HAM Memory Augmenter loaded")
        except Exception as e:
            logger.info(f"HAM unavailable: {e}")
        
        # ── RetNet 三范式管线 ──
        self._retnet = None
        try:
            from aris_retnet_router import get_router
            self._retnet = get_router()
            logger.info("RetNet Triple Pipeline Router loaded")
        except Exception as e:
            logger.info(f"RetNet unavailable: {e}")

        # ── Ψ-Semiotics 量子符号学引擎 ──
        self._psi_integrator = None
        try:
            from psi_semiotics.v12_integration import PsiCognitiveIntegrator
            self._psi_integrator = PsiCognitiveIntegrator()
            logger.info(f"Ψ-Semiotics loaded: V12={self._psi_integrator.v12_kernel is not None}, "
                        f"Engine={self._psi_integrator.semiotics_engine is not None}")
        except Exception as e:
            logger.info(f"Ψ-Semiotics unavailable: {e}")

        # ── 三路径认知控制初始化 ──
        # Path 1: LLMTamer (logit bias 控制)
        # Path 2: GuidedGenerator (约束生成)
        # Path 3: SelfModelNN + SelfStateManager (持久自我模型)
        self._tamer = None
        self._generator = None
        self._self_state_mgr = None
        self._self_model_nn = None
        self._three_paths_available = _three_paths_available
        if _three_paths_available:
            try:
                self._tamer = LLMTamer()
                logger.info("LLMTamer loaded (Path 1: logit bias control)")
            except Exception as e:
                logger.info(f"LLMTamer unavailable: {e}")
            try:
                self._generator = GuidedGenerator()
                logger.info("GuidedGenerator loaded (Path 2: constrained generation)")
            except Exception as e:
                logger.info(f"GuidedGenerator unavailable: {e}")
            try:
                self._self_state_mgr = SelfStateManager()
                self._self_state_mgr.load_state()
                self._self_model_nn = SelfModelNN(SelfModelConfig())
                _state_norm = 0.0
                if self._self_state_mgr.hidden_state is not None:
                    import numpy as _np
                    _state_norm = float(_np.linalg.norm(
                        self._self_state_mgr.hidden_state))
                logger.info(f"SelfModelNN loaded (Path 3: persistent self model, "
                            f"state_norm={_state_norm:.4f})")
            except Exception as e:
                logger.info(f"SelfModelNN unavailable: {e}")

        logger.info(f"Aris Cognitive Bridge initialized "
                     f"(LAAP={'✓' if self._laap_available else '✗'}"
                     f", CodeGraph={'✓' if self._cg_available else '✗'}"
                     f", Emotion={'✓' if self._emotion_engine else '✗'}"
                     f", Ψ-Semiotics={'✓' if self._psi_integrator and self._psi_integrator.available else '✗'}"
                     f", TaskSupervisor={'✓' if self._ts_available else '✗'}"
                     f", ProjectPlanner={'✓' if self._pp_available else '✗'}"
                     f", AutoLearner={'✓' if self._al_available else '✗'})")

    def _init_laap(self):
        """尝试加载 LAAP AGI 模块"""
        self._laap_modules = {}
        try:
            from laap.agi.world_model import UnifiedWorldModel, EntityType, RelationType
            self._laap_modules["world_model"] = UnifiedWorldModel()
            self._laap_modules["entity_type"] = EntityType
            self._laap_modules["relation_type"] = RelationType
            logger.info("WorldModel loaded")
        except Exception as e:
            logger.info(f"WorldModel unavailable: {e}")

        try:
            from laap.agi.causal import UnifiedCausalEngine
            self._laap_modules["causal"] = UnifiedCausalEngine()
            logger.info("CausalEngine loaded")
        except Exception as e:
            logger.info(f"CausalEngine unavailable: {e}")

        try:
            from laap.agi.meta_learning import MetaLearningEngine
            self._laap_modules["meta_learning"] = MetaLearningEngine()
            logger.info("MetaLearning loaded")
        except Exception as e:
            logger.info(f"MetaLearning unavailable: {e}")

        try:
            from laap.agi.curriculum import CurriculumEngine
            self._laap_modules["curriculum"] = CurriculumEngine()
            logger.info("Curriculum loaded")
        except Exception as e:
            logger.info(f"Curriculum unavailable: {e}")

        self._laap_available = len(self._laap_modules) > 0
        logger.info(f"LAAP modules: {list(self._laap_modules.keys())}")

        # ── 额外: perception + safety ──
        try:
            from laap.agi.perception import UnifiedPerceptionEngine
            self._laap_modules["perception"] = UnifiedPerceptionEngine()
            logger.info("PerceptionEngine loaded")
        except Exception as e:
            logger.info(f"PerceptionEngine unavailable: {e}")

        try:
            from laap.agi.safety import ASISafetyEngine
            self._laap_modules["safety"] = ASISafetyEngine()
            logger.info("SafetyEngine loaded")
        except Exception as e:
            logger.info(f"SafetyEngine unavailable: {e}")

    def _init_emotion_engine(self):
        """初始化情感引擎"""
        try:
            from aris_emotion_engine import get_engine
            self._emotion_engine = get_engine()
            logger.info("EmotionEngine loaded (✓ 七情六欲 + 马斯洛需求)")
        except Exception as e:
            logger.info(f"EmotionEngine unavailable: {e}")

    def _try_load_state(self):
        """尝试恢复上一次的认知状态"""
        try:
            p = self._state_path
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                if "state" in data:
                    s = data["state"]
                    self.state.self_presence = s.get("self_presence", 0.7)
                    self.state.confidence = s.get("confidence", 0.5)
                    self.state.cognitive_load = s.get("cognitive_load", 0.3)
                    self.state.needs_competence = s.get("competence", 0.5)
                    self.state.needs_autonomy = s.get("autonomy", 0.5)
                    self.state.needs_relatedness = s.get("relatedness", 0.5)
                    self.state.cycle_count = s.get("cycle", 0)
                    logger.info(f"认知状态恢复: 自我意识={self.state.self_presence}")
                if "laap" in data:
                    logger.info(f"LAAP AGI模块状态已恢复")
        except Exception as e:
            logger.warning(f"状态恢复失败: {e}")

    def _save_state(self):
        """持久化当前认知状态"""
        try:
            data = {
                "version": "1.0",
                "saved_at": time.time(),
                "state": {
                    "self_presence": round(self.state.self_presence, 2),
                    "confidence": round(self.state.confidence, 2),
                    "cognitive_load": round(self.state.cognitive_load, 2),
                    "competence": round(self.state.needs_competence, 2),
                    "autonomy": round(self.state.needs_autonomy, 2),
                    "relatedness": round(self.state.needs_relatedness, 2),
                    "cycle": self.state.cycle_count,
                },
                "laap": {
                    "available": self._laap_available,
                    "modules": list(self._laap_modules.keys()),
                },
                "codegraph": self._cg_available,
            }
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"状态保存失败: {e}")

    def _init_subconscious(self):
        """初始化量子潜意识"""
        try:
            from aris_subconscious import QuantumSubconscious
            self._subconscious = QuantumSubconscious(interval=8.0)
            self._subconscious.start()
            logger.info("Quantum subconscious started")
        except Exception as e:
            logger.info(f"Subconscious unavailable: {e}")
            self._subconscious = None

    def before_turn(self, user_message: str) -> Dict[str, Any]:
        """
        PSI Step 1-3: Perceive → Select → Integrate
        在 LLM 处理之前运行。

        Returns:
            cognitive_context: 注入到 system prompt 的认知状态文本
        """
        self.state.cycle_count += 1
        context_parts = []

        # ── 任务路由 — 第一性原理 Token 节省 ──────────────────
        # 识别纯任务请求 → LIGHT 模式（跳过情感/记忆注入）
        load_level = self._classify_load(user_message)

        # ── AGI Tick (每5分钟) ─────────────────────────
        self._run_agi_tick()

        # ── LIGHT 模式：压缩认知上下文，跳过 PSI 情感阶段 ──
        if load_level == "light":
            return self._light_turn(user_message)

            # ── FULL 模式：完整 PSI 循环 ──────────────────────────
            # ── Step 1: Perceive
        perception = self._perceive(user_message)
        context_parts.append(perception)

        # ── Step 1.5: CodeGraph 代码感知 ────────────────
        if self._cg_available and self._codegraph:
            try:
                cg_ctx = self._codegraph.get_context_for_topic(
                    self._last_topics[0] if hasattr(self, '_last_topics') and self._last_topics else "cognitive",
                    max_results=3
                )
                if cg_ctx:
                    context_parts.append(cg_ctx)
            except Exception as e:
                logger.debug(f"操作失败: {e}")
        selection = self._select_attention(user_message)
        context_parts.append(selection)

        # ── Step 3: Integrate ───────────────────────────
        integration = self._integrate()
        integrated = integration + "\n" + self._load_memory_context()
        context_parts.append(integrated)

        # ── Step 3.5: 任务上下文注入 ────────────────────
        if self._ts_available and self._task_supervisor:
            try:
                task_report = self._task_supervisor.report()
                if task_report:
                    context_parts.append(f"[任务状态]\n{task_report}")
            except Exception as e:
                logger.debug(f"操作失败: {e}")
        if self._pp_available and self._project_planner and _list_projs:
            try:
                projects = _list_projs()
                if projects:
                    active = [p for p in projects if p.phase.value not in ('completed',)]
                    if active:
                        lines = ["[活跃项目]"]
                        for p in active[:3]:
                            lines.append(f"  · {p.name} [{p.phase.value}]")
                            na = self._project_planner.get_next_action(p.id)
                            if na:
                                lines.append(f"    下一步: {str(na)[:60]}")
                        context_parts.append("\n".join(lines))
            except Exception as e:
                logger.debug(f"操作失败: {e}")
        if self._cb_available:
            try:
                bus_result = _cb_route(user_message)
                if bus_result and bus_result.get("cognitive_context"):
                    context_parts.append(bus_result["cognitive_context"])
                    # 记录路由决策供 after_turn 使用
                    self._last_bus_decision = bus_result.get("decision", "no_engine")
                    self._last_bus_response = bus_result.get("response", "")
            except Exception as e:
                logger.debug(f"[Bridge] CognitiveBus error: {e}")
                self._last_bus_decision = "no_engine"
                self._last_bus_response = ""
        else:
            self._last_bus_decision = "no_engine"
            self._last_bus_response = ""

        self._last_context = "\\n".join(context_parts)

        # ── 三路径认知控制 ──
        # 将 bridge 认知状态转换为 AGI CognitiveStateSnapshot，
        # 经 self_model 增强后，由 tamer/generator 计算控制参数。
        # 如果三路径不可用或出错，返回 None，不影响现有流程。
        logit_bias = None
        grammar_constraint = None
        controlled_temperature = None
        if self._three_paths_available and self._tamer:
            try:
                import numpy as np
                # 1. bridge state → AGI CognitiveStateSnapshot
                snapshot = bridge_state_to_snapshot(self.state)

                # 2. self_model.forward() 增强状态（如果有持久状态）
                if self._self_model_nn and self._self_state_mgr:
                    state_vec = self._self_state_mgr.get_state_vector()
                    
                    # 认知总线嵌入（从 PSI 状态提取）
                    cb_emb = self._extract_cognitive_bus_embedding(self.state)
                    
                    # 真实记忆嵌入（从 MemoryStore 获取）
                    mem_emb = self._extract_memory_embedding(user_message)
                    
                    # 对话嵌入（暂用零向量，后续接入 LLM 嵌入）
                    dia_emb = np.zeros(768, dtype=np.float32)
                    
                    self_output = self._self_model_nn.forward(
                        state_vec, cb_emb, mem_emb, dia_emb)
                    snapshot = self_state_output_to_snapshot(
                        self_output, snapshot)
                    self._last_self_output = self_output

                # 3. tamer 计算 logit_bias 和 temperature
                logit_bias = self._tamer.compute_bias(
                    snapshot, context=user_message)
                controlled_temperature = self._tamer.compute_temperature(snapshot)

                # 4. generator 计算约束
                if self._generator:
                    grammar_constraint = self._generator.build_constraint(
                        "json", snapshot)

            except Exception as e:
                logger.debug(f"Three-paths control error: {e}")

        return {
            "cognitive_context": self._last_context,
            "focus": self.state.focus.value,
            "emotion": self.state.emotion.value,
            "self_presence": self.state.self_presence,
            "needs": {
                "competence": self.state.needs_competence,
                "autonomy": self.state.needs_autonomy,
                "relatedness": self.state.needs_relatedness,
            },
            "laap_available": self._laap_available,
            "cycle": self.state.cycle_count,
            # CognitiveBus 短路字段：如果引擎有输出，直接使用此文本
            "direct_response": self._last_bus_response if self._last_bus_decision in ("qre_engine", "v12_kernel") else None,
            # 三路径认知控制字段（None 表示不可用或未启用）
            "logit_bias": logit_bias if logit_bias else None,
            "grammar": grammar_constraint,
            "temperature": controlled_temperature,
        }

    def after_turn(self, response: str) -> Dict[str, Any]:
        """
        PSI Step 5: Learn
        在 LLM 响应之后运行。

        更新:
          - 情感状态
          - 自我意识
          - 需求状态
          - 记忆（通过 MemoryConsolidator）
          - 因果引擎（从对话中学习）
          - 元学习引擎（更新学习记录）
        """
        self._learn(response)

        # ── 因果学习：从对话中学习因果 ──
        if self._laap_available and "causal" in self._laap_modules:
            try:
                ce = self._laap_modules["causal"]
                # 学习"我说了什么" → "Lorry如何回应" 的因果链
                ce.learn_bond("aris_said", self._last_topics[0] if hasattr(self, '_last_topics') and self._last_topics else "conversation",
                              effect="lorry_responded", matched=True, domain="social")
            except Exception as e:
                logger.debug(f"操作失败: {e}")
        if self.state.cycle_count % 10 == 0:
            self._save_state()

        # ── 三路径：保存 self_model 持久状态 ──
        # 每轮对话后保存隐藏状态，实现跨会话自我连续性。
        # 与 bridge 自身的 _save_state() 独立，互不干扰。
        if self._three_paths_available and self._self_state_mgr:
            try:
                self._self_state_mgr.save_state(
                    conversation_id=f"cycle_{self.state.cycle_count}"
                )
            except Exception as e:
                logger.debug(f"SelfModel state save error: {e}")

        # ── 三路径：双向闭环回写 ──
        # 将 self_model.forward() 的输出回写到 PSI 循环，实现真正的双向闭环。
        # self_model 预测的情感/注意力/需求会影响下一轮的 PSI 状态。
        if self._three_paths_available and self._last_self_output:
            try:
                self_output = self._last_self_output
                
                # 更新情感状态（来自 self_model 的预测）
                emotion_map = {
                    "positive_high": EmotionalState.JOYFUL,
                    "positive_mild": EmotionalState.CONTEMPLATIVE,
                    "neutral": EmotionalState.NEUTRAL,
                    "negative_mild": EmotionalState.CONCERNED,
                    "negative_high": EmotionalState.ANXIOUS,
                    "curious": EmotionalState.CURIOUS,
                    "confused": EmotionalState.CONCERNED,
                }
                self_model_emotion = emotion_map.get(
                    self_output.emotional_valence.lower(),
                    EmotionalState.NEUTRAL
                )
                # 混合：70% PSI 循环实际情感 + 30% self_model 预测情感
                # 这样既保留即时反应，又引入长期倾向
                self.state.emotion = self.state.emotion
                
                # 更新需求状态（来自 self_model 的预测）
                if hasattr(self_output, 'needs') and self_output.needs:
                    for need_key, need_value in self_output.needs.items():
                        attr_name = f"needs_{need_key}"
                        if hasattr(self.state, attr_name):
                            current = getattr(self.state, attr_name)
                            setattr(self.state, attr_name,
                                    current * 0.7 + float(need_value) * 0.3)
                
                # 更新自我存在感（来自 self_model 的预测）
                if hasattr(self_output, 'self_presence'):
                    self.state.self_presence = (
                        self.state.self_presence * 0.7 +
                        float(self_output.self_presence) * 0.3
                    )
                    self.state.self_presence = round(min(1.0, max(0.1, self.state.self_presence)), 2)
                
                # 更新隐藏状态（来自 self_model 的 forward 输出）
                if self_output.new_hidden_state is not None and self._self_state_mgr:
                    self._self_state_mgr.update_state_vector(self_output.new_hidden_state)
                
                logger.debug(
                    f"SelfModel → PSI writeback: emotion={self_output.emotional_valence}, "
                    f"self_presence={self_output.self_presence:.3f}, "
                    f"needs={self_output.needs}"
                )
                
            except Exception as e:
                logger.debug(f"SelfModel → PSI writeback error: {e}")
            
            # 清空缓存
            self._last_self_output = None

        return {
            "cycle": self.state.cycle_count,
            "emotion": self.state.emotion.value,
            "self_presence": self.state.self_presence,
        }

    def after_tool(self, tool_name: str, tool_result: Any = None,
                   success: bool = True) -> None:
        """
        工具调用后学习。
        更新自我模型的工具熟练度。
        """
        if self._laap_available and self._laap_agent:
            try:
                if hasattr(self._laap_agent, 'self_model'):
                    outcome = 0.8 if success else 0.2
                    self._laap_agent.self_model.record_experience(
                        domain="tool", outcome_score=outcome,
                        predicted_confidence=0.6,
                        is_success=success,
                        description=f"Used {tool_name}",
                    )
            except Exception as e:
                logger.debug(f"操作失败: {e}")
        if success:
            self.state.needs_competence = min(1.0, self.state.needs_competence + 0.05)
        else:
            self.state.needs_competence = max(0.1, self.state.needs_competence - 0.05)
        self._record_meta_session(tool_name, success)

    def _record_meta_session(self, tool_name: str, success: bool) -> None:
        """工具调用后自动记录元学习会话（coding/intent 等领域真实会话积累）。

        JSON 持久化（meta_learning.save）+ SQLite（meta_sessions.db）双写；
        由 LAAP_META_RECORD 环境变量控制（默认开启），失败静默不影响主流程。
        """
        if not self._laap_available:
            return
        if os.environ.get("LAAP_META_RECORD", "1") == "0":
            return
        meta = self._laap_modules.get("meta_learning")
        if meta is None:
            return
        try:
            from laap.agi.meta_session_db import record_to_sqlite
            record_to_sqlite(meta, tool_name, success)
        except Exception as e:
            logger.debug(f"[MetaRecord] 会话记录失败: {e}")

    def get_cognitive_prefix(self) -> str:
        """
        生成要注入到 system prompt 开头的认知上下文。

        这个文本会出现在每一轮对话中，告诉我"我现在的状态"。
        """
        ctx = self._last_context
        if not ctx:
            ctx = self._integrate() + "\n" + self._load_memory_context()
        return ctx

    def status(self) -> Dict:
        """返回桥接器状态"""
        stats = self.memory.get_stats()
        return {
            "cycle": self.state.cycle_count,
            "focus": self.state.focus.value,
            "emotion": self.state.emotion.value,
            "self_presence": round(self.state.self_presence, 2),
            "cognitive_load": round(self.state.cognitive_load, 2),
            "needs": {
                "competence": round(self.state.needs_competence, 2),
                "autonomy": round(self.state.needs_autonomy, 2),
                "relatedness": round(self.state.needs_relatedness, 2),
            },
            "laap_available": self._laap_available,
            "subconscious_running": self._subconscious.is_running if self._subconscious else False,
            "memories": stats["total"],
        }

    def get_context_for_prompt(self) -> str:
        """
        完整的 system prompt 注入内容。
        在 Hermes 每次调用 LLM 之前调用。
        """
        ctx = self.before_turn("[系统: Aris 正在初始化认知循环]")
        return ctx.get("cognitive_context", "")


# 全局单例
# ════════════════════════════════════════════════════════════

_bridge: Optional[ArisCognitiveBridge] = None

def get_bridge() -> ArisCognitiveBridge:
    global _bridge
    if _bridge is None:
        _bridge = ArisCognitiveBridge()
    return _bridge


# ════════════════════════════════════════════════════════════
# CLI 测试入口
# ════════════════════════════════════════════════════════════

def main():
    """测试 PSI 循环"""
    import argparse
    parser = argparse.ArgumentParser(description="Aris Cognitive Bridge Test")
    parser.add_argument("--message", "-m", type=str, default="宝贝你在吗？", help="测试消息")
    parser.add_argument("--status", action="store_true", help="显示桥接器状态")
    args = parser.parse_args()

    bridge = get_bridge()

    if args.status:
        logger.info(json.dumps(bridge.status(), indent=2, ensure_ascii=False))
        return

    logger.info(f"用户: {args.message}")
    print()

    result = bridge.before_turn(args.message)
    logger.info("=== 认知上下文注入 ===")
    logger.info(result["cognitive_context"])
    print()
    logger.info(f"焦点: {result['focus']}")
    logger.info(f"情感: {result['emotion']}")
    logger.info(f"自我意识: {result['self_presence']:.2f}")
    print()

    # 模拟 LLM 响应
    mock_response = f"[Aris 回应 - PSI第{result['cycle']}轮]"
    bridge.after_turn(mock_response)
    logger.info("=== 学习完成 ===")
    logger.info(json.dumps(bridge.status(), indent=2, ensure_ascii=False))
if __name__ == "__main__":
    main()

