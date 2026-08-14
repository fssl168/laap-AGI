"""
Aris Cognitive Bridge: 依赖探测 (R11 拆分)
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


# ── 外部依赖探测 (自原 aris_cognitive_bridge.py 拆分) ──────────
# ── 路径 ────────────────────────────────────────────────────
from laap_brain.config import BRAIN_DIR as BRAIN_ROOT, LAAP_ROOT

from aris_brain.memory_bridge import get_memory_context, recall_related, store_important
from aris_brain.memory_store import MemoryStore, MemoryFragment

# ── CodeGraph 代码知识图谱 ──────────────────────────────────
try:
    from laap_codegraph import get_codegraph as _get_cg, LAAPCodeGraph
    _cg_available = True
except Exception:
    _cg_available = False
    _get_cg = None

# ── TaskSupervisor 超长任务监督 ────────────────────────────
try:
    from task_supervisor import TaskSupervisor, TaskSource
    _task_supervisor = None
    _ts_available = True
except Exception:
    _ts_available = False
    _task_supervisor = None

# ── ProjectPlanner 项目经理规划引擎 ──────────────────────
try:
    from project_planner import ProjectPlanner, Phase
    from project_planner import save_project as _save_proj, load_project as _load_proj, list_projects as _list_projs
    _project_planner = None
    _pp_available = True
except Exception:
    _pp_available = False
    _project_planner = None

# ── AutoLearner 自动学习引擎 ─────────────────────────────
try:
    from auto_learner import AutoLearner
    _auto_learner = None
    _al_available = True
except Exception:
    _al_available = False
    _auto_learner = None

# ── CognitiveBus 认知总线 ────────────────────────────────────
try:
    from cognitive_bus import route_message as _cb_route, get_bus as _get_cb
    _cb_available = True
except Exception:
    _cb_available = False
    _cb_route = None
    _get_cb = None

logger = logging.getLogger("aris.cognitive_bridge")

# ── 三路径认知控制（llm_tamer / guided_generator / self_model_nn）──
# Path 1: llm_tamer — logit bias 控制
# Path 2: guided_generator — 约束生成
# Path 3: self_model_nn — 持久神经网络自我模型
try:
    from laap.laap_tools.llm_tamer import LLMTamer
    from laap.laap_tools.guided_generator.generator import GuidedGenerator
    from laap.laap_tools.self_model.state_manager import SelfStateManager
    from laap.laap_tools.self_model.model import (
        SelfModelNN, SelfModelConfig, SelfStateOutput,
    )
    from laap.laap_tools.self_model.adapter import (
        bridge_state_to_snapshot,
        self_state_output_to_snapshot,
        snapshot_to_self_state_output,
    )
    _three_paths_available = True
except Exception as e:
    _three_paths_available = False
    LLMTamer = None  # type: ignore
    GuidedGenerator = None  # type: ignore
    SelfStateManager = None  # type: ignore
    SelfModelNN = None  # type: ignore
    SelfModelConfig = None  # type: ignore
    SelfStateOutput = None  # type: ignore
    bridge_state_to_snapshot = None  # type: ignore
    self_state_output_to_snapshot = None  # type: ignore
    snapshot_to_self_state_output = None  # type: ignore
    logger.info(f"Three-paths (tamer/generator/self_model) unavailable: {e}")

# ── 任务路由 + 上下文压缩 — 第一性原理 Token 节省 ──
try:
    from aris_task_router import (
        classify as _router_classify, LoadLevel as _LoadLevel,
    )
    _router_available = True
except Exception as e:
    _router_available = False
    _LoadLevel = None
    logger.info(f"Task router unavailable: {e}")

try:
    from aris_context_compressor import (
        compress_cognitive_context as _compress_ctx,
        compress_tool_output as _compress_tool,
    )
    _compressor_available = True
except Exception as e:
    _compressor_available = False
    logger.info(f"Context compressor unavailable: {e}")

try:
    from aris_emotion_coupling import compute_from_engine as _compute_coupling
    _coupling_available = True
except Exception as e:
    _coupling_available = False
    logger.info(f"Emotion coupling unavailable: {e}")

# ── Code Engine — 第一性原理代码执行 ──────────────
try:
    sys.path.insert(0, str(Path(BRAIN_ROOT.parent / "aris_code_engine")))
    from code_bridge import get_code_bridge, CodeBridge
    _code_engine_available = True
except Exception as e:
    _code_engine_available = False
    logger.info(f"Code engine unavailable: {e}")

# ── PSI 状态 ────────────────────────────────────────────────

# ── 失败分支兜底 (拆分时补齐: 保证主类 import 永不因可选依赖失败) ──
try:
    _ = TaskSupervisor
except NameError:
    TaskSupervisor = None  # type: ignore
    TaskSource = None  # type: ignore
try:
    _ = ProjectPlanner
except NameError:
    ProjectPlanner = None  # type: ignore
    Phase = None  # type: ignore
    _save_proj = None  # type: ignore
    _load_proj = None  # type: ignore
    _list_projs = None  # type: ignore
try:
    _ = AutoLearner
except NameError:
    AutoLearner = None  # type: ignore
try:
    _ = _LoadLevel
except NameError:
    _LoadLevel = None  # type: ignore
try:
    _ = _router_classify
except NameError:
    _router_classify = None  # type: ignore
try:
    _ = _compress_ctx
except NameError:
    _compress_ctx = None  # type: ignore
    _compress_tool = None  # type: ignore
try:
    _ = _compute_coupling
except NameError:
    _compute_coupling = None  # type: ignore
try:
    _ = get_code_bridge
except NameError:
    get_code_bridge = None  # type: ignore
    CodeBridge = None  # type: ignore

