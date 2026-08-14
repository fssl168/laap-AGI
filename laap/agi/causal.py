"""
LAAP AGI — 统一量子因果引擎: 薄门面 (R11 拆分)
============================================================
原 causal.py (1642 行) 已拆分为:
  causal_models.py / causal_discovery.py / causal_engine.py
本文件保留全部既有导入符号, 确保
`from laap.agi.causal import UnifiedCausalEngine, CausalRule, ...` 零破坏。
"""

from .causal_models import (
    CausalCondition,
    CausalEffect,
    CausalRule,
    TemporalCausalLink,
    TemporalCausalChain,
    FactorOperator,
    CausalFactor,
    MultiFactorRule,
    InterventionResult,
    CounterfactualEmotion,
)
from .causal_discovery import (
    QuantumCausalStore,
    ConditionalIndependenceTester,
    CausalDiscovery,
    CausalBond,
)
from .causal_engine import UnifiedCausalEngine

# 向后兼容别名
CausalEngine = UnifiedCausalEngine

__all__ = [
    "CausalCondition", "CausalEffect", "CausalRule",
    "QuantumCausalStore", "ConditionalIndependenceTester",
    "CausalDiscovery", "CausalBond",
    "TemporalCausalLink", "TemporalCausalChain",
    "FactorOperator", "CausalFactor", "MultiFactorRule",
    "InterventionResult", "CounterfactualEmotion",
    "UnifiedCausalEngine", "CausalEngine",
]
