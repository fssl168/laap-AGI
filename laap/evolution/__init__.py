"""LAAP AGI — True RSI 受限递归引擎包 (M4)。

包内模块:
  true_rsi.py  — TrueRSIEngine: 受限递归编排层 (作用域限定 + 永久只读 + 递归深度<=1)

历史说明:
  早期 `laap/evolution/rsi.py` 与 `laap/evolution/aevo/harness.py` 已废弃归档;
  `laap/agi/evolution_system.py` 与 `aris_brain/agi_kernel.py` 中对其的引用在
  try/except 中容错降级 (ImportError → pass), 本包不恢复这些废弃模块。
"""

from laap.evolution.true_rsi import TrueRSIEngine  # noqa: F401

__all__ = ["TrueRSIEngine"]
