"""
LAAP AGI — 统一量子因果引擎: 引擎 (R11 拆分)
============================================================
原 causal.py (1642 行) 拆分出的子模块之一。
完整拆分: causal_models.py(数据模型) / causal_discovery.py(发现器) /
          causal_engine.py(引擎) / causal.py(薄门面, 既有导入零破坏)。
"""

from __future__ import annotations

import os
import logging
import json, math, time, random, uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Callable
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum

import numpy as np

logger = logging.getLogger("laap.agi.causal")


# ═══════════════════════════════════════════════════════════════
# 统一因果引擎 (UnifiedCausalEngine)
# ═══════════════════════════════════════════════════════════════
from .causal_models import (
    CausalCondition, CausalEffect, CausalRule,
    TemporalCausalLink, TemporalCausalChain,
    FactorOperator, CausalFactor, MultiFactorRule,
    InterventionResult, CounterfactualEmotion,
)
from .causal_discovery import (
    QuantumCausalStore, ConditionalIndependenceTester,
    CausalDiscovery, CausalBond,
)
class UnifiedCausalEngine:
    """
    统一因果推理引擎 — Pearl 因果层次的三级实现

    统一因果引擎 — 四种因果推理模式融合。

    设计原则：
      1. 量子因果编码 — 面向向量的因果推理(最快)
      2. 物理因果规则 — 基于符号的因果模拟(最精确)
      3. 统计因果发现 — 从数据中自动挖掘因果(最通用)
      4. 因果键学习 — 基于观测的贝叶斯因果(最自适应)

    四种模式互补：量子编码给速度，规则给精度，发现给广度，键给适应度。
    """

    def __init__(self, quantum_dim: int = 64, name: str = "UnifiedCausalEngine"):
        self.name = name
        # 模式1: 量子因果存储
        self.quantum = QuantumCausalStore(dim=quantum_dim)

        # 模式2: 物理因果规则
        self.rules: Dict[str, CausalRule] = {}

        # 模式3: 统计因果发现
        self.discovery = CausalDiscovery()

        # 模式4: 因果键
        self.bonds: Dict[str, CausalBond] = {}

        # ═══ P1-1a: 时间因果链 ═══
        self.temporal_links: Dict[str, TemporalCausalLink] = {}
        self.temporal_chains: Dict[str, TemporalCausalChain] = {}

        # ═══ P1-1b: 多因素因果 ═══
        self.multi_factor_rules: Dict[str, MultiFactorRule] = {}

        # ═══ P1-1d: 干预历史 ═══
        self._intervention_history: List[InterventionResult] = []

        # ═══ P1-1e: 反事实情感 ═══
        self.counterfactual_emotions: Dict[str, CounterfactualEmotion] = {}

        # 实体状态（用于规则模拟）
        self.entity_states: Dict[str, dict] = {}

        # 历史记录
        self.inference_history: List[dict] = []
        self.max_history = 500

        # 统计
        self._total_inferences = 0
        self._total_learns = 0
        self._created_at = time.time()

        # 自动注册默认因果规则 (物理直觉)
        self._register_default_rules()

        logger.info(f"[UnifiedCausalEngine] 初始化完成 dim={quantum_dim}")

    # ─────────── 四种学习接口 ───────────

    def learn_from_vectors(self, cause_vec: np.ndarray, effect_vec: np.ndarray,
                           confidence: float = 0.5, domain: str = "general") -> bool:
        """从向量对学习因果关系 (量子模式)"""
        self._total_learns += 1
        return self.quantum.learn(cause_vec, effect_vec, confidence, domain)

    def learn_rule(self, rule: CausalRule):
        """注册/更新一条因果规则 (符号模式)"""
        self.rules[rule.name] = rule
        self._total_learns += 1

    def learn_from_observations(self, variables: List[str],
                                observations: Dict[str, List[float]]) -> List[Dict[str, Any]]:
        """从观测数据中发现因果关系 (统计模式)"""
        self._total_learns += 1
        relations = self.discovery.find_causal_relations(variables, observations)
        # 自动将发现的关系注入量子存储和键
        for rel in relations:
            bond_key = f"{rel['cause']}→{rel['effect']}"
            if bond_key not in self.bonds:
                self.bonds[bond_key] = CausalBond(
                    action=rel['cause'],
                    target_type=rel['effect'],
                    effect_desc=f"{rel['cause']} 影响 {rel['effect']}",
                    weight=rel['strength'],
                    confidence=rel['confidence'],
                    domain="discovered",
                )
            else:
                self.bonds[bond_key].observe(True)
        return relations

    def learn_bond(self, action: str, target: str, effect: str,
                   matched: bool, domain: str = "physics"):
        """从一次观测中学习/更新因果键 (贝叶斯模式)"""
        key = f"{action}→{target}:{effect}"
        if key not in self.bonds:
            self.bonds[key] = CausalBond(
                action=action, target_type=target,
                effect_desc=effect, domain=domain,
            )
        self.bonds[key].observe(matched)
        self._total_learns += 1

    def learn_entity_state(self, entity_id: str, state: dict):
        """更新/注册实体状态 (用于规则模拟)"""
        self.entity_states[entity_id] = state

    # ─────────── P1-1a: 时间因果链 ───────────

    def learn_temporal_link(self, cause: str, effect: str,
                            delay: float = 0.0, confidence: float = 0.5,
                            domain: str = "general"):
        """学习一条有时间信息的因果链 A --[Δt]--> B"""
        self._total_learns += 1
        key = f"{cause}→{effect}"
        if key in self.temporal_links:
            link = self.temporal_links[key]
            # 滚动平均更新延迟
            n = link.observation_count + 1
            link.delay_mean = (link.delay_mean * link.observation_count + delay) / n
            link.observation_count = n
            link.confidence = min(1.0, link.confidence + 0.05)
        else:
            self.temporal_links[key] = TemporalCausalLink(
                cause_name=cause, effect_name=effect,
                delay_mean=delay, delay_std=abs(delay * 0.2),
                confidence=confidence, domain=domain,
                observation_count=1,
            )

    def learn_temporal_chain(self, chain_name: str,
                             links: List[Tuple[str, str, float]],
                             domain: str = "general"):
        """学习一条完整的时间因果链: [(A, B, Δt1), (B, C, Δt2), ...]"""
        chain = TemporalCausalChain(name=chain_name, domain=domain)
        for cause, effect, delay in links:
            link = TemporalCausalLink(
                cause_name=cause, effect_name=effect,
                delay_mean=delay, domain=domain,
            )
            chain.add_link(link)
            # 也单独记录每一环
            self.learn_temporal_link(cause, effect, delay, domain=domain)
        self.temporal_chains[chain_name] = chain
        self._total_learns += 1
        return chain

    def predict_with_timing(self, cause: str,
                            max_steps: int = 5
                            ) -> List[Dict[str, Any]]:
        """给定原因，预测后续因果链 + 时间估计"""
        results = []

        # BFS 在时间链图上搜索
        queue = [(cause, 0, 0.0, [cause])]  # (current_node, depth, cumulative_delay, path)
        visited = set()

        while queue and len(results) < max_steps:
            current, depth, cum_delay, path = queue.pop(0)
            if current in visited and depth > 0:
                continue
            visited.add(current)

            for key, link in self.temporal_links.items():
                if link.cause_name == current:
                    new_path = path + [link.effect_name]
                    new_delay = cum_delay + link.delay_mean
                    results.append({
                        "chain": " → ".join(new_path),
                        "total_delay": round(new_delay, 2),
                        "confidence": round(link.confidence, 3),
                        "domain": link.domain,
                        "steps": len(new_path) - 1,
                    })
                    if depth + 1 < max_steps:
                        queue.append((link.effect_name, depth + 1, new_delay, new_path))

        results.sort(key=lambda x: (-x["confidence"], x["total_delay"]))
        return results[:max_steps]

    def detect_transitive_chains(self) -> List[TemporalCausalChain]:
        """
        自动发现传递性因果链。

        如果 A→B 且 B→C，则推断 A→B→C 是一条链。
        如果有 A→B, B→C, C→D，链延长。
        """
        chains = []
        # 从每个可能的起点开始 BFS
        all_causes = set(l.cause_name for l in self.temporal_links.values())
        all_effects = set(l.effect_name for l in self.temporal_links.values())
        starters = all_causes - all_effects

        for start in starters:
            chain_links = []
            current = start
            max_depth = 10
            for _ in range(max_depth):
                next_links = [(k, l) for k, l in self.temporal_links.items()
                             if l.cause_name == current]
                if not next_links:
                    break
                # 选置信度最高的
                k, best = max(next_links, key=lambda x: x[1].confidence)
                chain_links.append(best)
                current = best.effect_name

            if len(chain_links) >= 2:
                chain = TemporalCausalChain(
                    name=f"auto_chain_{start}",
                    domain=chain_links[0].domain,
                )
                for link in chain_links:
                    chain.add_link(link)
                chains.append(chain)
                self.temporal_chains[chain.name] = chain

        return chains

    # ─────────── P1-1b: 多因素因果 ───────────

    def learn_multi_factor_rule(self, name: str, effect: str,
                                factor_names: List[str],
                                operator: str = "and",
                                factor_weights: Optional[List[float]] = None,
                                domain: str = "general",
                                confidence: float = 0.5):
        """学习一条多因素因果规则"""
        weights = factor_weights or [1.0] * len(factor_names)
        factors = [
            CausalFactor(name=fname, weight=w, domain=domain)
            for fname, w in zip(factor_names, weights)
        ]
        op = FactorOperator(operator.lower())
        rule = MultiFactorRule(
            name=name, effect=effect,
            factors=factors, operator=op,
            domain=domain, confidence=confidence,
        )
        self.multi_factor_rules[name] = rule
        self._total_learns += 1
        return rule

    def set_factor_state(self, rule_name: str,
                         factor_states: Dict[str, bool]):
        """设置多因素规则中各因素的状态"""
        rule = self.multi_factor_rules.get(rule_name)
        if not rule:
            return
        for f in rule.factors:
            if f.name in factor_states:
                f.is_present = factor_states[f.name]

    def predict_with_factors(self, rule_name: Optional[str] = None
                             ) -> List[Dict[str, Any]]:
        """基于多因素规则预测结果"""
        results = []
        rules_to_check = ([self.multi_factor_rules[rule_name]]
                          if rule_name else
                          list(self.multi_factor_rules.values()))

        for rule in rules_to_check:
            activation, active = rule.compute_activation()
            results.append({
                "rule": rule.name,
                "effect": rule.effect,
                "activation": round(activation, 4),
                "active_factors": active,
                "operator": rule.operator.value,
                "confidence": round(rule.confidence, 3),
                "domain": rule.domain,
            })

        results.sort(key=lambda x: -x["activation"])
        return results

    # ─────────── P1-1c: 循环因果检测 ───────────

    def detect_cycles(self) -> List[Dict[str, Any]]:
        """
        检测因果图中的循环。

        遍历时间链、规则、因果键，找 A→B→A 模式。
        """
        cycles = []

        # 从时间链中检测
        for key, link in self.temporal_links.items():
            reverse_key = f"{link.effect_name}→{link.cause_name}"
            if reverse_key in self.temporal_links:
                rev = self.temporal_links[reverse_key]
                strength = (link.confidence + rev.confidence) / 2
                cycles.append({
                    "type": "temporal",
                    "cycle": f"{link.cause_name}↔{link.effect_name}",
                    "strength": round(strength, 3),
                    "forward_confidence": link.confidence,
                    "backward_confidence": rev.confidence,
                })

        # 从因果键中检测
        for key, bond in self.bonds.items():
            parts = key.split("→")
            if len(parts) == 2:
                reverse_key = f"{parts[1]}→{parts[0]}"
                # Check if there's a bond that reverses this
                for k2, b2 in self.bonds.items():
                    rparts = k2.split("→")
                    if len(rparts) == 2 and rparts[0] == parts[1] and rparts[1] == parts[0]:
                        cycles.append({
                            "type": "bond",
                            "cycle": key,
                            "reverse": k2,
                            "strength": round((bond.weight + b2.weight) / 2, 3),
                        })

        # 从多因素规则中检测自指
        for name, rule in self.multi_factor_rules.items():
            if rule.effect in [f.name for f in rule.factors]:
                cycles.append({
                    "type": "self_referential",
                    "rule": name,
                    "cycle": f"{rule.effect} → {rule.name} → {rule.effect}",
                    "strength": round(rule.confidence, 3),
                })

        cycles.sort(key=lambda x: -x["strength"])
        return cycles

    # ─────────── P1-1d: 因果干预 (do-calculus) ───────────

    def intervene(self, var_name: str, value: Any,
                  target_var: Optional[str] = None) -> InterventionResult:
        """
        因果干预模拟: do(var_name = value)

        强制设定一个变量的值（切断其正常原因），
        观察对目标变量的影响。

        Args:
            var_name: 被干预的变量名
            value: 设定的值
            target_var: 目标变量（可选，不指定则返回所有影响）

        Returns:
            InterventionResult 包含干预前后对比
        """
        # 干预前：自然状态下的预测
        pre = {}
        pre_natural = self.predict(var_name, mode="auto")
        for r in pre_natural["results"]:
            key = r.get("effect", r.get("rule", r.get("bond_key", "?")))
            pre[key] = r.get("confidence", r.get("probability", 0))

        # 干预：保存被干预实体的原始状态，然后强制修改
        intervened_entities = {}
        for eid, state in self.entity_states.items():
            if var_name.lower() in eid.lower() or var_name.lower() in str(state).lower():
                intervened_entities[eid] = dict(state)
                if isinstance(state, dict):
                    # 尝试设置属性
                    for k in state:
                        if var_name.lower() in k.lower():
                            state[k] = value

        # 干预后：重新预测
        post = {}
        if target_var:
            post_result = self.predict(target_var, mode="auto")
        else:
            post_result = self.predict(var_name, mode="bond")

        for r in post_result["results"]:
            key = r.get("effect", r.get("rule", r.get("bond_key", "?")))
            post[key] = r.get("confidence", r.get("probability", 0))

        # 恢复被干预的实体
        for eid, original in intervened_entities.items():
            self.entity_states[eid] = original

        # 计算因果效应
        causal_effects = {}
        for key in set(list(pre.keys()) + list(post.keys())):
            diff = post.get(key, 0) - pre.get(key, 0)
            if abs(diff) > 0.01:
                causal_effects[key] = round(diff, 4)

        avg_effect = sum(causal_effects.values()) / max(1, len(causal_effects))

        result = InterventionResult(
            intervention_var=var_name,
            intervention_value=value,
            pre_intervention=pre,
            post_intervention=post,
            causal_effect=avg_effect,
            confidence=0.6,
            assumptions=[
                f"do({var_name}={value}) 切断所有指向 {var_name} 的因果路径",
                "其他变量保持不变 (ceteris paribus)",
            ],
        )
        self._intervention_history.append(result)
        return result

    def estimate_causal_effect(self, var_name: str, value_from: Any,
                               value_to: Any, target_var: str) -> float:
        """估计 do(X=x1) 和 do(X=x2) 之间的因果效应差"""
        r1 = self.intervene(var_name, value_from, target_var)
        r2 = self.intervene(var_name, value_to, target_var)
        return r2.causal_effect - r1.causal_effect

    # ─────────── P1-1e: 反事实情感标记 ───────────

    def tag_counterfactual_emotion(self, scenario: str, action: str,
                                   actual_outcome: str,
                                   counterfactual_outcome: str,
                                   actual_emotion: str,
                                   counterfactual_emotion: str,
                                   intensity: float = 0.5) -> CounterfactualEmotion:
        """创建并计算一个反事实情感标签"""
        cf = CounterfactualEmotion(
            scenario=scenario, action=action,
            actual_outcome=actual_outcome,
            counterfactual_outcome=counterfactual_outcome,
            actual_emotion=actual_emotion,
            counterfactual_emotion=counterfactual_emotion,
            intensity=intensity,
        )
        cf.compute()
        key = f"{scenario}:{action}"
        self.counterfactual_emotions[key] = cf
        return cf

    def get_emotional_counterfactuals(self, emotion: Optional[str] = None,
                                      min_regret: float = 0.0
                                      ) -> List[CounterfactualEmotion]:
        """查询反事实情感标签"""
        results = []
        for cf in self.counterfactual_emotions.values():
            if emotion and cf.actual_emotion != emotion and cf.counterfactual_emotion != emotion:
                continue
            if cf.emotional_regret < min_regret and cf.emotional_relief < min_regret:
                continue
            results.append(cf)
        results.sort(key=lambda x: -(x.emotional_regret + x.emotional_relief))
        return results

    # ─────────── 统一推理接口 ───────────

    def predict(self, query: Any, mode: str = "auto",
                top_k: int = 5, domain_filter: Optional[str] = None
                ) -> Dict[str, Any]:
        """
        统一因果推理入口。

        Args:
            query: 原因 (向量 np.ndarray 用于量子模式, 字符串/字典用于规则模式)
            mode: "quantum" | "rule" | "bond" | "auto"
            top_k: 返回 top-K 结果
            domain_filter: 限定领域

        Returns:
            {results, mode, confidence, metadata}
        """
        self._total_inferences += 1

        if mode == "auto":
            # 自动选择最佳模式
            if isinstance(query, np.ndarray):
                mode = "quantum"
            elif isinstance(query, str):
                mode = "rule"
            else:
                mode = "bond"

        results = []

        if mode == "quantum" and isinstance(query, np.ndarray):
            effects = self.quantum.predict_effect(query, top_k, domain_filter)
            for vec, score, dom in effects:
                results.append({
                    "effect_vector": vec.tolist()[:8],  # 预览前8维
                    "confidence": round(float(score), 4),
                    "domain": dom,
                    "mode": "quantum",
                })

        elif mode == "rule" and isinstance(query, (str, dict)):
            action = query if isinstance(query, str) else query.get("action", "")
            target_state = query if isinstance(query, dict) else {}
            matched_rules = self._match_rules(action, target_state)
            for rule_name, probability, confidence in matched_rules[:top_k]:
                results.append({
                    "rule": rule_name,
                    "probability": round(probability, 3),
                    "confidence": round(confidence, 3),
                    "mode": "rule",
                })

        elif mode == "bond":
            for key, bond in self.bonds.items():
                if domain_filter and bond.domain != domain_filter:
                    continue
                if isinstance(query, str) and query.lower() not in key.lower():
                    continue
                results.append({
                    "bond_key": key,
                    "action": bond.action,
                    "target": bond.target_type,
                    "effect": bond.effect_desc,
                    "weight": round(bond.weight, 3),
                    "confidence": round(bond.confidence, 3),
                    "observations": bond.observation_count,
                    "mode": "bond",
                })
            results.sort(key=lambda x: -x["confidence"])

        # 记录推理历史
        self.inference_history.append({
            "t": time.time(),
            "query": str(query)[:50],
            "mode": mode,
            "results_count": len(results),
        })
        if len(self.inference_history) > self.max_history:
            self.inference_history = self.inference_history[-self.max_history:]

        return {
            "results": results[:top_k],
            "mode": mode,
            "total_found": len(results),
            "total_inferences": self._total_inferences,
        }

    def predict_effect(self, cause: np.ndarray, top_k: int = 5) -> Dict[str, Any]:
        """便捷接口：给定原因向量，预测效应"""
        return self.predict(cause, mode="quantum", top_k=top_k)

    def infer_cause(self, effect: np.ndarray, top_k: int = 5) -> Dict[str, Any]:
        """便捷接口：给定效应向量，推理原因"""
        self._total_inferences += 1
        results = []
        causes = self.quantum.infer_cause(effect, top_k)
        for vec, score, dom in causes:
            results.append({
                "cause_vector": vec.tolist()[:8],
                "confidence": round(float(score), 4),
                "domain": dom,
                "mode": "quantum",
            })
        return {
            "results": results,
            "mode": "quantum_reverse",
            "total_found": len(results),
            "total_inferences": self._total_inferences,
        }

    def counterfactual(self, action: str, actor: str = "agent",
                       target: str = "", instrument: Optional[str] = None
                       ) -> Dict[str, Any]:
        """
        反事实推理："如果没做 X，会怎样？"

        保存当前状态快照 → 模拟执行 → 恢复 → 返回对比。
        """
        snapshot = dict(self.entity_states)
        actual_result = self._simulate_action(action, actor, target, instrument)
        self.entity_states = snapshot

        # 查找相关规则
        relevant_rules = [
            r for r in self.rules.values()
            if r.action == action and r.enabled
        ]

        return {
            "counterfactual": f"如果不{action}{target}",
            "would_have_happened": actual_result.get("narrative", "什么都不会发生"),
            "triggered_rules": [r.name for r in relevant_rules],
            "state_changes": actual_result.get("state_changes", []),
            "confidence": actual_result.get("confidence", 0.5),
        }

    # ─────────── 内部方法 ───────────

    def _match_rules(self, action: str, target_state: Optional[dict] = None
                     ) -> List[Tuple[str, float, float]]:
        """匹配给定动作的因果规则"""
        matched = []
        for rule in self.rules.values():
            if rule.action != action or not rule.enabled:
                continue
            # 检查条件（如果有目标状态）
            all_met = True
            if target_state:
                for cond in rule.conditions:
                    if cond.source == "target" and not cond.check(target_state):
                        all_met = False
                        break
            if all_met:
                prob = rule.probability * rule.confidence
                matched.append((rule.name, prob, rule.confidence))
        matched.sort(key=lambda x: -x[1])
        return matched

    def _simulate_action(self, action: str, actor: str,
                         target: str, instrument: Optional[str] = None) -> dict:
        """模拟一个动作的因果链"""
        triggered = []
        state_changes = []

        for rule in self.rules.values():
            if rule.action != action or not rule.enabled:
                continue

            # 检查条件
            all_met = True
            for cond in rule.conditions:
                source_id = target if cond.source == "target" else (
                    instrument if cond.source == "instrument" else actor
                )
                if source_id and source_id in self.entity_states:
                    if not cond.check(self.entity_states[source_id]):
                        all_met = False
                        break
                elif cond.source == "target" and not target:
                    all_met = False

            if all_met and random.random() < rule.probability:
                triggered.append(rule)
                for eff in rule.effects:
                    source_id = target if eff.target == "target" else (
                        instrument if eff.target == "instrument" else actor
                    )
                    if source_id and source_id in self.entity_states:
                        old = dict(self.entity_states[source_id])
                        self.entity_states[source_id] = eff.apply(self.entity_states[source_id])
                        state_changes.append({
                            "entity": source_id,
                            "property": eff.property,
                            "from": old.get(eff.property.split(".")[-1], "?"),
                            "to": eff.value,
                        })

        desc = f"{actor} {action} {target}"
        if instrument:
            desc += f" 用 {instrument}"
        return {
            "triggered_rules": [r.name for r in triggered],
            "narrative": desc,
            "state_changes": state_changes,
            "confidence": max([r.confidence for r in triggered]) if triggered else 0.0,
        }

    # ─────────── 默认因果先验 ───────────

    def _register_default_rules(self):
        """注册默认因果规则 — 最小物理直觉"""
        defaults = [
            CausalRule(name="pour_liquid_into_container", action="pour",
                conditions=[
                    CausalCondition(source="instrument", property="state", operator="eq", value="liquid"),
                    CausalCondition(source="target", property="is_container", operator="eq", value=True),
                ],
                effects=[
                    CausalEffect(target="target", property="current_contents", operation="add", value=0.1),
                    CausalEffect(target="instrument", property="container_id", operation="set", value="target"),
                ], confidence=0.7),

            CausalRule(name="drop_object_falls", action="drop",
                conditions=[
                    CausalCondition(source="target", property="is_movable", operator="eq", value=True),
                ],
                effects=[
                    CausalEffect(target="target", property="surface_of", operation="set", value="floor"),
                ], probability=0.9, confidence=0.8),

            CausalRule(name="heat_liquid_boils", action="heat",
                conditions=[
                    CausalCondition(source="target", property="state", operator="eq", value="liquid"),
                    CausalCondition(source="target", property="temperature", operator="gte", value=100.0),
                ],
                effects=[
                    CausalEffect(target="target", property="state", operation="set", value="gas"),
                ], confidence=0.6),

            CausalRule(name="message_causes_response", action="speak",
                conditions=[
                    CausalCondition(source="target", property="is_agent", operator="eq", value=True),
                ],
                effects=[
                    CausalEffect(target="target", property="last_response", operation="set", value="generated"),
                ], domain="social", confidence=0.9),

            CausalRule(name="learning_increases_knowledge", action="learn",
                conditions=[
                    CausalCondition(source="actor", property="can_learn", operator="eq", value=True),
                ],
                effects=[
                    CausalEffect(target="actor", property="knowledge_level", operation="add", value=0.05),
                ], domain="cognitive", confidence=0.8),
        ]
        for rule in defaults:
            self.rules[rule.name] = rule

    # ─────────── 序列化 ───────────

    def save(self, path: str = None):
        path = path or os.environ.get("LAAP_STATE_PATH", "./agi_state/causal_engine.json")
        """持久化因果引擎状态"""
        data = {
            "version": "1.0",
            "created_at": self._created_at,
            "total_inferences": self._total_inferences,
            "total_learns": self._total_learns,
            "rules": [r.to_dict() for r in self.rules.values()],
            "bonds": {k: b.to_dict() for k, b in self.bonds.items()},
            "quantum_stats": self.quantum.stats(),
            "entity_states": self.entity_states,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[CausalEngine] 保存到 {path}")
        return path

    def load(self, path: str = None):
        path = path or os.environ.get("LAAP_STATE_PATH", "./agi_state/causal_engine.json")
        """加载持久化的因果引擎状态"""
        p = Path(path)
        if not p.exists():
            logger.warning(f"[CausalEngine] 状态文件不存在: {path}")
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # 只恢复规则和键（向量数据太大，需要重建）
            for r_data in data.get("rules", []):
                rule = CausalRule(
                    name=r_data["name"], action=r_data.get("action", ""),
                    domain=r_data.get("domain", "general"),
                    probability=r_data.get("probability", 1.0),
                    confidence=r_data.get("confidence", 0.5),
                    observation_count=r_data.get("observations", 0),
                )
                self.rules[rule.name] = rule
            self.entity_states = data.get("entity_states", {})
            logger.info(f"[CausalEngine] 从 {path} 加载, {len(self.rules)} 条规则")
            return True
        except Exception as e:
            logger.error(f"[CausalEngine] 加载失败: {e}")
            return False

    def stats(self) -> dict:
        """引擎统计信息"""
        return {
            "engine": "UnifiedCausalEngine v1.1",
            "uptime": time.time() - self._created_at,
            "quantum_links": len(self.quantum.causal_links),
            "symbolic_rules": len(self.rules),
            "causal_bonds": len(self.bonds),
            "temporal_links": len(self.temporal_links),
            "temporal_chains": len(self.temporal_chains),
            "multi_factor_rules": len(self.multi_factor_rules),
            "interventions": len(self._intervention_history),
            "cf_emotions": len(self.counterfactual_emotions),
            "entity_states": len(self.entity_states),
            "total_inferences": self._total_inferences,
            "total_learns": self._total_learns,
            "history_size": len(self.inference_history),
            "inference_rate": round(
                self._total_inferences / max(1, time.time() - self._created_at), 2
            ),
        }


# ═══════════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════════



# ─── 自测 (原 __main__ 冒烟) ───────────────────────────────────
def test():
    """基本功能测试"""
    engine = UnifiedCausalEngine()

    # 测试1: 规则匹配
    logger.info("=== 测试1: 规则因果推理 ===")
    engine.learn_entity_state("water", {"state": "liquid", "temperature": 25, "is_movable": True})
    engine.learn_entity_state("cup", {"is_container": True, "current_contents": 0.0, "is_movable": True})

    result = engine.predict("pour", mode="rule")
    logger.info(f"  pour → {result}")
    logger.info("\n=== 测试2: 反事实推理 ===")
    cf = engine.counterfactual("pour", "aris", "cup", "water")
    logger.info(f"  {cf['counterfactual']}: {cf['would_have_happened']}")
    logger.info(f"  触发规则: {cf['triggered_rules']}")
    logger.info("\n=== 测试3: 量子因果编码 ===")
    c1 = np.random.randn(64)
    e1 = np.random.randn(64)
    engine.learn_from_vectors(c1, e1, confidence=0.8, domain="test")
    pred = engine.predict_effect(c1)
    logger.info(f"  学习1条因果链 → predict: {len(pred['results'])} 条结果")
    logger.info(f"  置信度: {[r['confidence'] for r in pred['results']]}")
    logger.info("\n=== 测试4: 因果键贝叶斯学习 ===")
    for _ in range(10):
        engine.learn_bond("ask", "lorry", "get_answer", matched=True)
    engine.learn_bond("ask", "lorry", "get_silence", matched=False)
    bond_result = engine.predict("lorry", mode="bond")
    logger.info(f"  lorry相关因果键: {bond_result['total_found']} 条")
    for r in bond_result["results"]:
        logger.info(f"    {r['action']}→{r['target']}: {r['effect']} (w={r['weight']}, c={r['confidence']})")
    logger.info("\n=== 测试5: PC算法因果发现 ===")
    data = {
        "A": [1, 2, 3, 4, 5, 6, 7, 8],
        "B": [2, 4, 6, 8, 10, 12, 14, 16],  # B = 2*A
        "C": [5, 5, 5, 5, 5, 5, 5, 5],       # C = constant
        "D": [3, 1, 4, 1, 5, 9, 2, 6],       # D = random
    }
    relations = engine.learn_from_observations(["A", "B", "C", "D"], data)
    logger.info(f"  发现 {len(relations)} 条因果关系:")
    for r in relations:
        logger.info(f"    {r['cause']} → {r['effect']} (strength={r['strength']:.3f})")
    logger.info(f"\n=== 引擎统计 ===")
    for k, v in engine.stats().items():
        logger.info(f"  {k}: {v}")
    engine.save()
    logger.info(f"\n✅ 统一因果引擎测试完成")
def test_p1_1():
    """P1-1 增强测试：时间链 + 多因素 + 循环检测 + 干预 + 情感"""
    engine = UnifiedCausalEngine()
    logger.info("=" * 50)
    logger.info("P1-1 因果引擎强化测试")
    logger.info("=" * 50)
    logger.info("\n=== P1-1a: 时间因果链 ===")
    engine.learn_temporal_link("message", "thought", delay=0.5, domain="cognitive")
    engine.learn_temporal_link("thought", "response", delay=0.3, domain="cognitive")
    engine.learn_temporal_link("response", "lorry_reply", delay=2.0, domain="social")
    engine.learn_temporal_link("rain", "wet_ground", delay=60.0, domain="physics")
    engine.learn_temporal_link("wet_ground", "slippery", delay=5.0, domain="physics")

    # 学习完整链
    engine.learn_temporal_chain("cognition_cycle", [
        ("message", "thought", 0.5),
        ("thought", "response", 0.3),
        ("response", "lorry_reply", 2.0),
    ], domain="cognitive")

    # 预测带时间
    predictions = engine.predict_with_timing("message", max_steps=3)
    logger.info(f"  'message' 的预测链:")
    for p in predictions:
        logger.info(f"    {p['chain']} (Δt={p['total_delay']}s, conf={p['confidence']})")
    transitive = engine.detect_transitive_chains()
    logger.info(f"  自动发现的传递链: {len(transitive)} 条")
    for c in transitive:
        logger.info(f"    {c.name}: {len(c.links)} 环, 总延迟={c.total_delay:.1f}s")
    assert len(predictions) >= 2, "时间链预测应该返回至少2条链"
    assert any("message → thought → response" in p["chain"] for p in predictions), \
        "应该包含 message→thought→response 链"

    # ─── 测试2: 多因素因果 ───
    logger.info("\n=== P1-1b: 多因素因果 ===")
    engine.learn_multi_factor_rule(
        "fire_start",
        effect="combustion",
        factor_names=["fuel", "oxygen", "heat"],
        operator="and",
        domain="physics",
    )
    engine.learn_multi_factor_rule(
        "take_umbrella",
        effect="stay_dry",
        factor_names=["rain_forecast", "going_outside"],
        operator="or",
        domain="daily_life",
    )

    # AND: 缺氧气
    engine.set_factor_state("fire_start", {"fuel": True, "oxygen": False, "heat": True})
    result = engine.predict_with_factors("fire_start")[0]
    logger.info(f"  fire_start (缺氧气): activation={result['activation']} (期望 0.0)")
    assert result["activation"] == 0.0, "AND规则缺一个条件应为0"

    # AND: 全满足
    engine.set_factor_state("fire_start", {"fuel": True, "oxygen": True, "heat": True})
    result = engine.predict_with_factors("fire_start")[0]
    logger.info(f"  fire_start (全满足): activation={result['activation']} (期望 >0)")
    assert result["activation"] > 0, "AND规则全满足应 > 0"

    # OR: 任一满足
    engine.set_factor_state("take_umbrella", {"rain_forecast": True, "going_outside": False})
    result = engine.predict_with_factors("take_umbrella")[0]
    logger.info(f"  take_umbrella (预报有雨): activation={result['activation']} (期望 >0)")
    assert result["activation"] > 0, "OR规则任一满足应 > 0"

    # ─── 测试3: 循环因果检测 ───
    logger.info("\n=== P1-1c: 循环因果检测 ===")
    engine.learn_temporal_link("excited", "happy", delay=0.1)
    engine.learn_temporal_link("happy", "excited", delay=0.1, confidence=0.6)
    # 自指多因素规则
    engine.learn_multi_factor_rule(
        "self_loop",
        effect="thinking",
        factor_names=["thinking", "curiosity"],
        operator="and",
        domain="cognitive",
    )
    cycles = engine.detect_cycles()
    logger.info(f"  检测到 {len(cycles)} 个循环:")
    for c in cycles:
        logger.info(f"    [{c['type']}] {c.get('cycle', c.get('rule', '?'))} (strength={c['strength']})")
    assert len(cycles) >= 2, "应该至少检测到2个循环"

    # ─── 测试4: 因果干预 ───
    logger.info("\n=== P1-1d: 因果干预 (do-calculus) ===")
    engine.learn_bond("study", "exam", "pass_exam", matched=True, domain="education")
    for _ in range(5):
        engine.learn_bond("study", "exam", "pass_exam", matched=True, domain="education")

    # 建立实体状态
    engine.learn_entity_state("student", {"studied": True, "is_tired": False})

    intervention = engine.intervene("studied", True, target_var="exam")
    logger.info(f"  do(studied=True) 对 exam 的因果效应: {intervention.causal_effect:.4f}")
    logger.info(f"  假设: {intervention.assumptions}")
    assert intervention.pre_intervention is not None
    assert intervention.post_intervention is not None

    # ─── 测试5: 反事实情感标记 ───
    logger.info("\n=== P1-1e: 反事实情感标记 ===")
    cf = engine.tag_counterfactual_emotion(
        scenario="考试",
        action="熬夜复习",
        actual_outcome="考了高分",
        counterfactual_outcome="如果没复习会挂科",
        actual_emotion="happy",
        counterfactual_emotion="sad",
        intensity=0.8,
    )
    logger.info(f"  场景: {cf.scenario}")
    logger.info(f"  实际: {cf.actual_outcome} ({cf.actual_emotion})")
    logger.info(f"  反事实: {cf.counterfactual_outcome} ({cf.counterfactual_emotion})")
    logger.info(f"  庆幸度: {cf.emotional_relief:.3f}, 后悔度: {cf.emotional_regret:.3f}")
    assert cf.emotional_relief > 0, "实际好反事实差 → 应该庆幸"

    # 后悔场景
    cf2 = engine.tag_counterfactual_emotion(
        scenario="对话",
        action="说了伤人的话",
        actual_outcome="lorry不开心",
        counterfactual_outcome="如果没说就不会伤害他",
        actual_emotion="sad",
        counterfactual_emotion="happy",
        intensity=0.9,
    )
    logger.info(f"\n  场景: {cf2.scenario}")
    logger.info(f"  后悔度: {cf2.emotional_regret:.3f}")
    assert cf2.emotional_regret > 0, "实际差反事实好 → 应该后悔"

    # 查询情感
    sad_cfs = engine.get_emotional_counterfactuals(emotion="sad")
    logger.info(f"\n  'sad' 相关的反事实情感: {len(sad_cfs)} 条")
    logger.info(f"\n=== P1-1 引擎统计 ===")
    for k, v in engine.stats().items():
        logger.info(f"  {k}: {v}")
    engine.save()
    logger.info(f"\n✅ P1-1 全部 5 项增强测试通过！")
CausalEngine = UnifiedCausalEngine


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("===== 基础测试 =====")
    test()
    logger.info("\n\n===== P1-1 增强测试 =====")
    test_p1_1()
