"""
LAAP AGI — Unified Memory System Interface

A unified interface layer that orchestrates all memory subsystems:
- Working Memory: Limited capacity (7 items) for current context
- Episodic Memory: Event sequences with emotional tagging
- Semantic Memory: Concept networks with relational reasoning
- Procedural Memory: Skills, habits, and automated responses

Provides a simplified, high-level API for memory operations.
"""

from __future__ import annotations

import time
import math
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from collections import deque

from .memory_system import (
    EpisodicMemory,
    SemanticMemory,
    ProceduralMemory,
    MemoryConsolidator,
    MemoryType,
    MemoryPriority,
    MemoryTrace,
)


class UnifiedMemory:
    # 记忆持久化：跨进程/重启保留 episodic/semantic/procedural 记忆。
    # 路径：<laap>/data/unified_memory.json（相对本文件向上两级 → laap/）
    PERSIST_PATH = (Path(__file__).resolve().parent.parent / "data"
                    / "unified_memory.json")

    def __init__(self):
        self.working_memory: deque[Dict[str, Any]] = deque(maxlen=7)
        self.episodic_memory = EpisodicMemory()
        self.semantic_memory = SemanticMemory()
        self.procedural_memory = ProceduralMemory()
        self.consolidator = MemoryConsolidator(
            self.episodic_memory,
            self.semantic_memory,
            self.procedural_memory,
        )
        self.emotional_state: Dict[str, float] = {
            "valence": 0.0,
            "arousal": 0.0,
            "dominance": 0.5,
        }
        # 启动即恢复已持久化的记忆（若无文件则保持空，不抛错）
        self.load()

    def add_to_working_memory(
        self,
        content: str,
        source_type: str = "unknown",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        item = {
            "content": content,
            "source_type": source_type,
            "metadata": metadata or {},
            "timestamp": time.time(),
        }
        self.working_memory.append(item)

    def encode_experience(
        self,
        content: str,
        emotional_valence: float = 0.0,
        emotional_arousal: float = 0.0,
        priority: MemoryPriority = MemoryPriority.RELEVANT,
        context_triggers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        episode_trace = self.episodic_memory.encode_episode(
            content=content,
            emotional_valence=emotional_valence,
            emotional_arousal=emotional_arousal,
            priority=priority,
            associations=context_triggers,
        )

        words = content.lower().split()
        for word in list(set(words))[:5]:
            if word not in self.semantic_memory.concepts:
                self.semantic_memory.encode_concept(
                    word,
                    f"Concept from experience: {content[:50]}",
                )

        for i in range(len(words) - 1):
            self.semantic_memory.encode_fact(
                words[i],
                "co_occurred_with",
                words[i + 1],
                confidence=0.3,
            )

        if "learn" in content.lower() or "skill" in content.lower():
            skill_id = f"skill_{episode_trace.trace_id}"
            self.procedural_memory.encode_skill(
                skill_id,
                f"Skill from experience",
                ["Analyze context", "Apply learned knowledge"],
                context_triggers=context_triggers or [],
            )

        self.consolidator.queue_for_consolidation(episode_trace.trace_id)

        self.add_to_working_memory(
            content,
            source_type="experience",
            metadata={"trace_id": episode_trace.trace_id},
        )

        # 2026-08-19: 写后即持久化，保证情景/语义/程序记忆跨重启保留
        self.save()

        return {
            "episode_id": episode_trace.trace_id,
            "encoded_concepts": len(list(set(words))[:5]),
            "triggered_skills": 1 if "learn" in content.lower() or "skill" in content.lower() else 0,
        }

    def encode_concept(
        self,
        concept_id: str,
        definition: str,
        category: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
        related_concepts: Optional[List[str]] = None,
    ) -> None:
        self.semantic_memory.encode_concept(
            concept_id=concept_id,
            definition=definition,
            category=category,
            attributes=attributes,
        )

        if related_concepts:
            for related in related_concepts:
                if related in self.semantic_memory.concepts:
                    self.semantic_memory.encode_fact(
                        concept_id,
                        "related_to",
                        related,
                        confidence=0.7,
                    )

        self.add_to_working_memory(
            f"Concept encoded: {concept_id}",
            source_type="concept",
            metadata={"concept_id": concept_id},
        )

    def encode_skill(
        self,
        skill_id: str,
        name: str,
        steps: List[str],
        context_triggers: Optional[List[str]] = None,
        domain: str = "",
        initial_proficiency: float = 0.5,
    ) -> None:
        self.procedural_memory.encode_skill(
            skill_id=skill_id,
            name=name,
            steps=steps,
            context_triggers=context_triggers,
            domain=domain,
            initial_proficiency=initial_proficiency,
        )

        self.add_to_working_memory(
            f"Skill encoded: {name}",
            source_type="skill",
            metadata={"skill_id": skill_id},
        )

    def query(
        self,
        query_text: str,
        max_results: int = 10,
        include_types: Optional[List[MemoryType]] = None,
    ) -> List[Dict[str, Any]]:
        all_results: List[Dict[str, Any]] = []

        if include_types is None:
            include_types = [MemoryType.EPISODIC, MemoryType.SEMANTIC, MemoryType.PROCEDURAL]

        if MemoryType.EPISODIC in include_types:
            episodes = self.episodic_memory.retrieve_similar(query_text, max_results=max_results)
            for episode in episodes:
                all_results.append({
                    "type": MemoryType.EPISODIC.value,
                    "id": episode.trace_id,
                    "content": episode.content,
                    "strength": episode.strength,
                    "emotional_valence": episode.emotional_valence,
                    "emotional_arousal": episode.emotional_arousal,
                })

        if MemoryType.SEMANTIC in include_types:
            keywords = query_text.lower().split()
            concepts = self.semantic_memory.query(keywords, max_results=max_results)
            for concept_id, score in concepts:
                concept_data = self.semantic_memory.retrieve_concept(concept_id)
                if concept_data:
                    all_results.append({
                        "type": MemoryType.SEMANTIC.value,
                        "id": concept_id,
                        "content": concept_data["definition"],
                        "strength": score,
                        "category": concept_data.get("category"),
                    })

        if MemoryType.PROCEDURAL in include_types:
            skills = self.procedural_memory.retrieve_skill(query_text, max_results=max_results)
            for skill in skills:
                all_results.append({
                    "type": MemoryType.PROCEDURAL.value,
                    "id": skill.get("name", "unknown"),
                    "content": " → ".join(skill.get("steps", [])),
                    "strength": skill.get("proficiency", 0.5),
                    "domain": skill.get("domain", ""),
                })

        return self._rank_results(all_results, query_text)

    def _rank_results(
        self,
        results: List[Dict[str, Any]],
        query_text: str,
    ) -> List[Dict[str, Any]]:
        query_words = set(query_text.lower().split())

        def score_result(result: Dict[str, Any]) -> float:
            content = str(result.get("content", "")).lower()
            content_words = set(content.split())

            similarity = len(query_words & content_words) / max(len(query_words), len(content_words), 1)
            strength = result.get("strength", 0.5)

            type_weights = {
                MemoryType.EPISODIC.value: 1.0,
                MemoryType.SEMANTIC.value: 0.8,
                MemoryType.PROCEDURAL.value: 0.9,
            }
            type_weight = type_weights.get(result.get("type", ""), 0.5)

            return similarity * strength * type_weight

        scored = [(score_result(r), r) for r in results]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r[1] for r in scored]

    def retrieve_context(
        self,
        context_text: str,
        emotional_valence: float = 0.0,
        emotional_arousal: float = 0.0,
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        emotional_results = self.episodic_memory.retrieve_by_emotion(
            emotional_valence,
            emotional_arousal,
            tolerance=0.3,
            max_results=max_results,
        )
        for episode in emotional_results:
            results.append({
                "type": "episodic",
                "id": episode.trace_id,
                "content": episode.content,
                "source": "emotional_match",
            })

        keywords = context_text.lower().split()
        semantic_results = self.semantic_memory.query(keywords, max_results=max_results)
        for concept_id, score in semantic_results:
            concept_data = self.semantic_memory.retrieve_concept(concept_id)
            if concept_data:
                results.append({
                    "type": "semantic",
                    "id": concept_id,
                    "content": concept_data["definition"],
                    "source": "semantic_association",
                })

        skill_results = self.procedural_memory.retrieve_skill(context_text, max_results=max_results)
        for skill in skill_results:
            results.append({
                "type": "procedural",
                "id": skill.get("name", "unknown"),
                "content": " → ".join(skill.get("steps", [])),
                "source": "skill_match",
            })

        return self._rank_results(results, context_text)

    def generate_memory_prompt(
        self,
        context_text: str,
        max_episodes: int = 3,
        max_concepts: int = 3,
        max_skills: int = 2,
    ) -> str:
        context = self.retrieve_context(context_text)

        episodes = [r for r in context if r["type"] == "episodic"][:max_episodes]
        concepts = [r for r in context if r["type"] == "semantic"][:max_concepts]
        skills = [r for r in context if r["type"] == "procedural"][:max_skills]

        prompt_parts = []

        if episodes:
            prompt_parts.append("## Relevant Experiences:")
            for ep in episodes:
                prompt_parts.append(f"- {ep['content']}")

        if concepts:
            prompt_parts.append("\n## Relevant Concepts:")
            for concept in concepts:
                prompt_parts.append(f"- {concept['id']}: {concept['content']}")

        if skills:
            prompt_parts.append("\n## Relevant Skills:")
            for skill in skills:
                prompt_parts.append(f"- {skill['id']}: {skill['content']}")

        if self.working_memory:
            prompt_parts.append("\n## Current Context:")
            for item in list(self.working_memory):
                prompt_parts.append(f"- {item['content']}")

        return "\n".join(prompt_parts) if prompt_parts else "No relevant memory context available."

    def get_recent_episodes(
        self,
        hours: float = 24.0,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        cutoff_time = time.time() - hours * 3600
        recent = [
            t for t in self.episodic_memory.episodes
            if t.timestamp >= cutoff_time
        ]
        recent.sort(key=lambda t: t.timestamp, reverse=True)

        # 工作执行记忆去重: 相同 content 只保留最新一条, 并标注重复次数
        seen = set()
        rows = []
        cnt = {}
        for t in recent:
            cnt[t.content] = cnt.get(t.content, 0) + 1
        for t in recent:
            if t.content in seen:
                continue
            seen.add(t.content)
            rows.append(t)
        return [{
            "id": t.trace_id,
            "content": t.content,
            "timestamp": t.timestamp,
            "emotional_valence": t.emotional_valence,
            "emotional_arousal": t.emotional_arousal,
            "strength": t.strength,
            "repeat": cnt[t.content],
        } for t in rows[:max_results]]

    def get_related_concepts(
        self,
        concept_id: str,
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        activated = self.semantic_memory.spread_activation(
            [concept_id],
            max_concepts=max_results + 1,
        )
        related = [c for c in activated if c[0] != concept_id]

        results = []
        for concept_id_, score in related[:max_results]:
            concept_data = self.semantic_memory.retrieve_concept(concept_id_)
            if concept_data:
                results.append({
                    "id": concept_id_,
                    "definition": concept_data["definition"],
                    "category": concept_data.get("category"),
                    "activation_score": score,
                })

        return results

    def get_relevant_skills(
        self,
        context: str,
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        skills = self.procedural_memory.retrieve_skill(context, max_results=max_results)

        return [{
            "id": skill.get("name", "unknown"),
            "steps": skill.get("steps", []),
            "proficiency": skill.get("proficiency", 0.0),
            "domain": skill.get("domain", ""),
            "context_triggers": skill.get("context_triggers", []),
        } for skill in skills]

    def consolidate(self) -> Dict[str, Any]:
        initial_count = self.consolidator.consolidation_count
        self.consolidator.consolidate()
        new_count = self.consolidator.consolidation_count

        dreams = self.consolidator.get_dream_report(recent_count=1)

        return {
            "consolidation_count": new_count,
            "was_consolidated": new_count > initial_count,
            "dreams_generated": len(dreams),
            "dream_content": dreams,
        }

    def get_dream_report(self, recent_count: int = 5) -> List[str]:
        return self.consolidator.get_dream_report(recent_count=recent_count)

    def get_memory_summary(self) -> Dict[str, Any]:
        return {
            "working_memory_size": len(self.working_memory),
            "episodic_memory_count": len(self.episodic_memory.episodes),
            "semantic_memory_count": len(self.semantic_memory.concepts),
            "procedural_memory_count": len(self.procedural_memory.skills),
            "habit_count": len(self.procedural_memory.habits),
            "consolidation_count": self.consolidator.consolidation_count,
            "dream_count": len(self.consolidator.dream_reports),
            "emotional_state": self.emotional_state.copy(),
        }

    # ── 记忆持久化（save/load）────────────────────────────
    def _serialize(self) -> Dict[str, Any]:
        """把 episodic/semantic/procedural 记忆序列化为可 JSON 化的 dict。"""
        return {
            "episodic": [
                {
                    "trace_id": t.trace_id,
                    "memory_type": t.memory_type.value,
                    "content": t.content,
                    "timestamp": t.timestamp,
                    "emotional_valence": t.emotional_valence,
                    "emotional_arousal": t.emotional_arousal,
                    "rehearsal_count": t.rehearsal_count,
                    "last_accessed": t.last_accessed,
                    "associations": list(t.associations),
                    "source_episode": t.source_episode,
                    "confidence": t.confidence,
                    "decay_rate": t.decay_rate,
                }
                for t in self.episodic_memory.episodes
            ],
            "semantic": {
                "concepts": dict(self.semantic_memory.concepts),
                "relations": [
                    {"subject": k, "edges": [list(e) for e in v]}
                    for k, v in self.semantic_memory.relations.items()
                ],
                "hierarchy": {k: list(v)
                              for k, v in self.semantic_memory.hierarchy.items()},
            },
            "procedural": {
                "skills": dict(self.procedural_memory.skills),
                "habits": dict(self.procedural_memory.habits),
                "automated_responses": dict(
                    self.procedural_memory.automated_responses),
            },
        }

    def save(self) -> bool:
        """原子写持久化文件。失败只记录日志，不抛错（fail-closed）。"""
        try:
            path = self.PERSIST_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._serialize(), ensure_ascii=False, indent=1),
                encoding="utf-8")
            os.replace(tmp, path)
            return True
        except Exception as e:  # noqa: BLE001
            try:
                import logging
                logging.getLogger(__name__).warning(
                    "UnifiedMemory.save failed: %s", e)
            except Exception:
                pass
            return False

    def load(self) -> bool:
        """从持久化文件恢复记忆（含重建索引）。文件缺失/损坏则静默保持空。"""
        path = self.PERSIST_PATH
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 损坏/空 → 忽略
            return False
        try:
            # episodic
            eps = data.get("episodic") or []
            new_episodes = []
            for d in eps:
                try:
                    mt = MemoryType(str(d.get("memory_type") or "episodic"))
                except Exception:
                    mt = MemoryType.EPISODIC
                new_episodes.append(
                    MemoryTrace(
                        trace_id=d.get("trace_id", f"ep_{time.time()}"),
                        memory_type=mt,
                        content=d.get("content", ""),
                        timestamp=float(d.get("timestamp", time.time())),
                        emotional_valence=float(
                            d.get("emotional_valence", 0.0)),
                        emotional_arousal=float(d.get("emotional_arousal", 0.0)),
                        rehearsal_count=int(d.get("rehearsal_count", 0)),
                        last_accessed=float(
                            d.get("last_accessed", time.time())),
                        associations=list(d.get("associations", []) or []),
                        source_episode=d.get("source_episode"),
                        confidence=float(d.get("confidence", 0.5)),
                        decay_rate=float(d.get("decay_rate", 0.01)),
                    ))
            self.episodic_memory.episodes = new_episodes
            # 重建时间/情感索引
            self.episodic_memory._time_index.clear()
            self.episodic_memory._emotion_index.clear()
            for t in new_episodes:
                self.episodic_memory._time_index[t.timestamp].append(
                    t.trace_id)
                self.episodic_memory._emotion_index[
                    self.episodic_memory._emotion_key(
                        t.emotional_valence, t.emotional_arousal)
                ].append(t.trace_id)
            # semantic
            sem = data.get("semantic") or {}
            self.semantic_memory.concepts = dict(sem.get("concepts") or {})
            self.semantic_memory.hierarchy = {
                k: list(v) for k, v in (sem.get("hierarchy") or {}).items()}
            rel = {}
            for r in sem.get("relations") or []:
                rel[str(r.get("subject"))] = [tuple(e) for e in r.get("edges", [])]
            self.semantic_memory.relations = rel
            # procedural
            pro = data.get("procedural") or {}
            self.procedural_memory.skills = dict(pro.get("skills") or {})
            self.procedural_memory.habits = dict(pro.get("habits") or {})
            self.procedural_memory.automated_responses = dict(
                pro.get("automated_responses") or {})
            return True
        except Exception as e:  # noqa: BLE001
            try:
                import logging
                logging.getLogger(__name__).warning(
                    "UnifiedMemory.load restore failed: %s", e)
            except Exception:
                pass
            return False