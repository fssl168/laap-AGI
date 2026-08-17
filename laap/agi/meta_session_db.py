# -*- coding: utf-8 -*-
"""
元学习会话 SQLite 持久化
========================
学习会话除 JSON 持久化（agi_state/meta_learning.json）外，同时写入
SQLite（data/laap.db 的 meta_sessions 表, 2026-08-17 起），便于按领域查询/统计
（如验证 coding/intent 领域的会话积累情况）。

对标 laap/paper_trading/db.py 的 sqlite3 风格：标准库、零新依赖、
db_path 可注入（测试用 tmp 路径）。
"""
import sqlite3
import time
from pathlib import Path


def _default_db_path() -> str:
    """meta_sessions 库路径: 统一到 data/laap.db (2026-08-17 迁移)。

    原 agi_state/meta_sessions.db 已备份为 .bak_20260817; 与 laap.db
    (meta_sessions 主库) 对齐, 数据在 data/laap.db 的 meta_sessions 表。
    """
    from pathlib import Path
    return str(Path(__file__).resolve().parents[2] / "data" / "laap.db")


_SCHEMA = """
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
CREATE INDEX IF NOT EXISTS idx_meta_sessions_ts ON meta_sessions(timestamp);
"""


def init_db(db_path: str = None) -> str:
    """初始化库（建表），返回实际路径。"""
    path = db_path or _default_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
    finally:
        conn.close()
    return path


def insert_session(record, db_path: str = None) -> None:
    """插入一条学习会话记录（record: LearningSessionRecord 或鸭子类型）。"""
    path = init_db(db_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO meta_sessions "
            "(id, concept, strategy, domain, duration_minutes, mastery_before, "
            " mastery_after, gain, successful, timestamp, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                getattr(record, "id", ""),
                getattr(record, "concept", ""),
                getattr(record, "strategy", "").value
                if hasattr(getattr(record, "strategy", ""), "value") else str(getattr(record, "strategy", "")),
                getattr(record, "domain", "general"),
                float(getattr(record, "duration_minutes", 0.0)),
                float(getattr(record, "mastery_before", 0.0)),
                float(getattr(record, "mastery_after", 0.0)),
                float(getattr(record, "gain", 0.0)),
                1 if getattr(record, "successful", False) else 0,
                float(getattr(record, "timestamp", time.time())),
                getattr(record, "notes", ""),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def count_by_domain(db_path: str = None) -> dict:
    """按领域统计会话数（含成功数），供验证数据积累情况。"""
    path = init_db(db_path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT domain, COUNT(*), COALESCE(SUM(successful), 0) "
            "FROM meta_sessions GROUP BY domain"
        ).fetchall()
        return {r[0]: {"count": r[1], "successful": r[2]} for r in rows}
    finally:
        conn.close()


def map_tool(tool_name: str) -> tuple:
    """工具名 → (领域, 学习策略)。复用 continuous_learning 场景语义。

    领域: coding / intent / api / complex / general
    策略: 取自 MetaLearningEngine.LearningStrategy 的合法值。
    """
    name = (tool_name or "").lower()
    if any(k in name for k in ("code", "generate", "verify", "代码", "生成", "patch", "compile")):
        # 验证/测试类 → active_recall（主动验证），生成类 → practical（实践）
        return "coding", "active_recall" if any(
            k in name for k in ("verify", "test", "check")) else "practical"
    if any(k in name for k in ("clarif", "ask_user", "ask_follow", "意图", "澄清", "followup")):
        return "intent", "active_recall"
    if any(k in name for k in ("retry", "api", "fallback", "重试")):
        return "api", "exploratory"
    if any(k in name for k in ("decompose", "分解", "plan")):
        return "complex", "theoretical"
    if any(k in name for k in ("analog", "类比", "map_")):
        return "general", "analogical"
    return "general", "structured"


def record_to_sqlite(meta_engine, tool_name: str, success: bool,
                     db_path: str = None) -> None:
    """工具调用 → 记录学习会话（内存 + JSON save + SQLite）。

    meta_engine: MetaLearningEngine 实例（需已加载）。
    全程容错：任何一步失败都不影响工具调用主流程。
    """
    try:
        domain, strategy = map_tool(tool_name)
        before, after = 0.5, (0.8 if success else 0.3)
        record = meta_engine.record_session(
            concept=f"tool:{tool_name}",
            strategy=strategy,
            duration_minutes=1.0,
            mastery_before=before,
            mastery_after=after,
            difficulty=0.5,
            domain=domain,
            successful=success,
            notes="auto-recorded from tool call",
        )
        try:
            meta_engine.save()
        except Exception:
            pass  # JSON 保存失败不影响 SQLite
        insert_session(record, db_path=db_path)
    except Exception:
        pass  # 记录失败静默，不干扰主流程


def save_session_records(records, db_path: str = None) -> int:
    """批量保存学习会话记录到 meta_sessions 表 (2026-08-17)。

    records: LearningSessionRecord 列表; 走 cognitive_db 双后端(PG/SQLite)。
    """
    try:
        from laap.agi.cognitive_db import upsert
        for r in records:
            upsert("meta_sessions", {
                "id": getattr(r, "id", ""),
                "concept": getattr(r, "concept", ""),
                "strategy": (r.strategy.value if hasattr(r.strategy, "value")
                             else str(getattr(r, "strategy", ""))),
                "domain": getattr(r, "domain", "general"),
                "duration_minutes": float(getattr(r, "duration_minutes", 0.0)),
                "mastery_before": float(getattr(r, "mastery_before", 0.0)),
                "mastery_after": float(getattr(r, "mastery_after", 0.0)),
                "gain": float(getattr(r, "gain", 0.0)),
                "successful": 1 if getattr(r, "successful", False) else 0,
                "timestamp": float(getattr(r, "timestamp", time.time())),
                "notes": getattr(r, "notes", ""),
            })
        return len(records)
    except Exception as e:
        logger = __import__("logging").getLogger("meta_session_db")
        logger.warning(f"save_session_records failed: {e}")
        return 0


def load_session_records(limit: int = 200, db_path: str = None):
    """从 meta_sessions 表读取学习会话记录 (2026-08-17)。

    返回 LearningSessionRecord 列表; 走 cognitive_db 双后端。
    """
    try:
        from laap.agi.cognitive_db import fetch_all
        rows = fetch_all("meta_sessions", limit=limit)
        from laap.agi.meta_learning import LearningSessionRecord, LearningStrategy
        out = []
        for r in rows:
            try:
                strat = LearningStrategy(r.get("strategy", "structured"))
            except ValueError:
                strat = LearningStrategy.STRUCTURED
            out.append(LearningSessionRecord(
                id=r.get("id", ""), concept=r.get("concept", ""),
                strategy=strat, domain=r.get("domain", "general"),
                duration_minutes=float(r.get("duration_minutes", 0.0)),
                mastery_before=float(r.get("mastery_before", 0.0)),
                mastery_after=float(r.get("mastery_after", 0.0)),
                gain=float(r.get("gain", 0.0)),
                gain_rate=0.0,
                difficulty=0.5,
                successful=bool(r.get("successful", False)),
                timestamp=float(r.get("timestamp", time.time())),
                notes=r.get("notes", ""),
            ))
        return out
    except Exception as e:
        logger = __import__("logging").getLogger("meta_session_db")
        logger.warning(f"load_session_records failed: {e}")
        return []
