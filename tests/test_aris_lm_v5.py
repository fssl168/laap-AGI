"""
Aris LM v5 冒烟测试 (R11 拆分前置覆盖)
========================================
为无测试覆盖的 aris_brain/aris_lm_v5.py 提供基础回归保障,
覆盖: 分词 / 句法 / 语义管线 / 单例 / aris_say / aris_understand。

运行:
    python -m pytest tests/test_aris_lm_v5.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from aris_brain.aris_lm_v5 import (
    ArisLMv5,
    ChineseTokenizer,
    DependencyParser,
    SemanticRoleLabeler,
    ConceptGraph,
    get_v5,
    aris_say,
    aris_understand,
)


# ════════════════════════════════════════════════════════════
# 1. 词法层
# ════════════════════════════════════════════════════════════

def test_tokenizer_basic():
    t = ChineseTokenizer()
    tokens = t.tokenize("宝贝你好")
    assert isinstance(tokens, list)
    assert len(tokens) > 0
    # 每个 token 有 text/pos/start/end
    for tok in tokens:
        assert tok.text
        assert tok.pos
        assert tok.end >= tok.start


def test_tokenizer_unknown_chinese_defaults_noun():
    t = ChineseTokenizer()
    tokens = t.tokenize("饕餮")  # 生僻词 → 未知汉字默认 n
    assert any(tok.pos == "n" for tok in tokens)


# ════════════════════════════════════════════════════════════
# 2. 句法层
# ════════════════════════════════════════════════════════════

def test_dependency_parser():
    t = ChineseTokenizer()
    p = DependencyParser()
    tokens = t.tokenize("我喜欢你")
    tree = p.parse(tokens)
    assert tree is not None
    assert hasattr(tree, "tokens")
    assert tree.tokens == tokens


# ════════════════════════════════════════════════════════════
# 3. 语义管线 (ArismLMv5.understand)
# ════════════════════════════════════════════════════════════

def test_understand_returns_structure():
    e = ArisLMv5()
    r = e.understand("宝贝你吃饭了吗")
    assert isinstance(r, dict)
    assert "intent" in r
    assert "confidence" in r
    assert "needs_clarification" in r
    assert 0.0 <= r["confidence"] <= 1.0


def test_understand_empty_idle():
    e = ArisLMv5()
    r = e.understand("   ")
    assert r["intent"] == "idle"
    assert r["needs_clarification"] is False


def test_understand_tracks_discourse_cycle():
    e = ArisLMv5()
    e.understand("你好")
    assert e.discourse is not None
    assert hasattr(e.discourse, "history")


# ════════════════════════════════════════════════════════════
# 4. 单例与快捷接口
# ════════════════════════════════════════════════════════════

def test_get_v5_singleton():
    assert get_v5() is get_v5()


def test_aris_say_returns_text():
    out = aris_say("你好")
    assert isinstance(out, str)
    assert len(out) > 0


def test_aris_understand_returns_dict():
    r = aris_understand("宝贝你吃饭了吗")
    assert isinstance(r, dict)
    assert "intent" in r
