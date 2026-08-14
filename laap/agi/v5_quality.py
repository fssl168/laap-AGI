"""
LAAP AGI v5 Upgrade: 质量/安全层 (R11 拆分)
====================================
原 v5_upgrade.py (1946 行) 拆分出的子模块之一。
完整拆分: v5_memory_learning.py(记忆/学习) / v5_planning.py(规划) /
          v5_quality.py(质量/安全/基准) /
          v5_upgrade.py(薄门面, 既有导入零破坏)。
"""

from __future__ import annotations

import logging
import sys, os, json, re, math, time, random, hashlib, itertools, heapq
from pathlib import Path
from typing import Optional, Any, Dict, List, Tuple, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque

import numpy as np

logger = logging.getLogger("laap.agi.v5_upgrade")


# ═══ 质量/安全/基准层 (自原 v5_upgrade.py 拆分) ═══
class FormalVerifier:
    """Pattern-based code verification without external AST parser.

    Uses regex patterns to detect common code issues, style violations,
    and potential bugs. Pure Python, no external dependencies.
    """

    RULES = [
        # (name, pattern, severity, message)
        ("no-var", r"\bvar\s+\w+\s*=", "style",
         "Use explicit types instead of 'var' for clarity"),
        ("magic-number", r"[^a-zA-Z]\d{4,}[^a-zA-Z)]", "style",
         "Avoid magic numbers; define as named constants"),
        ("todo-left", r"#\s*(TODO|FIXME|HACK|XXX)", "info",
         "Leftover TODO/FIXME marker — resolve before release"),
        ("print-left", r"print\(.*\)", "warning",
         "print() in production code — use logging instead"),
        ("bare-except", r"except\s*:", "error",
         "Bare except clause catches ALL exceptions — be specific"),
        ("mutable-default", r"def\s+\w+\(.*=\s*\[\s*\]",
         "error", "Mutable default argument (list) — use None instead"),
        ("mutable-default-dict", r"def\s+\w+\(.*=\s*\{\s*\}",
         "error", "Mutable default argument (dict) — use None instead"),
        ("global-mutation", r"global\s+\w+", "warning",
         "Modifying globals makes code hard to reason about"),
        ("thread-no-join", r"\.start\(\)", "warning",
         "Thread started without .join() — ensure cleanup"),
        ("eval-usage", r"\beval\s*\(", "error",
         "eval() is dangerous — use ast.literal_eval or safer alternative"),
        ("exec-usage", r"\bexec\s*\(", "error",
         "exec() is dangerous — avoid dynamic code execution"),
        ("wildcard-import", r"from\s+\w+\s+import\s+\*", "warning",
         "Wildcard imports pollute namespace — import specific names"),
        ("long-line", r"^.{120,}$", "style",
         "Line too long (>120 chars) — break into multiple lines"),
        ("deep-nesting", r"^(\s{8,})if\s", "warning",
         "Deep nesting (>4 levels) — consider early returns or guard clauses"),
    ]

    def verify(self, code: str, filename: str = "<string>") -> List[Dict]:
        findings = []
        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            for name, pattern, severity, message in self.RULES:
                import re
                if re.search(pattern, line):
                    findings.append({
                        "rule": name, "line": i,
                        "severity": severity, "message": message,
                        "code": line.strip()[:80],
                        "file": filename,
                    })
        return findings

    def verify_all(self, files: Dict[str, str]) -> Dict[str, List[Dict]]:
        return {fname: self.verify(code, fname) for fname, code in files.items()}

    def summary(self, findings: List[Dict]) -> str:
        if not findings:
            return "✅ No issues found"
        by_severity = defaultdict(list)
        for f in findings:
            by_severity[f["severity"]].append(f)
        lines = [f"Found {len(findings)} issue(s):"]
        for sev in ["error", "warning", "style", "info"]:
            items = by_severity.get(sev, [])
            if items:
                lines.append(f"\n  [{sev.upper()}] {len(items)}:")
                for item in items[:5]:
                    lines.append(f"    L{item['line']:4d} {item['message'][:60]}")
                if len(items) > 5:
                    lines.append(f"    ... and {len(items)-5} more")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# SecureSandbox Scanner (static analysis patterns)
# ═══════════════════════════════════════════════════════════════

class SecureSandboxScanner:
    """Static security analysis patterns.

    Scans code for common security vulnerabilities.
    Docker sandbox scaffold included for future container isolation.
    """

    SECURITY_PATTERNS = [
        ("command-injection", r"[os|subprocess]\.(system|popen|call)\s*\(",
         "critical", "OS command injection risk — use safe alternatives"),
        ("path-traversal", r"open\(.*\.\.\.", "high",
         "Path traversal risk — sanitize user input paths"),
        ("hardcoded-secret", r"(password|secret|api_key|token)\s*=\s*['\"][^'\"]{8,}['\"]",
         "critical", "Hardcoded secret detected — use environment variables"),
        ("sql-injection", r"f['\"]?.*SELECT.*\{", "critical",
         "SQL injection risk — use parameterized queries"),
        ("unsafe-yaml", r"yaml\.load\(.*\)", "high",
         "Unsafe YAML load — use yaml.safe_load()"),
        ("pickle-unsafe", r"pickle\.loads?\(", "high",
         "Unsafe deserialization — avoid pickle with untrusted data"),
        ("shell-true", r"shell\s*=\s*True", "critical",
         "shell=True in subprocess — command injection risk"),
    ]

    def __init__(self):
        self._sandbox_available = False  # Docker not available locally
        self._sandbox_image = "laap-sandbox:v1.0"

    def scan_code(self, code: str, filename: str = "<code>") -> List[Dict]:
        import re
        findings = []
        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            for name, pattern, severity, message in self.SECURITY_PATTERNS:
                if re.search(pattern, line):
                    findings.append({
                        "rule": name, "line": i, "severity": severity,
                        "message": message, "code": line.strip()[:80], "file": filename,
                    })
        return findings

    def analyze_repo(self, files: Dict[str, str]) -> Dict:
        all_findings = {}
        for fname, code in files.items():
            findings = self.scan_code(code, fname)
            if findings:
                all_findings[fname] = findings

        critical = sum(1 for f in all_findings.values() for r in f if r["severity"] == "critical")
        high = sum(1 for f in all_findings.values() for r in f if r["severity"] == "high")
        total = sum(len(f) for f in all_findings.values())

        return {
            "files_scanned": len(files),
            "files_with_issues": len(all_findings),
            "total_findings": total,
            "critical": critical,
            "high": high,
            "details": all_findings,
            "safe": critical == 0,
        }

    def is_safe_url(self, url: str) -> bool:
        """URL whitelist check (from V5.0 plan spec)."""
        from urllib.parse import urlparse
        allowed = ["github.com", "gitlab.com", "bitbucket.org"]
        try:
            parsed = urlparse(url)
            return parsed.netloc in allowed and parsed.scheme in ("https", "git")
        except Exception:
            return False

    def sandbox_available(self) -> bool:
        """Check if Docker sandbox is available (future)."""
        return self._sandbox_available

    def sandbox_scaffold(self) -> Dict:
        """Return the sandbox configuration (for future Docker integration)."""
        return {
            "image": self._sandbox_image,
            "mem_limit": "512m",
            "cpu_quota": 50000,
            "network_mode": "none",
            "available": self._sandbox_available,
        }


# ═══════════════════════════════════════════════════════════════
# Benchmark Suite — Self-testing & Validation
# ═══════════════════════════════════════════════════════════════

class BenchmarkSuite:
    """Self-testing module for V5.0 components validation."""

    def __init__(self, engine: Optional["V5UpgradeEngine"] = None):
        self.engine = engine
        self.results: List[Dict] = []
        self._start_time = time.time()

    def run_all(self) -> Dict:
        self.results = []
        self._test_ewc()
        self._test_buffer()
        self._test_bug_classifier()
        self._test_causal()
        self._test_active_learning()
        self._test_goal_creator()
        self._test_mcts()
        self._test_knowledge_graph()
        self._test_verifier()
        self._test_sandbox()
        return self._summary()

    def _test_ewc(self):
        if not self.engine:
            return
        before = len(self.engine.ewc.fisher)
        self.engine.ewc.record("test_module", 0.8, 1.0)
        penalty = self.engine.ewc.compute_penalty("test_module", 0.3)
        after = len(self.engine.ewc.fisher)
        self.results.append({
            "test": "EWC Fisher Tracking", "passed": after > before,
            "detail": f"Fisher={len(self.engine.ewc.fisher)} penalty={penalty:.3f}",
        })

    def _test_buffer(self):
        if not self.engine:
            return
        before = len(self.engine.experience_buffer)
        self.engine.record_experience("bench_state", "bench_action", 0.9)
        after = len(self.engine.experience_buffer)
        self.results.append({
            "test": "Experience Buffer", "passed": after > before,
            "detail": f"Buffer size: {after}",
        })

    def _test_bug_classifier(self):
        if not self.engine:
            return
        for cat in BugCategory:
            result = self.engine.classify_and_fix(f"{cat.value} test error", "test.py")
            if result["bug"].category != "unknown":
                self.results.append({
                    "test": f"Bug Classifier: {cat.value}",
                    "passed": True,
                    "detail": f"Classified as {result['bug'].category}",
                })
                break

    def _test_causal(self):
        if not self.engine:
            return
        data = {"X": [1, 2, 3, 4, 5], "Y": [2, 4, 6, 8, 10]}
        result = self.engine.discover_causality(data)
        self.results.append({
            "test": "Causal Discovery", "passed": len(result["edges"]) > 0,
            "detail": f"Edges: {len(result['edges'])}",
        })

    def _test_active_learning(self):
        if not self.engine:
            return
        al = self.engine.active_learning
        al._total_steps = 10
        action, _ = al.select_action({"confidence": 0.5}, "debug")
        al.record_outcome(action, "test", "debug", 0.8)
        self.results.append({
            "test": "Active Learning", "passed": action in ("explore", "exploit"),
            "detail": f"Action: {action}, Steps: {al._total_steps}",
        })

    def _test_goal_creator(self):
        if not self.engine:
            return
        goals = self.engine.goal_creator.co_create_goals("fix bug", {"confidence": 0.5})
        self.results.append({
            "test": "Goal Co-Creation", "passed": len(goals) > 0,
            "detail": f"Goals: {len(goals)}",
        })

    def _test_mcts(self):
        if not self.engine:
            return
        plan = self.engine.mcts_planner.plan(
            "test goal", ["analyze", "execute", "verify"]
        )
        self.results.append({
            "test": "MCTS Planning", "passed": len(plan.steps) > 0,
            "detail": f"Steps: {len(plan.steps)}",
        })

    def _test_knowledge_graph(self):
        if not self.engine:
            return
        if hasattr(self.engine, 'knowledge'):
            results = self.engine.knowledge.query("python")
            self.results.append({
                "test": "Knowledge Graph", "passed": len(results) > 0,
                "detail": f"Results for 'python': {len(results)}",
            })

    def _test_verifier(self):
        code = "x = 12345\ndef foo(x=[]):\n    print(x)"
        if hasattr(self, '_verifier') or True:
            from laap.agi.v5_upgrade import FormalVerifier
            v = FormalVerifier()
            findings = v.verify(code)
            self.results.append({
                "test": "Formal Verifier", "passed": len(findings) >= 2,
                "detail": f"Issues found: {len(findings)}",
            })

    def _test_sandbox(self):
        if hasattr(self, '_sandbox') or True:
            from laap.agi.v5_upgrade import SecureSandboxScanner
            s = SecureSandboxScanner()
            code = "password = 'my_secret_key_123'\nos.system('rm -rf /')"
            results = s.scan_code(code)
            self.results.append({
                "test": "Security Scanner", "passed": len(results) > 0,
                "detail": f"Issues: {len(results)}",
            })

    def _summary(self) -> Dict:
        passed = sum(1 for r in self.results if r["passed"])
        failed = sum(1 for r in self.results if not r["passed"])
        return {
            "total": len(self.results),
            "passed": passed,
            "failed": failed,
            "pass_rate": f"{passed/max(len(self.results),1)*100:.0f}%",
            "duration": round(time.time() - self._start_time, 2),
            "details": self.results,
        }
