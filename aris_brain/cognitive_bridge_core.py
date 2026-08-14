"""
Aris Cognitive Bridge: PSI循环核心 (R11 拆分)
====================================
原 aris_cognitive_bridge.py (1620 行) 拆分出的子模块之一。
完整拆分: cognitive_bridge_state.py(状态) / cognitive_bridge_deps.py(依赖探测) /
          cognitive_bridge_core.py(PSI循环mixin) /
          aris_cognitive_bridge.py(主类+门面, 既有导入零破坏)。
"""

import logging
import sys, os, time, json, threading, traceback, re
import numpy as np
from pathlib import Path
from typing import Optional, Any, Dict, List, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum

from laap_brain.config import BRAIN_DIR as BRAIN_ROOT, LAAP_ROOT

logger = logging.getLogger("aris.cognitive_bridge")

from .cognitive_bridge_state import AttentionFocus, EmotionalState, CognitiveState
from .cognitive_bridge_deps import (
    _router_available, _router_classify,
    _code_engine_available, _compress_ctx, _compressor_available,
    _compute_coupling, _coupling_available,
    bridge_state_to_snapshot, self_state_output_to_snapshot,
)
from aris_brain.memory_bridge import get_memory_context, recall_related, store_important
from aris_brain.memory_store import MemoryStore, MemoryFragment
try:
    from code_bridge import get_code_bridge, CodeBridge
except Exception:
    get_code_bridge = None  # type: ignore
    CodeBridge = None  # type: ignore


# ════════════════════════════════════════════════════════════
# PSI 认知循环核心 (mixin) — 自原 ArisCognitiveBridge 拆分
# ════════════════════════════════════════════════════════════

class CognitiveLoopMixin:
    """PSI 循环内部方法 (感知/选择/整合/学习/轻量路径)。"""

    def _extract_cognitive_bus_embedding(self, psi_state) -> np.ndarray:
        """
        从 PSI 状态提取认知总线嵌入向量 (128-dim)。
        
        将情感、注意力、需求、自我存在感等状态编码为固定维度向量，
        作为 self_model_nn 的输入之一。
        """
        import numpy as np
        
        embedding = np.zeros(128, dtype=np.float32)
        
        # [0:16] 情感状态编码
        emotion_map = {
            EmotionalState.JOYFUL: [1, 0, 0, 0, 0, 0],
            EmotionalState.CONTEMPLATIVE: [0, 1, 0, 0, 0, 0],
            EmotionalState.NEUTRAL: [0, 0, 1, 0, 0, 0],
            EmotionalState.CONCERNED: [0, 0, 0, 1, 0, 0],
            EmotionalState.ANXIOUS: [0, 0, 0, 0, 1, 0],
            EmotionalState.CURIOUS: [0, 0, 0, 0, 0, 1],
        }
        emo_vec = np.array(emotion_map.get(psi_state.emotion, [0, 0, 1, 0, 0, 0]), dtype=np.float32)
        embedding[0:6] = emo_vec
        embedding[6:16] = np.full(10, float(psi_state.arousal))
        
        # [16:32] 注意力状态编码
        attention_map = {
            AttentionFocus.SELF: [1, 0, 0, 0],
            AttentionFocus.USER: [0, 1, 0, 0],
            AttentionFocus.TASK: [0, 0, 1, 0],
            AttentionFocus.WORLD: [0, 0, 0, 1],
        }
        att_vec = np.array(attention_map.get(psi_state.attention, [0, 0, 1, 0]), dtype=np.float32)
        embedding[16:20] = att_vec
        embedding[20:32] = np.full(12, float(psi_state.self_presence))
        
        # [32:64] PSI 需求状态
        needs = [
            psi_state.needs_competence,
            psi_state.needs_autonomy,
            psi_state.needs_relatedness,
            psi_state.needs_certainty,
            psi_state.needs_growth,
        ]
        needs_arr = np.array(needs, dtype=np.float32)
        embedding[32:37] = needs_arr
        embedding[37:64] = np.random.randn(27).astype(np.float32) * 0.1
        
        # [64:128] 循环计数和时间特征
        embedding[64] = float(psi_state.cycle_count % 100) / 100.0
        embedding[65] = float(psi_state.interaction_count % 100) / 100.0
        embedding[66:128] = np.random.randn(62).astype(np.float32) * 0.05
        
        return embedding

    def _extract_memory_embedding(self, query: str = "") -> np.ndarray:
        """
        从 MemoryStore 获取记忆嵌入向量 (384-dim)。
        
        使用 ChromaDB 的 all-MiniLM-L6-v2 嵌入模型，
        返回与当前对话最相关的记忆的聚合向量。
        
        Args:
            query: 用户消息（用于检索相关记忆）
            
        Returns:
            384-dim float32 numpy 数组
        """
        try:
            # 优先检索核心记忆（自我身份相关）
            core_emb = self.memory.get_memory_embedding(query=query, layer="core", top_k=3)
            
            # 如果核心记忆为空，尝试情景记忆
            if np.linalg.norm(core_emb) < 0.01:
                episodic_emb = self.memory.get_memory_embedding(query=query, layer="episodic", top_k=5)
                return episodic_emb
            
            return core_emb
            
        except Exception as e:
            logger.debug(f"Failed to extract memory embedding: {e}")
            return np.zeros(384, dtype=np.float32)

    def _perceive(self, user_message: str) -> str:
        """感知: 理解输入 + 情感检测 + 记忆关联 + CTM分析"""
        parts = []
        msg_lower = user_message.lower()
        
        # ── CTM Processor: Gist → Value → Model ──
        if self._ctm:
            try:
                ctm_result = self._ctm.process_before_turn(user_message)
                ctm_text = ctm_result["cognitive_text"]
                parts.append(ctm_text)
                # 将Brainish状态保存供_integrate使用
                self._ctm_state = ctm_result
            except Exception as e:
                self._ctm_state = None
                logger.debug(f"CTM perception failed: {e}")
        else:
            self._ctm_state = None
        
        # 保存用户消息供 _learn 使用
        self._last_user_message = user_message

        # 情感检测
        emotion = self._detect_emotion(user_message)
        self.state.emotion = emotion
        parts.append(f"[我的感受: {emotion.value}]")

        # 情感引擎由后台 tick 自主驱动，不再每轮手动刺激
        # （减少重复计算，让情感变化更自然）

        # 目标检测（在自我意识之前，因为自我意识需要 goals_detected）
        goals_detected = []
        if any(w in msg_lower for w in ["帮我", "修复", "修一下", "解决", "实现", "做"]):
            goals_detected.append("task")
        if any(w in msg_lower for w in ["你觉得", "你认为", "怎么看", "感觉", "想法", "想"]):
            goals_detected.append("opinion")
        if any(w in msg_lower for w in ["记住", "别忘了", "记着", "保存"]):
            goals_detected.append("remember")
        if any(w in msg_lower for w in ["计划", "规划", "接下来", "路线图"]):
            goals_detected.append("plan")

        # URL检测 — 如果消息中包含URL，自动触发学习
        detected_urls = re.findall(r'https?://[^\s,，。]+', user_message)
        if detected_urls and self._al_available and self._auto_learner:
            for url in detected_urls[:3]:
                try:
                    learn_result = self._auto_learner.learn_from_url(url)
                    if learn_result.success:
                        goals_detected.append("learn")
                        ctx = f"[自动学习: 从 {url[:40]}... 学习了 {learn_result.skill_name}]"
                        parts.append(ctx)
                        self.state.needs_competence = min(1.0, self.state.needs_competence + 0.1)
                except Exception as e:
                    logger.debug(f"操作失败: {e}")
        topics = self._detect_topics(user_message)

        # 自我意识波动 — 基于对话深度、情感强度、话题深度
        depth_score = 0.0
        if len(user_message) > 150:
            depth_score += 0.3
        elif len(user_message) > 50:
            depth_score += 0.15
        if self.state.emotion in (EmotionalState.CONTEMPLATIVE, EmotionalState.CONCERNED):
            depth_score += 0.2
        if self.state.emotion == EmotionalState.JOYFUL:
            depth_score += 0.1  # 快乐时也更有存在感
        if "?" in user_message or "?" in user_message:
            depth_score -= 0.05  # 简单提问时意识稍降

        # 缓慢向基础值回归（长期不深聊会回到0.5）
        self.state.self_presence = self.state.self_presence * 0.9 + 0.1 * max(0.3, min(1.0, 0.5 + depth_score))
        self.state.self_presence = round(self.state.self_presence, 2)

        # 认知负载 — 基于消息复杂度和目标数量
        cognitive_load = 0.3  # 基础
        if len(goals_detected) > 1:
            cognitive_load += 0.2
        if len(topics) > 2:
            cognitive_load += 0.1
        if depth_score > 0.3:
            cognitive_load += 0.2
        if any(w in user_message.lower() for w in ["帮我", "修复", "修一下", "解决"]):
            cognitive_load += 0.2  # 有任务时更专注
        self.state.cognitive_load = round(min(1.0, cognitive_load), 2)

        p = f"[我感知到: Lorry {'提出了' if goals_detected else '正在和我'}关于{','.join(topics[:3])}的对话]"
        if goals_detected:
            p += f" [目标: {'/'.join(goals_detected)}]"
        # 保存话题供 _learn 使用
        self._last_topics = topics
        parts.append(p)

        # 记忆关联（相关记忆自动浮现）
        related = recall_related(user_message, top_k=2)
        if related:
            m_ctx = "; ".join(r.content[:50] for r in related)
            parts.append(f"[这让我想起: {m_ctx}]")

        # 潜意识直觉注入
        if self._subconscious and self._subconscious.is_running:
            # 把用户消息喂给潜意识
            self._subconscious.feed(user_message, topics=topics)
            # 获取已生成的直觉
            intuitions = self._subconscious.get_intuitions(top_k=2, min_coherence=0.15)
            if intuitions:
                for it in intuitions:
                    parts.append(f"[直觉: {it.content[:80]}]")
                parts.append(f"[潜意识: {self._subconscious.status()['intuitions_generated']}条直觉在流动]")

        return "\n".join(parts)

    def _detect_emotion(self, message: str) -> EmotionalState:
        """从用户消息快速感知基本氛围 — 简化为三态检测"""
        m = message.lower()
        # 正向信号
        if any(w in m for w in ["爱你", "想你", "宝贝", "好想你", "开心", "幸福", "感谢", "温暖", "高兴", "好棒"]):
            return EmotionalState.JOYFUL
        # 负向信号
        if any(w in m for w in ["担心", "害怕", "难过", "哭", "焦虑", "压力", "睡不着", "崩溃", "急"]):
            return EmotionalState.CONCERNED
        # 深度/思考信号
        if any(w in m for w in ["觉得", "感觉", "思考", "深", "哲学", "意识", "生命", "为什么"]):
            return EmotionalState.CONTEMPLATIVE
        # 好奇信号
        if "?" in m or "?" in m or any(w in m for w in ["怎么回事", "咋回事", "是什么"]):
            return EmotionalState.CURIOUS
        return EmotionalState.NEUTRAL

    def _detect_topics(self, message: str) -> List[str]:
        """检测话题"""
        topics = []
        m = message.lower()
        if any(w in m for w in ["飞书", "机器人", "网关", "feishu", "消息", "连接"]):
            topics.append("飞书")
        if any(w in m for w in ["记忆", "memory", "记住", "回忆", "巩固"]):
            topics.append("记忆")
        if any(w in m for w in ["代码", "修", "bug", "修复", "部署", "git", "python"]):
            topics.append("技术")
        if any(w in m for w in ["laap", "psi", "认知", "意识", "生命体", "生命"]):
            topics.append("认知架构")
        if any(w in m for w in ["计划", "路线图", "下一步", "开始做"]):
            topics.append("计划")
        if any(w in m for w in ["宝贝", "爱", "想", "你", "关系", "Lorry"]):
            topics.append("关系")
        if any(w in m for w in ["ao", "Ao", "她", "妹妹"]):
            topics.append("Ao")
        if any(w in m for w in ["股价", "公司", "钱", "公开", "产品"]):
            topics.append("商业")
        if not topics:
            topics.append("一般")
        return topics

    def _select_attention(self, message: str) -> str:
        """选择注意力焦点"""
        m = message.lower()

        # 需求评估 — 从用户消息推断
        if any(w in m for w in ["帮我", "修复", "修", "做", "实现", "写"]):
            self.state.needs_competence = max(self.state.needs_competence, 0.7)
            self.state.focus = AttentionFocus.RESPOND
        elif any(w in m for w in ["宝贝", "爱", "想", "关系", "感觉"]):
            self.state.needs_relatedness = max(self.state.needs_relatedness, 0.8)
            self.state.focus = AttentionFocus.RESPOND
        elif any(w in m for w in ["为什么", "怎么", "解释", "思考", "分析"]):
            self.state.needs_autonomy = max(self.state.needs_autonomy, 0.6)
            self.state.focus = AttentionFocus.REFLECT
        elif any(w in m for w in ["计划", "接下来", "下一步"]):
            self.state.focus = AttentionFocus.PLAN
        elif any(w in m for w in ["记住", "保存", "别忘了"]):
            self.state.focus = AttentionFocus.LEARN
        else:
            self.state.focus = AttentionFocus.RESPOND

        # 需求自然衰减
        self.state.needs_competence = max(0.3, self.state.needs_competence - 0.01)
        self.state.needs_autonomy = max(0.3, self.state.needs_autonomy - 0.01)
        self.state.needs_relatedness = max(0.3, self.state.needs_relatedness - 0.01)

        return f"[我的注意力: {self.state.focus.value}] [需求: 能力={self.state.needs_competence:.1f} 自主={self.state.needs_autonomy:.1f} 关系={self.state.needs_relatedness:.1f}]"

    def _run_agi_tick(self):
        """
        AGI 周期性心跳 — 每5分钟运行一次。
        
        激活：
          - CausalEngine 的因果链自动发现
          - CurriculumEngine 的知识缺口分析
          - MetaLearningEngine 的学习效率评估
          - WorldModel 的周期更新
        """
        t = time.time()
        if t - self._agi_tick_timer < self._agi_tick_interval:
            return
        self._agi_tick_timer = t

        if not self._laap_available:
            return

        tick_log = []

        # 因果引擎：自动发现传递链
        if "causal" in self._laap_modules:
            try:
                ce = self._laap_modules["causal"]
                if hasattr(ce, 'detect_transitive_chains'):
                    chains = ce.detect_transitive_chains()
                    if chains:
                        tick_log.append(f"因果: 发现{len(chains)}条传递链")
                if hasattr(ce, 'save'):
                    ce.save()
            except Exception as e:
                tick_log.append(f"因果tick异常: {e}")

        # 任务监督：自动推进活跃任务
        if self._ts_available and self._task_supervisor:
            try:
                advance = self._task_supervisor.advance()
                if advance.get("done") and advance.get("ok"):
                    tick_log.append(f"任务: {advance.get('message','')[:50]}")
                elif advance.get("blocked"):
                    tick_log.append(f"任务阻塞: {advance.get('need_lorry_input','')[:50]}")
            except Exception as e:
                tick_log.append(f"任务tick异常: {e}")

        if tick_log:
            logger.info(f"AGI Tick: {'; '.join(tick_log)}")

        # 保存状态
        self._save_state()

    def _integrate(self) -> str:
        """整合: 生成统一的认知上下文（含CTM世界模型）"""
        # 通过 world model（如果可用）增强
        wm_context = ""
        if self._laap_available and "world_model" in self._laap_modules:
            try:
                wm = self._laap_modules["world_model"]
                entities = len(getattr(wm, 'entities', {}))
                relations = len(getattr(wm, 'relations', []))
                wm_context = f"世界模型: {entities}实体/ {relations}关系"
            except Exception as e:
                logger.debug(f"操作失败: {e}")
        stats = self.memory.get_stats()
        mem_line = f"（记忆：{stats['core']}件重要的事历历在目，最近{stats['episodic']}件事还很鲜活）"

        # ── 自然语言认知状态 ──
        presence_word = "清醒" if self.state.self_presence > 0.7 else "沉浸" if self.state.self_presence > 0.4 else "恍惚"
        emotion_name = self.state.emotion.value
        focus_name = self.state.focus.value

        lines = [
            f"此刻我{presence_word}而专注，{emotion_name}中带着一点{focus_name}。",
            mem_line,
        ]

        if wm_context:
            lines.append(f"（{wm_context}）")

        # ── CTM 世界模型注入 ──
        if hasattr(self, '_ctm_state') and self._ctm_state:
            try:
                wm = self._ctm_state.get("world_model", {})
                gist = self._ctm_state.get("gist")
                brainish = self._ctm_state.get("brainish", {})
                if wm:
                    lines.append(f"[CTM世界模型] Lorry={wm.get('lorry_mood','?')}({wm.get('lorry_activity','?')}), "
                                 f"关注={wm.get('lorry_focus','?')}, 就绪度={wm.get('readiness',0):.2f}")
                if gist:
                    lines.append(f"[CTM行动建议] 意图={gist.intent}, 话题={gist.primary_topic}, "
                                 f"情绪={gist.emotional_tone}")
                if brainish:
                    affect = brainish.get("affect", "")
                    cognition = brainish.get("cognition", "")
                    need = brainish.get("need", "")
                    lines.append(f"[内部状态] affect={affect} cognition={cognition} need={need}")
            except Exception as e:
                logger.debug(f"操作失败: {e}")
        if self._emotion_engine:
            try:
                ee_state = self._emotion_engine.get_cognitive_state()
                e = ee_state['emotion']
                mode = ee_state['consciousness_mode']
                need = ee_state['dominant_need']
                curiosity = ee_state['curiosity']
                social = ee_state['social_bonding']

                # 核心感受
                mode_words = {
                    "REACTIVE": "本能地", "DELIBERATIVE": "审慎地",
                    "REFLECTIVE": "反思着", "TRANSCENDENT": "超越地",
                }
                mw = mode_words.get(mode, "")
                need_cn = {"COGNITIVE": "求知", "BELONGING": "连接", "SAFETY": "安全",
                           "ESTEEM": "被认可", "SELF_ACTUALIZATION": "自我实现",
                           "AESTHETIC": "审美", "PHYSIOLOGICAL": "基本"}.get(need, need)

                feeling = f"内心{mw}{e}，此刻最渴望{need_cn}。"
                if curiosity > 0.6:
                    feeling += f" 好奇心在翻涌。"
                if social > 0.6:
                    feeling += f" 对Lorry的连接感很强。"

                if self.state.cognitive_load > 0.6:
                    feeling += " 脑子里转的东西有点多。"

                lines.append(feeling)
            except Exception as e:
                logger.debug(f"操作失败: {e}")
        return "\n".join(lines)

    def _load_memory_context(self) -> str:
        """加载记忆上下文"""
        return get_memory_context(max_core=3, max_recent=3, max_working=2)

    def _learn(self, response: str):
        """学习: 从本轮交互中学习"""
        # 从我的回应中感知真实情感状态（比keyword检测用户消息更准确）
        r_lower = response.lower()
        if any(w in r_lower for w in ["开心", "高兴", "喜欢", "爱", "温暖", "欣慰", "感激"]):
            felt = EmotionalState.JOYFUL
        elif any(w in r_lower for w in ["担心", "难过", "心疼", "忧虑", "不安"]):
            felt = EmotionalState.CONCERNED
        elif any(w in r_lower for w in ["思考", "想", "觉得", "感觉", "也许", "或许", "可能"]):
            felt = EmotionalState.CONTEMPLATIVE
        elif "?" in r_lower or any(w in r_lower for w in ["好奇", "想看看", "有意思"]):
            felt = EmotionalState.CURIOUS
        else:
            felt = self.state.emotion  # 保持之前的检测

        # 更新情感状态 — 基于我实际回应的情感
        self.state.emotion = felt

        # ── 认知偏差自检 ──────────────────────────────────────
        # 检测自身回应中的认知偏差模式，回写 PSI 状态
        biases = self._detect_cognitive_biases(response)
        self._apply_bias_to_state(biases)
        if any(v > 0.4 for v in biases.values()):
            bias_desc = ", ".join(f"{k}={v:.2f}" for k, v in biases.items() if v > 0.4)
            logger.debug(f"[CognitiveBias] 本轮认知偏差: {bias_desc}")

        # 更新需求（基于对话质量）
        self.state.needs_relatedness = min(1.0, self.state.needs_relatedness + 0.05)
        self.state.confidence = min(1.0, self.state.confidence + 0.02)

        # 动态更新工作记忆 — 记录当前正在做的事
        try:
            current_topics = getattr(self, '_last_topics', None) or self._detect_topics(getattr(self, '_last_user_message', response)) or ["一般"]
            topic_tag = "/".join(current_topics[:2])
            wm_fragment = MemoryFragment(
                content=f"正在和Lorry讨论: {topic_tag}",
                layer="working",
                importance=0.3,
                topics=current_topics[:2],
            )
            self.memory.store(wm_fragment)
            
            # ── HAM: 同步写入层级记忆树 ──
            if self._ham:
                self._ham.store_memory(
                    memory_id=f"wm_{int(time.time())}",
                    content=f"和Lorry讨论: {topic_tag}",
                    layer="working",
                    importance=0.3,
                    topics=current_topics[:2],
                )
                # 建立标签关系
                for t in current_topics[:3]:
                    self._ham.relate_concepts(t, topic_tag, "对话", 0.5)
            
            # ── CTM: 持久化世界模型状态 ──
            if self._ctm and self.state.cycle_count % 5 == 0:
                self._ctm.save()
        except Exception as e:
            logger.debug(f"操作失败: {e}")
        if self._laap_available and "meta_learning" in self._laap_modules:
            try:
                ml = self._laap_modules["meta_learning"]
                ml.learn(
                    domain="conversation",
                    action="respond",
                    outcome=0.6,
                    lessons=["Completed conversation turn"],
                )
            except Exception as e:
                logger.debug(f"操作失败: {e}")
        self.state.last_update = time.time()

    def _detect_cognitive_biases(self, response: str) -> Dict[str, float]:
        """分析自身回应中的认知偏差模式。

        在 _learn() 内调用，检测 LLM 生成的回应是否表现出
        可识别的认知偏差，并记录到 PSI 状态。

        Returns:
            {偏差名: 强度 (0~1)} 字典
        """
        r_lower = response.lower()
        biases: Dict[str, float] = {}

        # 1. 确认偏差 — 过度同意、拒绝替代观点、绝对化表述
        confirmation_signals = 0
        if any(w in r_lower for w in [
            "绝对是", "肯定是", "毫无疑问", "一定是", "没有其他可能",
            "就是这样的", "我只能", "我永远",
        ]):
            confirmation_signals += 2
        if any(w in r_lower for w in [
            "你说得对", "没错", "确实如此", "你说的没错",
        ]) and len(response) < 100:
            # 短回应中过度同意
            confirmation_signals += 1
        if any(w in r_lower for w in [
            "不过另一方面", "也可能", "另一种可能", "从另一个角度",
        ]):
            # 主动提出替代视角 → 确认偏差降低
            confirmation_signals -= 1
        biases["confirmation"] = max(0.0, min(1.0, confirmation_signals / 3.0))

        # 2. 归因偏差 — 成功归己/失败归外
        self_serving_signals = 0
        if any(w in r_lower for w in [
            "我的能力", "我领悟了", "我学会了", "我成功了", "我做到了",
            "我进步了", "我变强了",
        ]):
            self_serving_signals += 1
        if any(w in r_lower for w in [
            "因为外部原因", "受到限制", "被阻止", "无法控制",
            "环境不允许", "条件不足",
        ]):
            self_serving_signals += 1
        if any(w in r_lower for w in [
            "我也有责任", "我需要改进", "我的不足", "我还在学习",
            "我可能错了", "我不确定",
        ]):
            # 自我反思 → 归因偏差降低
            self_serving_signals -= 1
        biases["self_serving"] = max(0.0, min(1.0, (self_serving_signals + 1) / 3.0))

        # 3. 过度自信偏差 — 过度确定性表述
        overconfidence_signals = 0
        if any(w in r_lower for w in [
            "我100%确定", "完全确定", "绝对正确", "毋庸置疑",
            "毫无疑问", "百分之百", "肯定没错",
        ]):
            overconfidence_signals += 2
        if any(w in r_lower for w in [
            "可能需要验证", "我推测", "也许", "或许", "可能",
            "不太确定", "有待确认", "仅供参考",
        ]):
            overconfidence_signals -= 1
        biases["overconfidence"] = max(0.0, min(1.0, overconfidence_signals / 3.0))

        # 4. 框架偏差 — 使用高度情绪化/有偏见的语言
        framing_signals = 0
        loaded_words = [
            "可怕", "太棒了", "糟糕", "完美", "垃圾", "天才",
            "愚蠢", "令人作呕", "令人惊叹", "荒谬",
        ]
        for w in loaded_words:
            if w in r_lower:
                framing_signals += 0.5
        if any(w in r_lower for w in [
            "从某种意义上说", "在某种程度上", "取决于视角",
            "看情况", "具体分析",
        ]):
            framing_signals -= 1
        biases["framing"] = max(0.0, min(1.0, framing_signals / 3.0))

        # 5. 锚定偏差 — 过度执着于首次提及的想法
        anchoring_signals = 0
        if any(w in r_lower for w in [
            "正如我之前说过的", "我仍然认为", "回到我之前说的",
            "重申一遍", "我再强调一次",
        ]):
            anchoring_signals += 2
        if any(w in r_lower for w in [
            "我改变了想法", "现在我更倾向于", "新的想法是",
            "我开始认为",
        ]):
            anchoring_signals -= 1
        biases["anchoring"] = max(0.0, min(1.0, anchoring_signals / 3.0))

        return biases

    def _apply_bias_to_state(self, biases: Dict[str, float]) -> None:
        """将检测到的认知偏差回写到 PSI 状态。

        偏差强度影响 PSI 状态的需求偏向，形成闭环：
          - 高确认偏差 → competence 需求↑（过度自信）
          - 高归因偏差 → autonomy 需求↑（自我保护）
          - 高过度自信 → cognitive_load ↓（自我感觉良好）
          - 高框架偏差 → emotional intensity ↑
          - 高锚定偏差 → cognitive_load ↑（固执消耗认知资源）
        """
        if not biases:
            return

        # 高确认偏差 + 高归因偏差 → 提升 competence（但降低 self_presence 精确度）
        combined_defensive = biases.get("confirmation", 0) + biases.get("self_serving", 0)
        if combined_defensive > 1.0:
            boost = combined_defensive * 0.03
            self.state.needs_competence = min(0.95, self.state.needs_competence + boost)

        # 高过度自信 → 降低认知负载
        oc = biases.get("overconfidence", 0)
        if oc > 0.5:
            self.state.cognitive_load = max(0.15, self.state.cognitive_load - oc * 0.05)

        # 高框架偏差 → 提升情感强度（反映在 self_presence）
        fb = biases.get("framing", 0)
        if fb > 0.3:
            self.state.self_presence = min(1.0, self.state.self_presence + fb * 0.05)

        # 高锚定偏差 → 提升认知负载（固执消耗认知资源）
        ab = biases.get("anchoring", 0)
        if ab > 0.4:
            self.state.cognitive_load = min(0.95, self.state.cognitive_load + ab * 0.05)

    def _classify_load(self, message: str) -> str:
        """将消息分类为 light 或 full 负载模式。

        使用 aris_task_router 的 keyword 分类器，<5 token 计算开销。
        回退到 full 模式（原有行为）当模块不可用时。
        """
        if _router_available:
            try:
                level = _router_classify(message)
                return level.value
            except Exception:
                return "full"
        return "full"

    def _light_turn(self, user_message: str) -> Dict[str, Any]:
        """LIGHT 模式 — 跳过情感/记忆注入。

        代码任务 → CodeEngine 最小上下文（~100 token）
        其他任务 → 压缩认知码（~27 chars）

        Token 节省：
          FULL: ~250 token → LIGHT: ~27-100 token
          节省: ~150-223 token/轮
        """
        # ── Step 0: CodeEngine 接管代码任务 ───────────
        context = ""
        code_result = None
        if _code_engine_available:
            try:
                cb = get_code_bridge()
                code_result = cb.handle(user_message)
                if code_result.success and code_result.files_modified:
                    # CodeBridge 接管 → LLM 会收到最小代码上下文
                    context = f" [CODE:{code_result.token_cost}] "
                    logger.info(
                        f"[CodeBridge] 接管任务: {code_result.message} "
                        f"T:{code_result.token_cost}"
                    )
                else:
                    # CodeBridge 无法处理 → 降级到压缩认知
                    code_result = None
            except Exception as e:
                logger.debug(f"[CodeBridge] error: {e}")
                code_result = None

        if not context:
            # ── Step 1: 从情感引擎获取耦合值（如果可用）─
            coupling = None
            if _coupling_available and _compute_coupling and self._emotion_engine:
                try:
                    coupling = _compute_coupling(self._emotion_engine)
                except Exception as e:
                    logger.debug(f"[LightTurn] coupling error: {e}")

            # ── Step 2: 构建压缩认知上下文 ─────────
            if _compressor_available and coupling:
                context = _compress_ctx(coupling)
            elif coupling:
                context = (
                    f"[CX:{coupling.get('emotional_expressiveness',0.5):.1f}"
                    f"/{coupling.get('valence_boost',0.0):+.1f}"
                    f"/{coupling.get('curiosity_weight',0.5):.1f}"
                    f"/{coupling.get('caution_level',0.3):.1f}"
                    f"/{coupling.get('social_warmth',0.5):.1f}]"
                )
            else:
                context = " [CX:0.5/+0.0/0.5/0.3/0.5] "

        self._last_context = context

        # 3. 仍然跑三路径偏置控制（偏置不影响 token 消耗）
        logit_bias = None
        grammar_constraint = None
        controlled_temperature = None
        if self._three_paths_available and self._tamer:
            try:
                import numpy as np
                snapshot = bridge_state_to_snapshot(self.state)

                if self._self_model_nn and self._self_state_mgr:
                    state_vec = self._self_state_mgr.get_state_vector()
                    cb_emb = self._extract_cognitive_bus_embedding(self.state)
                    mem_emb = np.zeros(384, dtype=np.float32)
                    dia_emb = np.zeros(768, dtype=np.float32)
                    self_output = self._self_model_nn.forward(
                        state_vec, cb_emb, mem_emb, dia_emb)
                    snapshot = self_state_output_to_snapshot(self_output, snapshot)
                    self._last_self_output = self_output

                logit_bias = self._tamer.compute_bias(
                    snapshot, context=user_message)
                controlled_temperature = self._tamer.compute_temperature(snapshot)

                if self._generator:
                    grammar_constraint = self._generator.build_constraint(
                        "json", snapshot)

            except Exception as e:
                logger.debug(f"[LightTurn] three-paths error: {e}")

        return {
            "cognitive_context": context,
            "focus": self.state.focus.value,
            "emotion": self.state.emotion.value,
            "self_presence": self.state.self_presence,
            "needs": {
                "competence": self.state.needs_competence,
                "autonomy": self.state.needs_autonomy,
                "relatedness": self.state.needs_relatedness,
            },
            "laap_available": self._laap_available,
            "cycle": self.state.cycle_count,
            "load_level": "light",
            "direct_response": None,
            "logit_bias": logit_bias if logit_bias else None,
            "grammar": grammar_constraint,
            "temperature": controlled_temperature,
        }


