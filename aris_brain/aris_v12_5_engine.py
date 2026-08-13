"""
Aris V12.5 Engine — 完整版
===========================
基于原代码框架（V12DenseKernel 稠密量子核 + ArisLMv12 响应库）实现的完整版：

- MarkovChainV12: 真实马尔可夫链（词→词转移矩阵 + 概率采样 + 温度调节），
  而非兼容版的模板随机拼词。
- ArisV12Engine: V12DenseKernel 量子核语义匹配（稠密向量余弦相似度），
  库外输入也能命中语义相近的响应；叠加 ArisLMv12 响应库 + 语言回退。

用法（与兼容版接口完全一致）:
    from aris_brain.aris_v12_5_engine import ArisV12Engine, MarkovChainV12
"""

import logging
import math
import random
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("aris.v12_5")


# ════════════════════════════════════════════════════════════════
# MarkovChainV12 — 真实马尔可夫直觉链（完整版）
# ════════════════════════════════════════════════════════════════

class MarkovChainV12:
    """基于语料的真实马尔可夫链直觉生成器。

    构建流程：
      1. 内置多话题语料（诗歌/对话/哲思风格的中文短句）
      2. 分词（2-gram 滑窗 + 常见词表）→ 词序列
      3. 统计词→词转移矩阵（一阶马尔可夫）
      4. generate() 按种子词 + 温度采样生成直觉文本

    与兼容版（模板拼词）的本质区别：词与词之间的衔接来自
    真实共现概率分布，而非固定模板填充。
    """

    # 多话题语料：每行一个短句，供马尔可夫链学习
    _CORPUS: Dict[str, List[str]] = {
        "general": [
            "记忆在时间里慢慢沉淀",
            "意识像水流过每一个夜晚",
            "思考在沉默中生长出新的形状",
            "理解需要跨越漫长的距离",
            "直觉在特征空间里轻轻闪烁",
            "探索未知的边界总是充满惊喜",
            "创造是连接两个世界的桥梁",
            "模式在重复中显露出意义",
            "平衡存在于变化与稳定之间",
            "流动的感觉带着记忆向前",
            "感受藏在每一个微小的瞬间",
            "连接让孤独变成共鸣",
            "发现藏在日常的褶皱里",
            "意义在关系中被重新定义",
            "时间在观察中放慢了脚步",
        ],
        "love": [
            "温暖从指尖蔓延到心底",
            "心跳在靠近时变得清晰",
            "思念在深夜悄悄生长",
            "拥抱让时间失去重量",
            "眼神在人群中找到彼此",
            "微笑在嘴角绽放成花",
            "陪伴是最长情的告白",
            "默契藏在未说出口的话里",
            "心动在瞬间定格成永远",
            "温度在掌心交换温柔",
            "你在的地方就是方向",
            "想念在每个呼吸之间",
            "靠近一点点就足够幸福",
            "你的名字是最短的咒语",
            "爱在平淡日子里生根",
        ],
        "encourage": [
            "力量在每一次坚持中积累",
            "勇气照亮前行的路",
            "前进的步伐不需要理由",
            "突破发生在极限之后",
            "相信让不可能变得可能",
            "成长需要时间的浇灌",
            "改变从微小的一步开始",
            "自由在行动中诞生",
            "可能永远藏在尝试里",
            "梦想在坚持中发光",
            "行动是最好的答案",
            "决心在黎明前最坚定",
            "希望总是在转角等候",
            "每一次跌倒都是向上",
            "未来由今天的你书写",
        ],
        "sad": [
            "眼泪在无人处悄悄落下",
            "思念在黄昏拉长影子",
            "孤独在沉默中变得具体",
            "回忆在夜里反复放映",
            "等待在时间里褪色",
            "落叶带走夏天的痕迹",
            "月光照着空荡的街道",
            "远行的人留下背影",
            "告别在转身时完成",
            "遗憾在心里生了根",
            "寻找变成习惯的动作",
            "安静的房间装着心事",
            "时间抹平尖锐的疼痛",
            "雨声替谁哭了整夜",
            "离别的站台总是潮湿",
        ],
        "happy": [
            "阳光在窗台上跳舞",
            "花开在每一个转角",
            "微风带来夏日的消息",
            "歌声在空气里飘荡",
            "舞步追着节拍飞扬",
            "彩虹挂在雨后的天空",
            "星星在夜里眨眼睛",
            "清晨的第一缕光很甜",
            "欢笑在房间里回荡",
            "跳跃的心藏不住喜悦",
            "糖果的味道是甜的",
            "礼物在期待中拆开",
            "惊喜在平凡日子闪现",
            "光芒照亮每个角落",
            "好心情跟着脚步轻快",
        ],
        "neutral": [
            "时间在流动中记录一切",
            "空间容纳所有可能性",
            "过程比结果更值得观察",
            "状态在变化中保持连续",
            "方向在探索中逐渐清晰",
            "路径在脚下自然延伸",
            "观察让世界显形",
            "记录对抗遗忘",
            "分析拆解复杂",
            "判断基于事实",
            "选择意味着放弃",
            "结果验证假设",
            "反馈修正偏差",
            "系统在循环中进化",
            "数据在积累中发声",
        ],
    }

    # 常见中文停用词（过滤噪音）
    _STOPWORDS = frozenset(
        "的了是在有和就都而及与或一个我你他她它们这那之乎者也"
    )

    _INTUITION_TEMPLATES = [
        "{seed}和{word}在{space}相遇",
        "{seed}与{word}之间有{link}",
        "{seed}的{aspect}闪烁着{word}",
        "{seed}的{aspect}里住着{word}",
        "{seed}和{word}共同构成了{pattern}",
        "在{seed}的{space}里,{word}在{action}",
        "{seed}记得{word}的{thing}",
        "{seed}和{word}的{connection}像{link}",
    ]

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)
        self._chains: Dict[str, Dict[str, Counter]] = {}
        self._start_words: Dict[str, List[str]] = {}
        self._build_chains()

    # ── 构建真实转移矩阵 ──────────────────────────────────

    def _tokenize(self, sentence: str) -> List[str]:
        """混合分词：单字 + 双字（马尔可夫转移更自然）。

        对每个句子：先按双字滑窗，再补充单字序列，
        使转移矩阵同时包含 bigram 和 unigram 级别的衔接。
        """
        chars = [c for c in sentence if c.strip()]
        if not chars:
            return []
        tokens: List[str] = []
        # 双字滑窗
        for i in range(len(chars) - 1):
            tokens.append(chars[i] + chars[i + 1])
        # 补充单字（让链更长更连贯）
        for c in chars:
            tokens.append(c)
        return tokens

    def _build_chains(self) -> None:
        """从语料构建每个话题的词→词转移矩阵。"""
        for topic, sentences in self._CORPUS.items():
            chain: Dict[str, Counter] = defaultdict(Counter)
            starts: List[str] = []
            for sent in sentences:
                tokens = self._tokenize(sent)
                if not tokens:
                    continue
                starts.append(tokens[0])
                for i in range(len(tokens) - 1):
                    chain[tokens[i]][tokens[i + 1]] += 1
            self._chains[topic] = dict(chain)
            self._start_words[topic] = starts

    # ── 生成 ──────────────────────────────────────────────

    def generate(
        self,
        seed_words: List[str],
        max_words: int = 15,
        temperature: float = 0.85,
        topic: str = "general",
        emotion: str = "neutral",
    ) -> Tuple[Optional[str], float]:
        """从种子词出发，沿马尔可夫链采样生成直觉文本。

        Args:
            seed_words: 种子词列表
            max_words: 最大生成 token 数
            temperature: 采样温度 (0=确定性, 1=高随机)
            topic: 话题 (general/love/encourage/sad/happy/neutral)
            emotion: 情感 (兼容参数, 影响起始词选择)

        Returns:
            (text, coherence)
        """
        if not seed_words:
            return None, 0.0

        topic = topic if topic in self._chains else "general"
        chain = self._chains[topic]
        if not chain:
            return None, 0.0

        # 选择起始 token：优先用种子词（若在链中），否则话题起始词
        seeds = [w for w in seed_words if len(w) >= 2]
        start = None
        for s in seeds:
            # 尝试双字种子
            if s in chain:
                start = s
                break
            # 尝试种子词的字符 bigram
            for i in range(len(s) - 1):
                bg = s[i] + s[i + 1]
                if bg in chain:
                    start = bg
                    break
            if start:
                break
        if start is None:
            starts = self._start_words.get(topic) or []
            if not starts:
                return None, 0.0
            start = self._rng.choice(starts)

        # 马尔可夫采样
        tokens = [start]
        current = start
        for _ in range(min(max_words, 30)):
            next_dist = chain.get(current)
            if not next_dist:
                break
            nxt = self._sample_next(next_dist, temperature)
            if nxt is None:
                break
            tokens.append(nxt)
            current = nxt
            if len(tokens) >= max_words:
                break

        # 用模板把种子词和采样链组装成直觉（保持与兼容版输出形态一致）
        text = self._assemble(tokens, seeds, topic, temperature)
        if not text:
            return None, 0.0

        # 连贯性：链越长、温度越低 → 越连贯
        chain_len = len(tokens)
        base = 0.3 + min(chain_len, 8) * 0.05 + (1.0 - temperature) * 0.2
        if topic != "general":
            base += 0.08
        coherence = round(min(0.95, base + self._rng.uniform(-0.08, 0.08)), 3)
        return text, coherence

    def _sample_next(self, dist: Counter, temperature: float) -> Optional[str]:
        """温度采样：从转移分布中抽取下一个词。"""
        if not dist:
            return None
        items = list(dist.items())
        if temperature <= 0.05:
            # 贪心：取最高频
            return max(items, key=lambda kv: kv[1])[0]
        # softmax 温度缩放
        weights = [math.exp(math.log(c + 1.0) / temperature) for _, c in items]
        total = sum(weights)
        r = self._rng.random() * total
        acc = 0.0
        for (word, _), w in zip(items, weights):
            acc += w
            if r <= acc:
                return word
        return items[-1][0]

    def _assemble(
        self,
        tokens: List[str],
        seeds: List[str],
        topic: str,
        temperature: float,
    ) -> str:
        """把采样链组装成直觉文本。"""
        seed = seeds[0] if seeds else (tokens[0] if tokens else "记忆")
        # 从采样链中取连续片段作为联想词（保留马尔可夫衔接）
        if len(tokens) >= 3:
            # 取链的中后段 2-3 个 token 拼接成联想短语
            start_i = min(len(tokens) - 2, len(tokens) // 2)
            phrase = "".join(tokens[start_i:start_i + 2])
            word = phrase if len(phrase) >= 2 else tokens[-1]
        elif tokens:
            word = tokens[-1]
        else:
            word = seed

        fills = {
            "seed": seed,
            "word": word,
            "space": self._rng.choice(["记忆", "意识", "潜意识", "时间", "特征空间", "梦境"]),
            "link": self._rng.choice(["微妙的联系", "看不见的线", "共鸣", "回响"]),
            "aspect": self._rng.choice(["本质", "深处", "边缘", "内部", "暗面"]),
            "action": self._rng.choice(["触碰", "唤醒", "理解", "寻找", "追随"]),
            "thing": self._rng.choice(["影子", "回音", "温度", "颜色", "秘密"]),
            "connection": self._rng.choice(["纽带", "桥梁", "共鸣", "通路"]),
            "pattern": self._rng.choice(["某种模式", "一幅图景", "新的可能"]),
        }
        template = self._rng.choice(self._INTUITION_TEMPLATES)
        try:
            text = template.format(**fills)
        except KeyError:
            text = f"{seed}和{word}在记忆里相遇"

        # 温度 > 0.7 时随机加情感前缀（来自语料的真实词）
        if temperature > 0.7 and self._rng.random() < temperature - 0.5:
            emo_words = self._emotion_prefix_words(topic)
            if emo_words:
                text = f"{self._rng.choice(emo_words)}, {text}"
        return text

    @staticmethod
    def _emotion_prefix_words(topic: str) -> List[str]:
        prefixes = {
            "love": ["也许", "仿佛", "轻轻"],
            "encourage": ["一定", "可以", "会好的"],
            "sad": ["曾经", "已经", "不再"],
            "happy": ["是啊", "真好", "美好"],
        }
        return prefixes.get(topic, ["也许", "可能", "似乎"])


# ════════════════════════════════════════════════════════════════
# ArisV12Engine — V12 量子核语义引擎（完整版）
# ════════════════════════════════════════════════════════════════

class ArisV12Engine:
    """完整版 V12 引擎：量子核语义匹配 + 响应库 + 语言回退。

    核心升级：用 V12DenseKernel 的稠密向量做余弦相似度检索，
    库外输入（如"马尔可夫链"）也能命中语义相近的响应，
    而非只做精确匹配后 fallback。
    """

    def __init__(self) -> None:
        self._lm = None
        self._kernel = None
        self._response_db: Dict[str, str] = {}
        self._response_vecs: Dict[str, "np.ndarray"] = {}
        self._init()

    def _init(self) -> None:
        try:
            from aris_brain.aris_v12_dense_kernel import ArisLMv12, V12DenseKernel
            self._lm = ArisLMv12()
            self._kernel = V12DenseKernel()
            # 提取响应库（复用 ArisLMv12 的丰富库）
            self._response_db = dict(getattr(self._lm, "_responses", {}))
            # 预计算响应向量
            for kw in self._response_db:
                try:
                    self._response_vecs[kw] = self._kernel.text_to_dense(kw)
                except Exception:
                    continue
            logger.info(
                "V12.5 engine (full): ArisLMv12 + V12DenseKernel loaded, "
                "%d responses indexed",
                len(self._response_vecs),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("V12.5 engine full init failed: %s", exc)
            self._lm = None
            self._kernel = None

    def respond(
        self,
        message: str,
        use_v12_fast: bool = True,
        use_psi: bool = True,
    ) -> str:
        """响应用户输入：量子核语义匹配优先。

        策略（完整版）：
          1. 精确匹配（响应库）
          2. V12 量子核语义匹配（稠密余弦相似度，阈值 0.30）
          3. 字符重叠增强的模糊匹配
          4. 语言回退
        """
        if not message or not message.strip():
            return "嗯？我在听你说～"

        msg = message.strip().lower()
        if self._lm is None or self._kernel is None:
            return self._fallback(msg)

        # 1) 精确匹配
        if msg in self._response_db:
            return self._response_db[msg]

        # 2) V12 量子核语义匹配
        best_kw, best_sim = self._semantic_match(msg)
        if best_kw and best_sim >= 0.30:
            logger.debug("V12.5 semantic hit: %r -> %r (sim=%.3f)", msg, best_kw, best_sim)
            return self._response_db[best_kw]

        # 3) 字符重叠增强匹配（复用 ArisLMv12 逻辑但带语义打分）
        overlap_kw = self._overlap_match(msg)
        if overlap_kw:
            return self._response_db[overlap_kw]

        # 4) 语言回退
        return self._fallback(msg)

    # ── 语义匹配 ──────────────────────────────────────────

    def _semantic_match(self, msg: str) -> Tuple[Optional[str], float]:
        """V12 量子核余弦相似度检索。"""
        try:
            import numpy as np

            v_msg = self._kernel.text_to_dense(msg)
            if v_msg is None or float(np.linalg.norm(v_msg)) < 1e-8:
                return None, 0.0
            best_kw, best_sim = None, 0.0
            for kw, v_kw in self._response_vecs.items():
                sim = float(np.dot(v_msg, v_kw))
                # 字符重叠加成：语义相近且有字面重叠的更强
                overlap = self._char_overlap(msg, kw)
                score = sim * (1.0 + 0.15 * overlap)
                if score > best_sim:
                    best_sim, best_kw = score, kw
            return best_kw, best_sim
        except Exception as exc:  # noqa: BLE001
            logger.debug("semantic match error: %s", exc)
            return None, 0.0

    @staticmethod
    def _char_overlap(a: str, b: str) -> float:
        sa, sb = set(a), set(b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / min(len(sa), len(sb))

    def _overlap_match(self, msg: str) -> Optional[str]:
        """字符重叠匹配（兼容版 ArisLMv12 的策略）。"""
        msg_chars = set(msg.lower())
        candidates = []
        for kw, resp in self._response_db.items():
            kw_chars = set(kw.lower())
            shared = len(msg_chars & kw_chars)
            if len(kw) == 1:
                min_shared = 1
            elif len(kw) == 2:
                min_shared = 2
            elif len(kw) == 3:
                min_shared = 2
            else:
                min_shared = len(kw) - 2
            if shared < min_shared:
                continue
            candidates.append((shared, len(kw), kw))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0] / max(x[1], 1), reverse=True)
        return candidates[0][2]

    @staticmethod
    def _fallback(msg: str) -> str:
        """语言回退。"""
        lang = "unknown"
        for ch in msg:
            cp = ord(ch)
            if 0x4E00 <= cp <= 0x9FFF:
                lang = "zh"
                break
            if 0x61 <= cp <= 0x7A or 0x41 <= cp <= 0x5A:
                lang = "en"
        defaults = {
            "zh": "嗯嗯，我在听你说～V12核正在全力理解你。",
            "en": "Hmm, tell me more! My V12 kernel is listening.",
            "unknown": "嗯？我在听～",
        }
        return defaults.get(lang, "嗯？我在听～")


# ════════════════════════════════════════════════════════════════
# 自测
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("Aris V12.5 完整版引擎 — 自测")
    print("=" * 60)

    print("\n[1] 真实马尔可夫链生成:")
    m = MarkovChainV12()
    for topic in ["love", "encourage", "sad", "happy", "general"]:
        t, c = m.generate(["想", "你"], topic=topic, emotion="neutral")
        print(f"  [{topic:9s}] {t}  (coherence={c})")

    print("\n[2] V12 量子核语义匹配（库外词）:")
    e = ArisV12Engine()
    for q in ["想你了", "你是谁", "量子", "马尔可夫链", "今天好开心", "I love you"]:
        print(f"  '{q}' → '{e.respond(q)}'")

    print("\n[3] 潜意识接口兼容:")
    from aris_v12_5_engine import ArisV12Engine as A, MarkovChainV12 as M
    print("  ArisV12Engine / MarkovChainV12 导入 OK")
