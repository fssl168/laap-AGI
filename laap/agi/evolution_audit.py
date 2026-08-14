"""
LAAP AGI — 进化治理审计 (EvolutionAuditLog) — M3 True RSI
==========================================================
为代码级自改进提供治理与可观测性:
  - 每次 mutation 全生命周期审计 (JSONL, 可追溯)
  - 冷却期校验 (同一目标 N 小时内不重复修改, 防抖动)
  - 部署决策记录 (谁批准/拒绝, 理由)

存储: <LAAP_ROOT>/state/evolution_audit.jsonl
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("laap.agi.evolution_audit")


class EvolutionAuditLog:
    """进化审计日志 (M3 治理)。"""

    def __init__(self, repo_root: str = "", cooldown_hours: float = 24.0):
        self.repo_root = repo_root or os.environ.get("LAAP_ROOT", str(Path.cwd()))
        self.cooldown_hours = cooldown_hours
        self._path = Path(self.repo_root) / "state" / "evolution_audit.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ════════════════════════════════════════════════════════
    # 写入
    # ════════════════════════════════════════════════════════

    def record(self, mutation: Any, decision: str,
               reason: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
        """记录一次 mutation 生命周期事件。

        Args:
            mutation: CodeMutation 对象 (或 dict)
            decision: proposed / test_passed / approved / rejected / deployed /
                      rolled_back
            reason: 决策理由
            meta: 附加信息
        """
        entry = {
            "ts": time.time(),
            "decision": decision,
            "reason": reason,
            "mutation_id": getattr(mutation, "id", "") if not isinstance(mutation, dict)
                          else mutation.get("id", ""),
            "target": self._target_path(mutation),
            "status": getattr(mutation, "status", "").value
                      if hasattr(getattr(mutation, "status", ""), "value") else "",
        }
        if isinstance(mutation, dict):
            entry["status"] = mutation.get("status", "")
        if meta:
            entry["meta"] = meta
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.warning(f"Evolution audit write failed: {e}")

    @staticmethod
    def _target_path(mutation: Any) -> str:
        """从 mutation (对象或 dict) 提取目标文件路径 (字符串)。"""
        tgt = mutation.get("target") if isinstance(mutation, dict) else getattr(mutation, "target", None)
        if tgt is None:
            return ""
        if isinstance(tgt, str):
            return tgt
        # CodeTarget 对象
        path = getattr(tgt, "file_path", "") or str(tgt)
        return path

    # ════════════════════════════════════════════════════════
    # 冷却期
    # ════════════════════════════════════════════════════════

    def cooldown_check(self, target_path: str) -> Tuple[bool, float]:
        """检查目标是否在冷却期内。

        Returns (in_cooldown, remaining_hours).
        冷却期内返回 True — 应拒绝新的 mutation 提案。
        """
        now = time.time()
        for entry in self.query(limit=500):
            if (entry.get("target") or "") == target_path:
                elapsed_h = (now - entry.get("ts", 0)) / 3600.0
                if elapsed_h < self.cooldown_hours:
                    return True, self.cooldown_hours - elapsed_h
        return False, 0.0

    # ════════════════════════════════════════════════════════
    # 查询
    # ════════════════════════════════════════════════════════

    def query(self, limit: int = 100) -> List[Dict[str, Any]]:
        """读取最近 N 条审计记录 (倒序)。"""
        if not self._path.exists():
            return []
        entries = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            return []
        return entries[-limit:][::-1]

    def stats(self) -> Dict[str, Any]:
        """审计统计。"""
        entries = self.query(limit=100000)
        decisions: Dict[str, int] = {}
        for e in entries:
            d = e.get("decision", "unknown")
            decisions[d] = decisions.get(d, 0) + 1
        return {
            "total_entries": len(entries),
            "by_decision": decisions,
            "log_path": str(self._path),
        }
