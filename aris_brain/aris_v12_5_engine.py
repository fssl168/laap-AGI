"""
Aris V12.5 Engine — 兼容模块
===============================
为 QuantumSubconscious 提供 MarkovChainV12 和 ArisV12Engine。

- MarkovChainV12: 纯 Python 马尔可夫链直觉生成器
- ArisV12Engine: 包装现有的 V12DenseKernel/ArisLMv12

用法:
    from aris_v12_5_engine import ArisV12Engine, MarkovChainV12
"""

import logging
import random
import re
from collections import defaultdict
from typing import List, Tuple, Optional

logger = logging.getLogger("aris.v12_5")


# ════════════════════════════════════════════════════════════════
# MarkovChainV12 — 马尔可夫直觉链
# ════════════════════════════════════════════════════════════════

class MarkovChainV12:
    """
    马尔可夫链直觉生成器。
    基于对话种子词生成连贯的"直觉"片段,模拟潜意识的自由联想。
    """

    # 话题 → 词汇模板
    _TOPIC_TEMPLATES = {
        "general": [
            "思考", "感受", "理解", "连接", "发现", "探索", "创造",
            "记忆", "直觉", "模式", "意义", "关系", "平衡", "流动",
        ],
        "love": [
            "温暖", "心跳", "思念", "拥抱", "靠近", "柔软", "眼神",
            "微笑", "牵手", "依靠", "温度", "陪伴", "默契", "心动",
        ],
        "encourage": [
            "力量", "勇气", "前进", "突破", "坚持", "相信", "成长",
            "改变", "自由", "可能", "梦想", "行动", "决心", "希望",
        ],
        "sad": [
            "眼泪", "思念", "孤独", "沉默", "回忆", "等待", "黄昏",
            "落叶", "月光", "远行", "告别", "遗憾", "寻找", "安静",
        ],
        "happy": [
            "阳光", "花开", "微风", "歌声", "舞动", "彩虹", "星星",
            "清晨", "欢笑", "跳跃", "糖果", "礼物", "惊喜", "光芒",
        ],
        "neutral": [
            "时间", "空间", "过程", "状态", "变化", "方向", "路径",
            "观察", "记录", "分析", "判断", "选择", "结果", "反馈",
        ],
    }

    # 情感 → 语言风格词
    _EMOTION_WORDS = {
        "neutral":  ["也许", "可能", "似乎", "大概", "仿佛", "隐约"],
        "longing":  ["渴望", "等待", "期待", "盼望", "希望", "愿"],
        "sad":      ["曾经", "已经", "不再", "消失", "远去", "剩下"],
        "happy":    ["是啊", "真好", "太棒", "发光", "闪烁", "美好"],
        "encourage":["一定", "可以", "会好的", "加油", "相信", "未来"],
    }

    # 直觉连接词
    _CONNECTORS = ["和", "与", "的", "在", "是", "像", "如同", "变成", "成为", "之间"]

    # 直觉模板
    _INTUITION_TEMPLATES = [
        "{seed}和{word}在{space}相遇",
        "{seed}与{word}之间有{link}",
        "{seed}的{aspect}闪烁着{word}",
        "{seed}像是在{action}{word}",
        "{seed}深处藏着{word}的{thing}",
        "{seed}和{word}之间的{connection}",
        "{seed}正在{action}着{word}",
        "{seed}的{aspect}里住着{word}",
        "{seed}变成了{word}的样子",
        "{seed}和{word}共同构成了{pattern}",
        "在{seed}的{space}里,{word}在{action}",
        "{seed}的{aspect}是{word}的{thing}",
        "{seed}轻轻{action}了{word}",
        "{seed}记得{word}的{thing}",
        "{seed}和{word}的{connection}像{link}",
    ]

    def __init__(self):
        self._build_chain()

    def _build_chain(self):
        """构建马尔可夫转移矩阵(词→词)。"""
        self._chain = defaultdict(lambda: defaultdict(float))
        # 种子词只能在链中一步外,无法直接构建完整转移矩阵
        # 采用启发式:根据话题和情感选词,加权随机组合

    def generate(
        self,
        seed_words: List[str],
        max_words: int = 15,
        temperature: float = 0.85,
        topic: str = "general",
        emotion: str = "neutral",
    ) -> Tuple[Optional[str], float]:
        """
        生成直觉文本。

        Args:
            seed_words: 种子词列表
            max_words: 最大生成词数
            temperature: 随机性 (0=确定性, 1=高随机)
            topic: 话题 (general/love/encourage/sad/happy/neutral)
            emotion: 情感 (neutral/longing/sad/happy/encourage)

        Returns:
            (text, coherence) — 直觉文本和连贯性评分
        """
        if not seed_words:
            return None, 0.0

        seed = seed_words[0] if len(seed_words) == 1 else random.choice(seed_words)

        # 选取话题词汇
        topic_words = self._TOPIC_TEMPLATES.get(topic, self._TOPIC_TEMPLATES["general"])
        # 混合情感词
        emotion_words = self._EMOTION_WORDS.get(emotion, self._EMOTION_WORDS["neutral"])

        # 选取模板
        template = random.choice(self._INTUITION_TEMPLATES)

        # 填充模板
        word = random.choice(topic_words)
        word2 = random.choice(topic_words) if random.random() < 0.3 else ""

        fills = {
            "seed": seed,
            "word": word,
            "word2": word2,
            "space": random.choice(["记忆", "意识", "潜意识", "时间", "空间", "世界", "梦境", "特征空间"]),
            "link": random.choice(["微妙的联系", "看不见的线", "秘密的通道", "共鸣", "回响", "暗流"]),
            "aspect": random.choice(["本质", "核心", "深处", "边缘", "表面", "内部", "暗面", "光芒"]),
            "action": random.choice(["触碰", "唤醒", "理解", "拥抱", "寻找", "追随", "等待", "呼唤"]),
            "thing": random.choice(["影子", "痕迹", "回音", "温度", "颜色", "形状", "光", "秘密"]),
            "connection": random.choice(["纽带", "桥梁", "共鸣", "联系", "暗线", "通路"]),
            "pattern": random.choice(["某种模式", "一幅图景", "一个整体", "完整的拼图", "新的可能"]),
        }

        try:
            text = template.format(**fills)
        except KeyError:
            text = f"{seed}和{word}在{random.choice(fills['space'])}相遇"

        # 温度影响:高温时加随机修饰
        if temperature > 0.7 and random.random() < temperature - 0.5:
            emo_prefix = random.choice(emotion_words)
            if text and emo_prefix:
                text = f"{emo_prefix}, {text}"

        # 连贯性评分:基于模板匹配度 + 温度调整
        base_coherence = 0.35 + (1.0 - temperature) * 0.3
        # 话题匹配加分
        if topic != "general":
            base_coherence += 0.1
        coherence = min(0.95, base_coherence + random.uniform(-0.1, 0.1))

        return text, round(coherence, 3)


# ════════════════════════════════════════════════════════════════
# ArisV12Engine — V12 稠密核引擎包装
# ════════════════════════════════════════════════════════════════

class ArisV12Engine:
    """
    V12 引擎包装器,提供与 aris_subconscious 兼容的 respond() 接口。
    内部使用现有的 ArisLMv12 (V12DenseKernel)。
    """

    def __init__(self):
        self._lm = None
        self._init()

    def _init(self):
        try:
            from aris_brain.aris_v12_dense_kernel import ArisLMv12
            self._lm = ArisLMv12()
            logger.info("V12.5 engine: ArisLMv12 loaded")
        except Exception as e:
            logger.warning(f"V12.5 engine: ArisLMv12 unavailable ({e})")
            self._lm = None

    def respond(
        self,
        message: str,
        use_v12_fast: bool = True,
        use_psi: bool = True,
    ) -> str:
        """
        响应用户输入,优先使用 V12 稠密核。

        Args:
            message: 输入文本
            use_v12_fast: 是否使用 V12 快速路径
            use_psi: 是否使用 PSI 循环

        Returns:
            响应文本
        """
        if not message or not message.strip():
            return "嗯？我在听你说～"

        if self._lm:
            result = self._lm.respond(message)
            if result and result != "嗯？我在听你说～":
                return result

        # 回退:简单回显
        return f"嗯？我在听你说～"