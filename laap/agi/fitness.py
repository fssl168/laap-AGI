"""
LAAP AGI — 适应度评估器 (FitnessEvaluator) — M2 True RSI
========================================================
为代码级自改进提供可测量的"更好"标准。

适应度合成 (0~1):
  - 测试通过率    weight 0.4  (pytest passed/total, 确定性核心信号)
  - 认知链延迟    weight 0.3  (process_with_laap 平均延迟, 越低越好)
  - 记忆召回命中  weight 0.3  (语义记忆召回非空率)

用法:
    fe = FitnessEvaluator(repo_root=...)
    score = fe.composite()
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("laap.agi.fitness")


class FitnessEvaluator:
    """组合适应度评估器 (M2)。"""

    def __init__(self, repo_root: str = "", api_base: str = "http://127.0.0.1:11546"):
        self.repo_root = repo_root or os.environ.get("LAAP_ROOT", str(Path.cwd()))
        self.api_base = api_base
        # 权重
        self.W_TEST = 0.4
        self.W_LATENCY = 0.3
        self.W_MEMORY = 0.3

    # ════════════════════════════════════════════════════════
    # 分量 1: 测试通过率
    # ════════════════════════════════════════════════════════

    def test_pass_rate(self, timeout: int = 300) -> float:
        """运行 pytest tests -q, 返回 passed/total (无测试则 0.0)。"""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "tests", "-q"],
                cwd=self.repo_root, capture_output=True, text=True,
                timeout=timeout,
            )
            m = re.search(r"(\d+) passed", result.stdout)
            passed = int(m.group(1)) if m else 0
            m = re.search(r"(\d+) failed", result.stdout)
            failed = int(m.group(1)) if m else 0
            total = passed + failed
            if total == 0:
                return 0.0
            return passed / total
        except Exception as e:
            logger.warning(f"test_pass_rate failed: {e}")
            return 0.0

    # ════════════════════════════════════════════════════════
    # 分量 2: 认知链延迟 (越低越好, 归一化到 [0,1])
    # ════════════════════════════════════════════════════════

    def avg_latency_ms(self, samples: int = 5, timeout: int = 60) -> float:
        """采样 process_with_laap 平均延迟 (ms)。

        归一化: score = clamp(1 - latency/5000, 0, 1)
        (5 秒以上视为退化, 得 0; 1 秒内接近满分)
        """
        latencies: List[float] = []
        try:
            from laap_brain.api import process_with_laap
            for _ in range(samples):
                t0 = time.time()
                process_with_laap(
                    [{"role": "user", "content": "测试"}],
                    model="laap-core",
                )
                latencies.append((time.time() - t0) * 1000)
        except Exception as e:
            logger.warning(f"avg_latency_ms failed: {e}")
            return 0.0
        if not latencies:
            return 0.0
        avg = sum(latencies) / len(latencies)
        return max(0.0, min(1.0, 1.0 - avg / 5000.0))

    # ════════════════════════════════════════════════════════
    # 分量 3: 记忆召回命中率
    # ════════════════════════════════════════════════════════

    def memory_recall_hit_rate(self, queries: Optional[List[str]] = None) -> float:
        """抽样语义记忆召回, 返回非空命中率。"""
        queries = queries or ["我的自选股", "Aris 的状态", "最近发生了什么"]
        hits = 0
        tried = 0
        try:
            import json
            import urllib.request
            for q in queries:
                tried += 1
                try:
                    payload = json.dumps({"query": q, "limit": 1}).encode()
                    req = urllib.request.Request(
                        f"{self.api_base}/v1/recall_memory", data=payload,
                        headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        d = json.load(r)
                    if d.get("memories"):
                        hits += 1
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"memory_recall_hit_rate failed: {e}")
            return 0.0
        if tried == 0:
            return 0.0
        return hits / tried

    # ════════════════════════════════════════════════════════
    # 合成
    # ════════════════════════════════════════════════════════

    def composite(self, components: bool = False) -> Dict[str, float]:
        """加权合成适应度分数。

        Args:
            components: True 时返回各分量明细 (便于诊断)
        """
        test = self.test_pass_rate()
        latency = self.avg_latency_ms()
        memory = self.memory_recall_hit_rate()
        score = (
            self.W_TEST * test
            + self.W_LATENCY * latency
            + self.W_MEMORY * memory
        )
        if components:
            return {
                "score": round(score, 4),
                "test_pass_rate": round(test, 4),
                "avg_latency_score": round(latency, 4),
                "memory_recall_hit_rate": round(memory, 4),
            }
        return {"score": round(score, 4)}
