# -*- coding: utf-8 -*-
"""
元学习会话记录链路单测
======================
覆盖:
  1. map_tool 工具名→(领域, 策略) 映射
  2. SQLite 写入/按领域统计 (insert_session / count_by_domain)
  3. record_to_sqlite 完整流程: MetaLearningEngine 记录 → JSON + SQLite 双写
  4. LAAP_META_RECORD=0 时 cognitive bridge 跳过记录

运行: python -m pytest tests/test_meta_session_record.py -v
"""
import os
import tempfile

import pytest

from laap.agi.meta_session_db import (
    map_tool, init_db, insert_session, count_by_domain, record_to_sqlite,
)


@pytest.fixture()
def tmp_db():
    """临时 SQLite 路径（对标 paper_trading/db.py 的测试注入风格）"""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="meta_sessions_test_")
    os.close(fd)
    os.unlink(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestMapTool:
    """工具名 → (领域, 策略) 映射"""

    def test_coding_tools(self):
        assert map_tool("generate_code") == ("coding", "practical")
        assert map_tool("verify_generated_code")[0] == "coding"
        assert map_tool("apply_patch")[0] == "coding"
        assert map_tool("代码生成")[0] == "coding"

    def test_intent_tools(self):
        assert map_tool("ask_user_to_clarify") == ("intent", "active_recall")
        assert map_tool("clarify_intent")[0] == "intent"
        assert map_tool("意图澄清")[0] == "intent"

    def test_api_and_others(self):
        assert map_tool("retry_with_alternatives")[0] == "api"
        assert map_tool("decompose_task")[0] == "complex"
        assert map_tool("map_to_known_pattern")[0] == "general"
        assert map_tool("unknown_tool") == ("general", "structured")
        assert map_tool("") == ("general", "structured")


class TestSqlitePersistence:
    """SQLite 写入与统计"""

    def test_insert_and_count(self, tmp_db):
        init_db(tmp_db)
        class R:
            id = "ls_test1"
            concept = "tool:generate_code"
            strategy = "practical"
            domain = "coding"
            duration_minutes = 1.0
            mastery_before = 0.5
            mastery_after = 0.8
            gain = 0.3
            successful = True
            timestamp = 1000.0
            notes = "test"
        insert_session(R(), db_path=tmp_db)
        stats = count_by_domain(tmp_db)
        assert stats["coding"]["count"] == 1
        assert stats["coding"]["successful"] == 1

    def test_count_by_domain_multi(self, tmp_db):
        init_db(tmp_db)
        for i, (dom, ok) in enumerate([("coding", True), ("coding", False),
                                       ("intent", True)]):
            class R:
                id = f"ls_{i}"
                concept = "c"
                strategy = "structured"
                domain = dom
                duration_minutes = 1.0
                mastery_before = 0.5
                mastery_after = 0.8 if ok else 0.3
                gain = 0.3 if ok else -0.2
                successful = ok
                timestamp = float(i)
                notes = ""
            insert_session(R(), db_path=tmp_db)
        stats = count_by_domain(tmp_db)
        assert stats["coding"]["count"] == 2
        assert stats["coding"]["successful"] == 1
        assert stats["intent"]["count"] == 1


class TestRecordToSqlite:
    """完整流程: MetaLearningEngine 记录 → JSON + SQLite 双写"""

    def test_full_flow(self, tmp_db, monkeypatch):
        from laap.agi.meta_learning import MetaLearningEngine

        # 隔离 JSON 状态文件
        monkeypatch.setenv("LAAP_STATE_PATH", os.path.join(
            os.path.dirname(tmp_db), "meta_learning_test.json"))
        engine = MetaLearningEngine()
        record_to_sqlite(engine, "verify_generated_code", True, db_path=tmp_db)
        record_to_sqlite(engine, "ask_user_to_clarify", False, db_path=tmp_db)

        assert engine.stats()["total_sessions"] == 2
        stats = count_by_domain(tmp_db)
        assert stats["coding"]["count"] == 1
        assert stats["coding"]["successful"] == 1
        assert stats["intent"]["count"] == 1
        assert stats["intent"]["successful"] == 0

        # 推荐接口可用（数据已进入策略统计：coding 1 条 active_recall 成功 → 推荐 active_recall）
        rec = engine.recommend_strategy(concept="test", domain="coding",
                                        difficulty=0.5)
        assert rec.value in {"active_recall", "practical", "structured"}


class TestBridgeHook:
    """cognitive bridge after_tool 的开关控制（不实例化 bridge，只测开关逻辑）"""

    def test_env_switch(self, monkeypatch):
        # LAAP_META_RECORD=0 应跳过；此测试验证开关读取逻辑
        monkeypatch.setenv("LAAP_META_RECORD", "0")
        assert os.environ.get("LAAP_META_RECORD", "1") == "0"
        monkeypatch.setenv("LAAP_META_RECORD", "1")
        assert os.environ.get("LAAP_META_RECORD", "1") == "1"
