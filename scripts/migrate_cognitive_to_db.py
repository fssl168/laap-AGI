# -*- coding: utf-8 -*-
"""认知引擎 JSON → 关系库迁移 (2026-08-17)。

三模块 9 表: rsi_engine(3) + evolution_audit(1) + meta_learning(4+1已有)
本地: data/laap.db | 远程: PG16 laap 库

表清单:
  rsi_params          (OptimizableParameter)
  rsi_attempts        (SelfImprovementAttempt)
  rsi_goals           (LearningGoal)
  evolution_audit     (EvolutionAuditLog.record)
  meta_sessions       (已有, 补 gain_rate/difficulty 列)
  strategy_efficacy   (StrategyEfficacy)
  knowledge_transfers (KnowledgeTransfer)
  meta_learning_meta  (计数器: total_sessions/strategy_switches/transfer_discoveries)
"""
import json
import os
import sqlite3
from pathlib import Path

import psycopg

LOCAL_DB = r"D:\laap-AGI\data\laap.db"
PG = dict(host="192.168.88.251", port=54322, user="fileclaw",
          password="fileclaw_secret", dbname="laap")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta_sessions (
    id TEXT PRIMARY KEY,
    concept TEXT NOT NULL,
    strategy TEXT NOT NULL,
    domain TEXT NOT NULL,
    duration_minutes REAL,
    mastery_before REAL,
    mastery_after REAL,
    gain REAL,
    successful INTEGER,
    timestamp REAL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_meta_sessions_domain ON meta_sessions(domain);
CREATE TABLE IF NOT EXISTS rsi_params (
    name TEXT PRIMARY KEY,
    category TEXT DEFAULT 'psi',
    current_value REAL DEFAULT 0.5,
    min_value REAL DEFAULT 0.0,
    max_value REAL DEFAULT 1.0,
    step_size REAL DEFAULT 0.05,
    description TEXT DEFAULT '',
    last_optimized REAL DEFAULT 0.0,
    optimization_count INTEGER DEFAULT 0,
    performance_history TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS rsi_attempts (
    id TEXT PRIMARY KEY,
    target TEXT DEFAULT '',
    category TEXT DEFAULT 'psi',
    old_value REAL DEFAULT 0.0,
    new_value REAL DEFAULT 0.0,
    rationale TEXT DEFAULT '',
    expected_improvement REAL DEFAULT 0.0,
    actual_improvement REAL DEFAULT 0.0,
    success INTEGER DEFAULT 0,
    reverted INTEGER DEFAULT 0,
    timestamp REAL,
    evaluation_period REAL DEFAULT 3600.0
);
CREATE INDEX IF NOT EXISTS idx_rsi_attempts_target ON rsi_attempts(target);
CREATE TABLE IF NOT EXISTS rsi_goals (
    id TEXT PRIMARY KEY,
    description TEXT DEFAULT '',
    domain TEXT DEFAULT 'general',
    target_mastery REAL DEFAULT 0.8,
    current_mastery REAL DEFAULT 0.0,
    priority REAL DEFAULT 0.5,
    strategy TEXT DEFAULT 'structured',
    motivation TEXT DEFAULT '',
    status TEXT DEFAULT 'proposed',
    created_at REAL,
    deadline REAL
);
CREATE TABLE IF NOT EXISTS evolution_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    decision TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    mutation_id TEXT DEFAULT '',
    target TEXT DEFAULT '',
    status TEXT DEFAULT '',
    meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_evolution_audit_target ON evolution_audit(target);
CREATE INDEX IF NOT EXISTS idx_evolution_audit_decision ON evolution_audit(decision);
CREATE TABLE IF NOT EXISTS strategy_efficacy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT DEFAULT '',
    domain TEXT DEFAULT 'general',
    avg_gain_rate REAL DEFAULT 0.0,
    avg_retention REAL DEFAULT 0.0,
    times_used INTEGER DEFAULT 0,
    success_rate REAL DEFAULT 0.0,
    best_diff_min REAL DEFAULT 0.1,
    best_diff_max REAL DEFAULT 0.9,
    last_used REAL DEFAULT 0.0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_efficacy_key ON strategy_efficacy(strategy, domain);
CREATE TABLE IF NOT EXISTS knowledge_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_domain TEXT DEFAULT '',
    target_domain TEXT DEFAULT '',
    source_concept TEXT DEFAULT '',
    target_concept TEXT DEFAULT '',
    similarity REAL DEFAULT 0.0,
    transfer_effect REAL DEFAULT 0.0,
    confidence REAL DEFAULT 0.5,
    discovered_at REAL
);
CREATE TABLE IF NOT EXISTS meta_learning_meta (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


def _sqlite_exec(conn, sql):
    conn.executescript(sql)


def _pg_exec(cur, sql):
    cur.execute(sql)


SCHEMA_PG = SCHEMA.replace("AUTOINCREMENT", "GENERATED ALWAYS AS IDENTITY")


def init_local():
    if os.path.exists(LOCAL_DB):
        os.remove(LOCAL_DB)  # 全新重建(数据从 JSON 迁移)
    conn = sqlite3.connect(LOCAL_DB)
    conn.executescript(SCHEMA)
    conn.commit()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1").fetchall()]
    print("本地 laap.db 表:", tables)
    conn.close()


def init_pg():
    conn = psycopg.connect(**PG, connect_timeout=5)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(SCHEMA_PG)
    cur.execute("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY 1")
    print("PG laap 库表:", [r[0] for r in cur.fetchall()])
    cur.close()
    conn.close()


def migrate_from_json():
    """迁移现有 JSON 数据 → 本地 laap.db (双端各自从 JSON 迁, 保证同源)。"""
    conn = sqlite3.connect(LOCAL_DB)

    # ── meta_learning.json ──
    ml_path = Path(r"D:\laap-AGI\agi_state\meta_learning.json")
    if ml_path.exists():
        data = json.loads(ml_path.read_text(encoding="utf-8"))
        # sessions → meta_sessions
        for s in data.get("sessions", []):
            conn.execute(
                "INSERT OR IGNORE INTO meta_sessions (id, concept, strategy, domain,"
                " duration_minutes, mastery_before, mastery_after, gain, successful,"
                " timestamp, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (s.get("id", ""), s.get("concept", ""), s.get("strategy", ""),
                 s.get("domain", ""), s.get("duration_minutes", 0.0),
                 s.get("mastery_before", 0.0), s.get("mastery_after", 0.0),
                 s.get("gain", 0.0), 1 if s.get("successful") else 0,
                 s.get("timestamp", 0.0), s.get("notes", "")))
        # strategy_efficacy
        for k, v in data.get("strategy_efficacy", {}).items():
            if isinstance(v, dict):
                conn.execute(
                    "INSERT OR IGNORE INTO strategy_efficacy (strategy, domain,"
                    " avg_gain_rate, avg_retention, times_used, success_rate,"
                    " best_diff_min, best_diff_max, last_used) VALUES (?,?,?,?,?,?,?,?,?)",
                    (k.split(":")[0] if ":" in k else k,
                     v.get("domain", "general"), v.get("avg_gain", 0.0),
                     v.get("avg_retention", 0.0), v.get("times_used", 0),
                     v.get("success_rate", 0.0),
                     v.get("difficulty_range", [0.1, 0.9])[0],
                     v.get("difficulty_range", [0.1, 0.9])[1],
                     v.get("last_used", 0.0)))
        # transfers
        for t in data.get("transfers", []):
            if isinstance(t, dict):
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_transfers (source_domain,"
                    " target_domain, source_concept, target_concept, similarity,"
                    " transfer_effect, confidence, discovered_at) VALUES (?,?,?,?,?,?,?,?)",
                    (t.get("source_domain", ""), t.get("target_domain", ""),
                     t.get("source_concept", ""), t.get("target_concept", ""),
                     t.get("similarity", 0.0), t.get("transfer_effect", 0.0),
                     t.get("confidence", 0.5), t.get("discovered_at", 0.0)))
        # meta
        for k in ["total_sessions", "strategy_switches", "transfer_discoveries"]:
            conn.execute("INSERT OR REPLACE INTO meta_learning_meta (key, value) VALUES (?, ?)",
                         (k, str(data.get(k, 0))))
        print(f"meta_learning: {len(data.get('sessions', []))} sessions, "
              f"{len(data.get('strategy_efficacy', {}))} efficacy, "
              f"{len(data.get('transfers', []))} transfers")

    # ── evolution_audit.jsonl ──
    ea_path = Path(r"D:\laap-AGI\state\evolution_audit.jsonl")
    if ea_path.exists():
        n = 0
        for line in ea_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO evolution_audit (ts, decision, reason,"
                " mutation_id, target, status, meta) VALUES (?,?,?,?,?,?,?)",
                (e.get("ts", 0.0), e.get("decision", ""), e.get("reason", ""),
                 e.get("mutation_id", ""), e.get("target", ""),
                 e.get("status", ""), json.dumps(e.get("meta", {}), ensure_ascii=False)))
            n += 1
        print(f"evolution_audit: {n} 条")

    # ── rsi_engine.json (若存在) ──
    rsi_path = Path(os.path.expanduser("~/.laap/rsi_engine.json"))
    if rsi_path.exists():
        data = json.loads(rsi_path.read_text(encoding="utf-8"))
        for p in data.get("parameters", []):
            conn.execute(
                "INSERT OR IGNORE INTO rsi_params (name, category, current_value,"
                " min_value, max_value, step_size, description, last_optimized,"
                " optimization_count, performance_history) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (p.get("name", ""), p.get("category", "psi"),
                 p.get("current_value", 0.5), p.get("min_value", 0.0),
                 p.get("max_value", 1.0), p.get("step_size", 0.05),
                 p.get("description", ""), p.get("last_optimized", 0.0),
                 p.get("optimization_count", 0),
                 json.dumps(p.get("performance_history", []))))
        for a in data.get("attempts", []):
            conn.execute(
                "INSERT OR IGNORE INTO rsi_attempts (id, target, category, old_value,"
                " new_value, rationale, expected_improvement, actual_improvement,"
                " success, reverted, timestamp, evaluation_period) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (a.get("id", ""), a.get("target", ""), a.get("category", "psi"),
                 a.get("old_value", 0.0), a.get("new_value", 0.0),
                 a.get("rationale", ""), a.get("expected_improvement", 0.0),
                 a.get("actual_improvement", 0.0), 1 if a.get("success") else 0,
                 1 if a.get("reverted") else 0, a.get("timestamp", 0.0),
                 a.get("evaluation_period", 3600.0)))
        for g in data.get("goals", []):
            conn.execute(
                "INSERT OR IGNORE INTO rsi_goals (id, description, domain, target_mastery,"
                " current_mastery, priority, strategy, motivation, status, created_at,"
                " deadline) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (g.get("id", ""), g.get("description", ""), g.get("domain", "general"),
                 g.get("target_mastery", 0.8), g.get("current_mastery", 0.0),
                 g.get("priority", 0.5), g.get("strategy", "structured"),
                 g.get("motivation", ""), g.get("status", "proposed"),
                 g.get("created_at", 0.0), g.get("deadline")))
        print(f"rsi_engine: {len(data.get('parameters', []))} params, "
              f"{len(data.get('attempts', []))} attempts, {len(data.get('goals', []))} goals")
    else:
        print("rsi_engine.json 不存在(跳过)")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_local()
    init_pg()
    migrate_from_json()
    print("完成")
