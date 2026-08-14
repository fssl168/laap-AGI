"""策略参数提取器（AST 从 mutated code 提取 STRATEGY_PARAMS）。

增强 3：让 QuantEvolutionGate 对 strategy.py 的变更做 mutation 前后 OOS 对比。
"""

from __future__ import annotations

import ast
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("laap.paper_trading.param_extractor")


def extract_strategy_params(code: str) -> Optional[Dict[str, Any]]:
    """从代码提取 `STRATEGY_PARAMS = {...}` 的键值对（AST 解析）。

    Args:
        code: 源码（通常为 mutation.mutated_code）
    Returns: {param: value}；未找到 STRATEGY_PARAMS 赋值或语法错误时 None。
    """
    if not code or not code.strip():
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        logger.warning(f"extract_strategy_params: syntax error: {e}")
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "STRATEGY_PARAMS":
                if isinstance(node.value, ast.Dict):
                    params: Dict[str, Any] = {}
                    for k, v in zip(node.value.keys, node.value.values):
                        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                            params[str(k.value)] = v.value
                    return params if params else None
    return None


def load_baseline_params() -> Dict[str, Any]:
    """从 laap/paper_trading/strategy.py 读取当前策略参数（baseline）。"""
    try:
        from laap.paper_trading import strategy
        return dict(getattr(strategy, "STRATEGY_PARAMS", {"short": 5, "long": 20}))
    except Exception as e:
        logger.warning(f"load_baseline_params failed: {e}")
        return {"short": 5, "long": 20}
