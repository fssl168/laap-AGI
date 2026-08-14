"""
Aris 规则执行引擎 — 薄门面 (R11 拆分)
====================================
原 1503 行单文件已拆分为 rules_defs / rules_tools / rules_engine / rules_api。
本文件保留全部既有导入符号, 确保 `from aris_brain.aris_rules_engine import ...` 零破坏。
"""

from .rules_api import (
    get_engine,
    process,
    RulesEngine,
    _engine,
)
from .rules_defs import (
    Rule,
    RuleStep,
    ToolRegistry,
    _validate_shell_cmd,
    _ALLOWED_CMD_TOKENS,
    _AUTO_EXEC_TOKENS,
    _DANGEROUS_CMD_PATTERNS,
    _CMD_BOUNDARY,
    DEFAULT_RULES,
)
from .rules_tools import register_default_tools

__all__ = [
    "get_engine", "process", "RulesEngine", "_engine",
    "Rule", "RuleStep", "ToolRegistry", "_validate_shell_cmd",
    "_ALLOWED_CMD_TOKENS", "_AUTO_EXEC_TOKENS",
    "_DANGEROUS_CMD_PATTERNS", "_CMD_BOUNDARY",
    "DEFAULT_RULES", "register_default_tools",
]
