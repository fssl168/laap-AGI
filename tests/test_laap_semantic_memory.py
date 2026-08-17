"""
LAAP Semantic Memory 嵌入器选择回归测试
=======================================

覆盖 2026-08-11 修复：环境存在 DEEPSEEK_API_KEY 但未配置 OPENAI_BASE_URL 时，
嵌入器不应选择 OpenAI 兼容 API（否则 key 会被发往 api.openai.com 导致 401，
所有 recall 静默返回空）。

运行:
    python -m pytest tests/test_laap_semantic_memory.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repository root is on sys.path when running directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aris_brain"))

import pytest  # noqa: E402

from laap_semantic_memory import (  # noqa: E402
    OpenAIEmbeddingProvider,
    SentenceTransformersProvider,
    TfidfEmbeddingProvider,
    _get_embedding_provider,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """确保测试互不影响：清掉所有可能影响嵌入器选择的变量。"""
    for var in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def _provider_name() -> str:
    return type(_get_embedding_provider()).__name__


def test_bare_deepseek_key_does_not_select_api_provider(monkeypatch):
    """回归：只有 DEEPSEEK_API_KEY、无 OPENAI_BASE_URL 时，不得选 API 嵌入器。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert not isinstance(_get_embedding_provider(), OpenAIEmbeddingProvider)
    # 无 OPENAI_BASE_URL → 不走 API，落到本地 provider
    # （sentence-transformers 首选；无该依赖的环境回退 TF-IDF 兜底——两者都是本地，非 API）
    assert isinstance(_get_embedding_provider(),
                      (SentenceTransformersProvider, TfidfEmbeddingProvider))


def test_openai_key_selects_api_provider(monkeypatch):
    """真实 OPENAI_API_KEY 时应选择 OpenAI 嵌入器。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert isinstance(_get_embedding_provider(), OpenAIEmbeddingProvider)


def test_deepseek_key_with_explicit_base_selects_api_provider(monkeypatch):
    """DEEPSEEK_API_KEY + 显式 OPENAI_BASE_URL 时允许 API 嵌入器。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://embeddings.example.com/v1")
    assert isinstance(_get_embedding_provider(), OpenAIEmbeddingProvider)


def test_no_keys_falls_back_to_local_provider():
    """无任何 key 时回退到本地嵌入（bge-small-zh；无 sentence_transformers 则 TF-IDF 兜底）。"""
    assert isinstance(_get_embedding_provider(),
                      (SentenceTransformersProvider, TfidfEmbeddingProvider))
