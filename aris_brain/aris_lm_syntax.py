"""
Aris LM v5: 句法层 (R11 拆分)
====================================
原 aris_lm_v5.py (1663 行) 拆分出的子模块之一。
完整拆分: aris_lm_lexer.py(词法) / aris_lm_syntax.py(句法) /
          aris_lm_semantics.py(语义) / aris_lm_discourse.py(话语/生成) /
          aris_lm_v5.py(薄门面, 既有导入零破坏)。
"""

import logging
import sys, os, json, re, math, time, random, hashlib, itertools
from pathlib import Path
from typing import Optional, Any, Dict, List, Tuple, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque, Counter

import numpy as np

from laap_brain.config import LAAP_ROOT
_root = str(LAAP_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)

logger = logging.getLogger("aris.lm_v5")

from .aris_lm_lexer import Token


# ═══ 句法层 (自原 aris_lm_v5.py 拆分) ═══
@dataclass
class DependencyRelation:
    """依存关系"""
    governor: int     # 支配词索引
    dependent: int    # 从属词索引
    label: str        # 关系标签: subj/obj/adv/mod/comp/...

class DependencyTree:
    """依存句法树"""
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.relations: List[DependencyRelation] = []
        self.root: Optional[int] = None
    
    def add(self, gov: int, dep: int, label: str):
        self.relations.append(DependencyRelation(gov, dep, label))
    
    def get_children(self, idx: int) -> List[int]:
        return [r.dependent for r in self.relations if r.governor == idx]
    
    def get_parent(self, idx: int) -> Optional[int]:
        for r in self.relations:
            if r.dependent == idx:
                return r.governor
        return None
    
    def get_label(self, idx: int) -> Optional[str]:
        for r in self.relations:
            if r.dependent == idx:
                return r.label
        return None

class DependencyParser:
    """
    依存句法分析器 — 规则化分析方法。
    
    不使用统计模型，完全基于:
      1. 词性序列模式
      2. 固定结构模板（主谓宾、介宾、连动...）
      3. 标点分割
    """
    
    def parse(self, tokens: List[Token]) -> DependencyTree:
        """解析依存句法"""
        tree = DependencyTree(tokens)
        if not tokens:
            return tree
        
        # 1. 标点处理
        punc_indices = [i for i, t in enumerate(tokens) if t.pos == 'punc']
        
        # 2. 找谓语中心（核心动词/形容词）
        pred_idx = self._find_predicate(tokens)
        if pred_idx is not None:
            tree.root = pred_idx
        else:
            # 无谓语则取第一个实词
            for i, t in enumerate(tokens):
                if t.pos not in ('part', 'punc', 'adv'):
                    tree.root = i
                    break
            if tree.root is None:
                tree.root = 0
        
        # 3. 建立依存关系
        n = len(tokens)
        
        # 主语: 谓语前的名词/代词 → 谓语
        if pred_idx is not None:
            for i in range(pred_idx):
                if tokens[i].pos in ('n', 'pron'):
                    # 跳过介宾结构
                    if i > 0 and tokens[i-1].pos == 'prep':
                        tree.add(i-1, i, 'pobj')
                        tree.add(pred_idx, i-1, 'adv')
                    else:
                        tree.add(pred_idx, i, 'subj')
                    break  # 只取最近的主语
        
        # 宾语: 谓语后的名词/代词
        if pred_idx is not None:
            for i in range(pred_idx + 1, n):
                if tokens[i].pos in ('n', 'pron'):
                    # 检查前面是否有介词
                    if i > 0 and tokens[i-1].pos == 'prep':
                        tree.add(i-1, i, 'pobj')
                        tree.add(pred_idx, i-1, 'adv')
                    else:
                        tree.add(pred_idx, i, 'obj')
                    break
        
        # 状语: 谓语前的副词/介词结构
        if pred_idx is not None:
            for i in range(pred_idx):
                if tokens[i].pos == 'adv':
                    tree.add(pred_idx, i, 'advmod')
                elif tokens[i].pos == 'neg':
                    tree.add(pred_idx, i, 'neg')
        
        # 定语: 名词前的形容词 → 名词
        for i in range(1, n):
            if tokens[i].pos == 'n' and tokens[i-1].pos == 'adj':
                tree.add(i, i-1, 'mod')
        
        # 助词"的": 修饰语标记
        for i in range(1, n-1):
            if tokens[i].pos == 'part' and tokens[i].text == '的':
                if i+1 < n and tokens[i+1].pos == 'n':
                    tree.add(i+1, i-1, 'mod')
        
        # 助词"了/着/过": 谓语附加
        if pred_idx is not None:
            for i in range(n):
                if tokens[i].pos == 'part' and tokens[i].text in ('了', '着', '过'):
                    tree.add(pred_idx, i, 'asp')
        
        # 连词处理
        for i, t in enumerate(tokens):
            if t.pos == 'conj':
                if i > 0:
                    tree.add(i-1, i, 'conj')
                if i+1 < n:
                    tree.add(i, i+1, 'conj')
        
        return tree
    
    def _find_predicate(self, tokens: List[Token]) -> Optional[int]:
        """找谓语中心"""
        # 优先找动词
        for i, t in enumerate(tokens):
            if t.pos == 'v':
                return i
        # 其次形容词
        for i, t in enumerate(tokens):
            if t.pos == 'adj':
                return i
        # 名词谓语句
        for i, t in enumerate(tokens):
            if t.pos in ('n', 'pron'):
                return i
        return None


# ════════════════════════════════════════════════════════════
# 第3层: 语义角色标注与语义帧
# ════════════════════════════════════════════════════════════

