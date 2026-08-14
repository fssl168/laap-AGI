"""
LAAP Brain API — 兼容入口（DEPRECATED）
========================================

统一入口已收敛至 `laap_brain.api`（全功能版：工具路由 / RSI / 自动记忆 / SSE 流式）。

本文件仅为旧启动脚本、Dockerfile 与文档保留的向后兼容薄包装：
直接运行本文件等价于 `python -m laap_brain.api`。

用法（推荐使用统一入口）:
    python -m laap_brain.api --port 11546

历史: 本文件曾为独立的 OpenAI 兼容 API 实现，已由 laap_brain/api.py
      （全功能实现）取代，2026-08 收敛。新功能只维护 laap_brain.api。
"""
import sys as _sys
from pathlib import Path as _Path

_LAAP_ROOT = str(_Path(__file__).resolve().parent.parent)
if _LAAP_ROOT not in _sys.path:
    _sys.path.insert(0, _LAAP_ROOT)

# re-export laap_brain.api 的全部公开符号（单一实现，无重复逻辑）
from laap_brain.api import (  # noqa: F401
    create_app,
    get_integrator,
    handle_bootstrap,
    handle_chat_completions,
    handle_cognitive_state,
    handle_express,
    handle_get_bond,
    handle_get_personality,
    handle_health,
    handle_models,
    handle_recall_memory,
    handle_reflect,
    handle_root,
    handle_rsi_full_cycle,
    handle_rsi_improve,
    handle_rsi_status,
    handle_set_personality,
    main,
    process_with_laap,
)


def get_laap_engine():
    """DEPRECATED: 等价于 laap_brain.api.get_integrator()（惰性加载 LAAP 引擎）。"""
    return get_integrator()


if __name__ == "__main__":
    main()
