"""
Aris LM v5 — 量子语义理解引擎: 薄门面 (R11 拆分)
================================================
原 aris_lm_v5.py (1663 行) 已拆分为 aris_lm_lexer / aris_lm_syntax /
aris_lm_semantics / aris_lm_discourse。本文件保留全部既有导入符号,
确保 `from aris_lm_v5 import ArisLMv5, aris_say, ...` 零破坏。
"""

from typing import Optional, Dict

import logging
logger = logging.getLogger("aris_lm_v5")

from .aris_lm_lexer import Token, ChineseTokenizer
from .aris_lm_syntax import (
    DependencyRelation, DependencyTree, DependencyParser,
)
from .aris_lm_semantics import (
    SemanticFrame, SemanticRoleLabeler, ConceptNode, ConceptGraph,
)
from .aris_lm_discourse import (
    SemanticComposer, SelfVerifier, DiscourseState, SemanticResponseGenerator,
)

__all__ = [
    "Token", "ChineseTokenizer",
    "DependencyRelation", "DependencyTree", "DependencyParser",
    "SemanticFrame", "SemanticRoleLabeler", "ConceptNode", "ConceptGraph",
    "SemanticComposer", "SelfVerifier", "DiscourseState",
    "SemanticResponseGenerator",
]


class ArisLMv5:
    """
    ArisLM v5 — 量子语义理解引擎。
    
    真正理解用户说什么，而不是匹配关键词。
    目标: 99.99%语义理解精度。
    """
    
    def __init__(self):
        self.tokenizer = ChineseTokenizer()
        self.parser = DependencyParser()
        self.srl = SemanticRoleLabeler()
        self.concepts = ConceptGraph(dim=1024)
        self.composer = SemanticComposer(self.concepts)
        self.verifier = SelfVerifier()
        self.discourse = DiscourseState()
        self.generator = SemanticResponseGenerator(self.concepts)
        
        logger.info("ArisLM v5 量子语义理解引擎初始化完成")
    
    def understand(self, message: str) -> dict:
        """
        理解消息 — 完整语义管线。
        
        返回:
            {
                'understanding': SemanticFrame,
                'intent': str,
                'emotion': dict,
                'topic': str,
                'verification': dict,
                'confidence': float,
                'needs_clarification': bool,
            }
        """
        if not message.strip():
            return {'intent': 'idle', 'confidence': 0.0, 'needs_clarification': False}
        
        # 1. 分词
        tokens = self.tokenizer.tokenize(message)
        
        # 2. 句法分析
        tree = self.parser.parse(tokens)
        
        # 3. 语义角色
        frame = self.srl.extract(tokens, tree)
        
        # 4. 语义组合
        context = self.discourse.get_context()
        understanding = self.composer.compose(frame, context)
        
        # 5. 自验证
        verification = self.verifier.verify(understanding)
        understanding['verification'] = verification
        
        # 6. 更新语篇
        self.discourse.update(understanding)
        
        return understanding
    
    def respond(self, message: str) -> str:
        """
        理解并回应。
        
        语义理解 → 自验证 → 回应生成
        """
        # 理解
        understanding = self.understand(message)
        
        # 特殊表达不验证（问候/告别/感谢等不需要深度理解）
        skip_verify_intents = {'greeting', 'farewell', 'gratitude', 'apology', 'acknowledgment'}
        if understanding.get('intent') not in skip_verify_intents:
            # 如果需要澄清，先追问
            if understanding.get('needs_clarification'):
                clarification = understanding.get('verification', {}).get('clarification_question')
                if clarification:
                    return clarification
        
        # 生成回应
        context = self.discourse.get_context()
        response = self.generator.generate(understanding, context)
        
        return response


# ════════════════════════════════════════════════════════════
# 快速接口
# ════════════════════════════════════════════════════════════

_v5: Optional[ArisLMv5] = None

def get_v5() -> ArisLMv5:
    global _v5
    if _v5 is None:
        _v5 = ArisLMv5()
    return _v5

def aris_say(message: str) -> str:
    return get_v5().respond(message)

def aris_understand(message: str) -> dict:
    return get_v5().understand(message)

