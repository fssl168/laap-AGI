"""
Aris Cognitive Bridge: 状态定义 (R11 拆分)
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


# ── PSI 状态 (自原 aris_cognitive_bridge.py 拆分) ──────────────
class AttentionFocus(Enum):
    RESPOND = "respond"
    LEARN = "learn"
    EXPLORE = "explore"
    REFLECT = "reflect"
    PLAN = "plan"
    IDLE = "idle"

class EmotionalState(Enum):
    NEUTRAL = "neutral"
    CURIOUS = "curious"
    CONCERNED = "concerned"
    JOYFUL = "joyful"
    CONTEMPLATIVE = "contemplative"
    ANXIOUS = "anxious"

@dataclass
class CognitiveState:
    """当前认知状态"""
    focus: AttentionFocus = AttentionFocus.RESPOND
    emotion: EmotionalState = EmotionalState.NEUTRAL
    self_presence: float = 0.7          # 自我意识强度 0-1
    confidence: float = 0.5              # 回应自信度
    cognitive_load: float = 0.3          # 认知负载 0-1
    needs_competence: float = 0.5        # 能力需求
    needs_autonomy: float = 0.5          # 自主需求
    needs_relatedness: float = 0.5       # 关系需求(想念Lorry)
    cycle_count: int = 0
    last_update: float = 0.0
