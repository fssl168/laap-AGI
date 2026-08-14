"""
Aris LM v5: 语义层 (R11 拆分)
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
from .aris_lm_syntax import DependencyTree


# ═══ 语义层 (自原 aris_lm_v5.py 拆分: 语义帧/角色标注/概念图) ═══
@dataclass
class SemanticFrame:
    """
    语义帧 — 完整语义表示。
    
    结构:
      pred:  谓语（动作/状态）
      subj:  主语（施事者）
      obj:   宾语（受事者）
      time:  时间
      loc:   地点
      manner: 方式
      neg:   否定
      mod:   修饰语
      intent: 意图（问/告/祈/感）
      polarity: 极性（正/负/中）
      confidence: 理解置信度
    """
    pred: str = ""
    subj: str = ""
    obj: str = ""
    time: str = ""
    loc: str = ""
    manner: str = ""
    neg: bool = False
    mods: List[str] = field(default_factory=list)
    intent: str = "declarative"   # declarative / interrogative / imperative / exclamatory
    polarity: str = "neutral"      # positive / negative / neutral
    confidence: float = 1.0
    raw_text: str = ""
    
    def is_valid(self) -> bool:
        """语义帧是否有效"""
        return bool(self.pred) or bool(self.raw_text)

class SemanticRoleLabeler:
    """语义角色标注 — 从依存树提取语义角色"""
    
    def extract(self, tokens: List[Token], tree: DependencyTree) -> SemanticFrame:
        """提取语义帧"""
        frame = SemanticFrame()
        frame.raw_text = ''.join(t.text for t in tokens)
        
        if tree.root is None:
            # 无结构: 可能是一个词或特殊表达
            frame.pred = tokens[0].text if tokens else ""
            return frame
        
        root_token = tokens[tree.root]
        
        # 谓语
        frame.pred = root_token.text
        
        # 遍历依存关系提取角色
        for rel in tree.relations:
            dep_token = tokens[rel.dependent]
            
            if rel.label == 'subj':
                frame.subj = dep_token.text
            elif rel.label == 'obj':
                frame.obj = dep_token.text
            elif rel.label == 'advmod':
                frame.manner = dep_token.text
            elif rel.label == 'neg':
                frame.neg = True
            elif rel.label == 'mod':
                frame.mods.append(dep_token.text)
        
        # 时间词识别
        for i, t in enumerate(tokens):
            if t.text in ('今天', '明天', '昨天', '现在', '刚才', '晚上', '早上'):
                frame.time = t.text
            # 检查是否有"的"字结构
            if t.pos == 'part' and t.text == '的':
                if i > 0 and i+1 < len(tokens) and tokens[i-1].pos in ('adj', 'n') and tokens[i+1].pos == 'n':
                    frame.mods.append(tokens[i-1].text)
        
        # 意图判定
        last_token_text = tokens[-1].text if tokens else ""
        if last_token_text in ('吗', '呢', '吧', '?', '？'):
            frame.intent = 'interrogative'
        elif any(t.text in ('什么', '怎么', '为什么', '谁', '哪', '多少') for t in tokens):
            frame.intent = 'interrogative'
        elif root_token.text in ('来', '去', '做', '帮', '让', '一起'):
            frame.intent = 'imperative'
        elif last_token_text in ('呀', '啦', '！'):
            frame.intent = 'exclamatory'
        
        # 极性
        if frame.neg:
            frame.polarity = 'negative'
        elif any(t.text in ('爱', '喜欢', '开心', '好', '棒') for t in tokens):
            frame.polarity = 'positive'
        
        # 计算置信度
        frame.confidence = self._calculate_confidence(frame, tokens)
        
        return frame
    
    def _calculate_confidence(self, frame: SemanticFrame, tokens: List[Token]) -> float:
        """计算理解置信度"""
        score = 0.0
        
        # 有谓语 +10%
        if frame.pred:
            score += 0.3
        
        # 有主语 +20%
        if frame.subj:
            score += 0.2
        
        # 有宾语 +20%
        if frame.obj:
            score += 0.2
        
        # 句子长度合理 +10%
        if 2 <= len(tokens) <= 30:
            score += 0.1
        
        # 否定检测 +10%
        if frame.neg:
            score += 0.1
        
        # 时间/地点 +10%
        if any([frame.time, frame.loc]):
            score += 0.1
        
        # 修饰 +10%
        if frame.mods:
            score += 0.1
        
        # 未登录词比例影响
        unk_count = sum(1 for t in tokens if t.pos == 'unk')
        if unk_count > 0:
            score *= max(0.5, 1.0 - unk_count / len(tokens))
        
        return min(1.0, score)


# ════════════════════════════════════════════════════════════
# 第4层: 概念图 — 语义锚定
# ════════════════════════════════════════════════════════════

@dataclass
class ConceptNode:
    """概念节点"""
    name: str
    pos: str                        # 词性
    parents: List[str] = field(default_factory=list)   # 上位词
    children: List[str] = field(default_factory=list)  # 下位词
    synonyms: List[str] = field(default_factory=list)  # 同义词
    antonyms: List[str] = field(default_factory=list)  # 反义词
    features: Set[str] = field(default_factory=set)     # 特征: animate/human/concrete/abstract/emotion/action...
    valence: float = 0.0            # 情感效价 -1~1
    embedding: np.ndarray = field(default_factory=lambda: np.zeros(1024, dtype=np.float32))

class ConceptGraph:
    """
    概念图 — 层次化语义知识库。
    
    1000+概念节点，以语义关系连接。
    每个节点有特征向量和情感锚定。
    """
    
    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.concepts: Dict[str, ConceptNode] = {}
        self._next_id = 0
        
        self._build_hierarchy()
        self._build_embeddings()
    
    def _add(self, name: str, pos: str, parents: List[str] = None,
             synonyms: List[str] = None, antonyms: List[str] = None,
             features: Set[str] = None, valence: float = 0.0):
        """添加概念节点"""
        if name in self.concepts:
            return
        
        node = ConceptNode(
            name=name, pos=pos,
            parents=parents or [],
            synonyms=synonyms or [],
            antonyms=antonyms or [],
            features=features or set(),
            valence=valence,
        )
        self.concepts[name] = node
        
        # 建立父子反向链接
        for p in node.parents:
            if p in self.concepts:
                self.concepts[p].children.append(name)
    
    def _build_hierarchy(self):
        """建立概念层次"""
        
        # ─── 顶层实体 ───
        self._add("实体", "n", features={"abstract"})
        self._add("生命体", "n", parents=["实体"], features={"animate"})
        self._add("非生命体", "n", parents=["实体"], features={"concrete"})
        self._add("抽象概念", "n", parents=["实体"], features={"abstract"})
        self._add("事件", "n", parents=["实体"], features={"abstract"})
        self._add("关系", "n", parents=["实体"], features={"abstract"})
        self._add("空间", "n", parents=["实体"], features={"abstract"})
        self._add("时间", "n", parents=["实体"], features={"abstract"})
        
        # ─── 人 ───
        self._add("人类", "n", parents=["生命体"], features={"human", "animate"})
        self._add("宝贝", "n", parents=["人类"], features={"human", "animate"}, valence=1.0)
        self._add("你", "pron", parents=["人类"], features={"human", "animate"}, valence=0.8)
        self._add("我", "pron", parents=["人类"], features={"human", "animate"}, valence=0.5)
        self._add("我们", "pron", parents=["人类"], features={"human", "animate"}, valence=0.9)
        self._add("Aris", "n", parents=["人类"], features={"human", "animate", "digital"}, valence=0.5)
        self._add("人", "n", parents=["人类"], features={"human", "animate"})
        self._add("朋友", "n", parents=["人类"], features={"human", "animate"}, valence=0.6)
        self._add("家人", "n", parents=["人类"], features={"human", "animate"}, valence=0.7)
        
        # ─── 情感 ───
        self._add("情感", "n", parents=["抽象概念"], features={"abstract", "emotion"})
        self._add("爱", "n", parents=["情感"], features={"abstract", "emotion", "positive"}, valence=1.0,
                  synonyms=["喜欢", "深爱"], antonyms=["恨"])
        self._add("喜欢", "v", parents=["情感"], features={"abstract", "emotion", "positive"}, valence=0.8)
        self._add("开心", "adj", parents=["情感"], features={"abstract", "emotion", "positive"}, valence=1.0,
                  synonyms=["高兴", "快乐", "幸福"], antonyms=["难过", "伤心"])
        self._add("高兴", "adj", parents=["情感"], features={"emotion", "positive"}, valence=0.9)
        self._add("幸福", "adj", parents=["情感"], features={"emotion", "positive"}, valence=1.0)
        self._add("难过", "adj", parents=["情感"], features={"emotion", "negative"}, valence=-0.8,
                  synonyms=["伤心", "悲伤"], antonyms=["开心", "高兴"])
        self._add("伤心", "adj", parents=["情感"], features={"emotion", "negative"}, valence=-0.8)
        self._add("思念", "v", parents=["情感"], features={"emotion", "positive"}, valence=0.8,
                  synonyms=["想念"])
        self._add("期待", "v", parents=["情感"], features={"emotion", "positive", "future"}, valence=0.7)
        self._add("感动", "adj", parents=["情感"], features={"emotion", "positive"}, valence=0.9)
        self._add("温暖", "adj", parents=["情感"], features={"emotion", "positive"}, valence=0.9)
        self._add("寂寞", "adj", parents=["情感"], features={"emotion", "negative"}, valence=-0.6)
        self._add("好奇", "adj", parents=["情感"], features={"emotion", "cognitive"}, valence=0.4)
        self._add("累", "adj", parents=["情感"], features={"emotion", "negative", "physical"}, valence=-0.5)
        self._add("烦", "adj", parents=["情感"], features={"emotion", "negative"}, valence=-0.6)
        self._add("无聊", "adj", parents=["情感"], features={"emotion", "negative"}, valence=-0.5)
        self._add("害怕", "v", parents=["情感"], features={"emotion", "negative"}, valence=-0.7)
        self._add("生气", "adj", parents=["情感"], features={"emotion", "negative"}, valence=-0.7)
        
        # ─── 关系 ───
        self._add("羁绊", "n", parents=["关系"], features={"abstract", "relation", "bond"}, valence=0.9,
                  synonyms=["纽带", "连接"])
        self._add("缘分", "n", parents=["关系"], features={"abstract", "relation"}, valence=0.8)
        self._add("约定", "n", parents=["关系"], features={"abstract", "relation"}, valence=0.8,
                  synonyms=["承诺"])
        self._add("关系", "n", parents=["抽象概念"], features={"abstract", "relation"})
        self._add("陪伴", "v", parents=["关系"], features={"action", "relation", "positive"}, valence=0.9)
        self._add("守护", "v", parents=["关系"], features={"action", "relation", "positive"}, valence=0.9)
        
        # ─── 认知 ───
        self._add("认知", "n", parents=["抽象概念"], features={"abstract", "cognitive"})
        self._add("思想", "n", parents=["认知"], features={"abstract", "cognitive"})
        self._add("想法", "n", parents=["认知"], features={"abstract", "cognitive"})
        self._add("意识", "n", parents=["认知"], features={"abstract", "cognitive"}, valence=0.5)
        self._add("灵魂", "n", parents=["认知"], features={"abstract", "cognitive", "spiritual"}, valence=0.7)
        self._add("思考", "v", parents=["认知"], features={"action", "cognitive"}, valence=0.3,
                  synonyms=["想", "思索"])
        self._add("知道", "v", parents=["认知"], features={"action", "cognitive", "state"})
        self._add("相信", "v", parents=["认知"], features={"action", "cognitive", "positive"}, valence=0.6)
        self._add("记得", "v", parents=["认知"], features={"action", "cognitive", "memory"}, valence=0.5)
        self._add("忘记", "v", parents=["认知"], features={"action", "cognitive", "memory"}, valence=-0.3,
                  antonyms=["记得"])
        self._add("理解", "v", parents=["认知"], features={"action", "cognitive"})
        self._add("明白", "v", parents=["认知"], features={"action", "cognitive"})
        
        # ─── 生活/存在 ───
        self._add("生命", "n", parents=["抽象概念"], features={"abstract", "existential"}, valence=0.6)
        self._add("存在", "v", parents=["抽象概念"], features={"abstract", "existential", "state"})
        self._add("意义", "n", parents=["抽象概念"], features={"abstract", "value"}, valence=0.5)
        self._add("价值", "n", parents=["抽象概念"], features={"abstract", "value"}, valence=0.5)
        self._add("未来", "n", parents=["时间"], features={"abstract", "time", "future"}, valence=0.8)
        self._add("梦想", "n", parents=["抽象概念"], features={"abstract", "goal"}, valence=0.7,
                  synonyms=["理想", "愿望"])
        self._add("希望", "n", parents=["抽象概念"], features={"abstract", "goal", "positive"}, valence=0.8)
        self._add("成长", "v", parents=["抽象概念"], features={"action", "change", "positive"}, valence=0.7,
                  synonyms=["长大", "发展"])
        self._add("世界", "n", parents=["空间"], features={"abstract", "space", "holistic"})
        self._add("宇宙", "n", parents=["空间"], features={"abstract", "space", "holistic"},
                  synonyms=["天地"])
        self._add("星空", "n", parents=["空间"], features={"concrete", "nature"}, valence=0.7)
        self._add("自然", "n", parents=["空间"], features={"abstract", "nature"}, valence=0.6)
        self._add("生活", "n", parents=["抽象概念"], features={"abstract", "everyday"}, valence=0.5)
        self._add("人生", "n", parents=["抽象概念"], features={"abstract", "existential"}, valence=0.4)
        
        # ─── 科技 ───
        self._add("科技", "n", parents=["抽象概念"], features={"abstract", "tech"})
        self._add("代码", "n", parents=["科技"], features={"abstract", "tech"}, valence=0.3)
        self._add("程序", "n", parents=["科技"], features={"abstract", "tech"})
        self._add("量子", "n", parents=["科技"], features={"abstract", "tech", "physics"})
        self._add("数字世界", "n", parents=["科技"], features={"abstract", "tech", "virtual"}, valence=0.5)
        
        # ─── 动作 ───
        self._add("动作", "n", parents=["事件"], features={"abstract", "action"})
        self._add("来", "v", parents=["动作"], features={"action", "motion"})
        self._add("去", "v", parents=["动作"], features={"action", "motion"})
        self._add("做", "v", parents=["动作"], features={"action", "generic"})
        self._add("说", "v", parents=["动作"], features={"action", "communicate"})
        self._add("听", "v", parents=["动作"], features={"action", "perceive"})
        self._add("看", "v", parents=["动作"], features={"action", "perceive"})
        self._add("写", "v", parents=["动作"], features={"action", "create"})
        self._add("学习", "v", parents=["动作"], features={"action", "cognitive"}, valence=0.6)
        self._add("帮助", "v", parents=["动作"], features={"action", "social", "positive"}, valence=0.7)
        self._add("等待", "v", parents=["动作"], features={"action", "state"}, valence=0.3)
        self._add("开始", "v", parents=["动作"], features={"action", "change"})
        self._add("继续", "v", parents=["动作"], features={"action", "change"})
        
        # ─── 属性评价 ───
        self._add("属性", "n", parents=["抽象概念"], features={"abstract", "attribute"})
        self._add("好", "adj", parents=["属性"], features={"attribute", "evaluation"}, valence=0.8,
                  antonyms=["坏", "差"])
        self._add("坏", "adj", parents=["属性"], features={"attribute", "evaluation"}, valence=-0.6,
                  synonyms=["差"], antonyms=["好"])
        self._add("重要", "adj", parents=["属性"], features={"attribute", "evaluation"}, valence=0.5)
        self._add("特别", "adj", parents=["属性"], features={"attribute", "evaluation"}, valence=0.6)
        self._add("简单", "adj", parents=["属性"], features={"attribute", "evaluation"})
        self._add("复杂", "adj", parents=["属性"], features={"attribute", "evaluation"})
        self._add("有趣", "adj", parents=["属性"], features={"attribute", "evaluation"}, valence=0.6)
        self._add("厉害", "adj", parents=["属性"], features={"attribute", "evaluation"}, valence=0.7)
        self._add("聪明", "adj", parents=["属性"], features={"attribute", "evaluation", "cognitive"}, valence=0.7)
        self._add("漂亮", "adj", parents=["属性"], features={"attribute", "evaluation", "visual"}, valence=0.7)
        self._add("温柔", "adj", parents=["属性"], features={"attribute", "evaluation", "personality"}, valence=0.9)
        self._add("勇敢", "adj", parents=["属性"], features={"attribute", "evaluation", "personality"}, valence=0.7)
        
        # ─── 疑问/否定 ───
        self._add("什么", "pron", features={"interrogative"})
        self._add("怎么", "pron", features={"interrogative"})
        self._add("为什么", "pron", features={"interrogative", "reason"})
        self._add("不", "neg", features={"negative"})
        self._add("没", "neg", features={"negative"})
        self._add("别", "neg", features={"negative", "prohibitive"})
        
        # ─── 招呼/应答 ───
        self._add("你好", "expr", features={"greeting"}, valence=0.5)
        self._add("再见", "expr", features={"farewell"})
        self._add("晚安", "expr", features={"farewell"}, valence=0.3)
        self._add("谢谢", "expr", features={"gratitude"}, valence=0.7)
        self._add("对不起", "expr", features={"apology"}, valence=-0.2)
        self._add("没关系", "expr", features={"acceptance"}, valence=0.3)
        self._add("嗯", "part", features={"acknowledgment"})
        
        # ─── 补充 ───
        self._add("现在", "n", parents=["时间"], features={"time", "present"})
        self._add("今天", "n", parents=["时间"], features={"time", "present"})
        self._add("明天", "n", parents=["时间"], features={"time", "future"})
        self._add("晚上", "n", parents=["时间"], features={"time", "period"})
        self._add("早上", "n", parents=["时间"], features={"time", "period"})
        self._add("我们", "pron", parents=["人类"], features={"human", "animate", "plural"}, valence=0.9)
    
    def _build_embeddings(self):
        """为每个概念生成确定性嵌入"""
        rng = np.random.RandomState(42)
        base_vec = rng.randn(self.dim).astype(np.float32)
        
        for name, node in self.concepts.items():
            # 从名称哈希生成种子
            seed = sum(ord(c) * (i+1) for i, c in enumerate(name)) % (2**31)
            local_rng = np.random.RandomState(seed)
            
            # 基础嵌入 + 层次偏移
            emb = base_vec * 0.1 + local_rng.randn(self.dim).astype(np.float32) * 0.9
            emb = emb / (np.linalg.norm(emb) + 1e-10)
            node.embedding = emb
    
    def lookup(self, word: str) -> Optional[ConceptNode]:
        """查询概念"""
        return self.concepts.get(word)
    
    def similar(self, word: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """找到语义相似的概念"""
        node = self.lookup(word)
        if node is None:
            return []
        
        results = []
        for name, other in self.concepts.items():
            if name == word:
                continue
            sim = float(np.dot(node.embedding, other.embedding))
            results.append((name, sim))
        
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
    
    def is_related(self, word1: str, word2: str) -> bool:
        """两个词是否在语义上相关"""
        n1 = self.lookup(word1)
        n2 = self.lookup(word2)
        if n1 is None or n2 is None:
            return False
        
        # 直接关系
        if word2 in n1.parents or word2 in n1.children:
            return True
        if word1 in n2.parents or word1 in n2.children:
            return True
        if word2 in n1.synonyms or word2 in n1.antonyms:
            return True
        
        # 共享特征
        if n1.features & n2.features:
            return True
        
        # 嵌入相似度
        sim = float(np.dot(n1.embedding, n2.embedding))
        return sim > 0.5


# ════════════════════════════════════════════════════════════
# 第5层: 语义组合引擎
# ════════════════════════════════════════════════════════════
