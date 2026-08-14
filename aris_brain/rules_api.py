"""
Aris 规则引擎 — 门面 (R11 拆分)
====================================
原 aris_rules_engine.py (1503 行) 拆分出的子模块之一。
完整拆分: rules_defs.py(定义表) / rules_tools.py(工具) /
          rules_engine.py(引擎) / rules_api.py(门面) /
          aris_rules_engine.py(薄门面, 保持既有导入零破坏)。
"""

import sys, os, json, re, time, subprocess, logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field

from laap_brain.config import LAAP_ROOT
_root = str(LAAP_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)

logger = logging.getLogger("aris.rules")

# ─── 全局单例 + 门面 (自原 aris_rules_engine.py 拆分) ──────
from .rules_engine import RulesEngine

# ─── 全局单例 ────────────────────────────────────────────

_engine: Optional[RulesEngine] = None

def get_engine() -> RulesEngine:
    global _engine
    if _engine is None:
        _engine = RulesEngine()
    return _engine

def process(text: str) -> Dict[str, Any]:
    return get_engine().process(text)


