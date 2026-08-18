"""错误闭环单测 (error_monitor: 发现→分析→处理→总结)。"""
import os
import sqlite3
import sys
import time
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from laap.paper_trading.error_monitor import (
    analyze, decide_disposition, fmt_report, fmt_summary, persist, probe_db,
    probe_paths, scan_logs)


def _ts_line(offset_sec: int, level: str, msg: str) -> str:
    ts = datetime.fromtimestamp(time.time() - offset_sec).strftime(
        "%Y-%m-%d %H:%M:%S")
    return f"{ts},{int(offset_sec % 1000):03d} [{level}] {msg}\n"


@pytest.fixture
def err_log(tmp_path):
    p = tmp_path / "test-errors.log"
    lines = [
        _ts_line(30, "WARNING",
                 "LiveMarketSource failed for 600519, fallback to stub: no live price"),
        _ts_line(60, "WARNING",
                 "LiveMarketSource failed for 600519, fallback to stub: no live price"),
        _ts_line(90, "ERROR", "sqlite3.OperationalError: no such table: signals"),
        _ts_line(120, "ERROR", "FileNotFoundError: [Errno 2] No such file: x.db"),
        _ts_line(150, "WARNING",
                 "market source TxMarketSource failed: tx no quote for sh600144"),
        _ts_line(180, "INFO", "MarketEventSource started (normal info line)"),
        _ts_line(10000, "ERROR", "太旧的行，应被窗口过滤"),
    ]
    p.write_text("".join(lines), encoding="utf-8")
    return p


class TestScanAndAnalyze:
    def test_scan_window_filter(self, err_log):
        errs = scan_logs(log_paths=[err_log], window_seconds=600)
        # 5 条错误行；INFO 与超窗行被过滤
        assert len(errs) == 5
        assert all(e.ts >= time.time() - 600 for e in errs)

    def test_classify(self, err_log):
        errs = scan_logs(log_paths=[err_log], window_seconds=600)
        cats = {e.category for e in errs}
        assert "datasource" in cats   # LiveMarketSource failed
        assert "database" in cats     # sqlite3.OperationalError
        assert "file" in cats         # FileNotFoundError

    def test_analyze_aggregation_and_root_cause(self, err_log):
        errs = scan_logs(log_paths=[err_log], window_seconds=600)
        groups = analyze(errs)
        by_rc = {g["root_cause"]: g for g in groups}
        assert by_rc["partial_source_failure"]["count"] == 2  # 同类聚合
        assert by_rc["db_read_error"]["priority"] == 2        # DB 高优先级
        assert by_rc["file_missing"]["priority"] == 2
        # 排序: 高优先级在前
        assert groups[0]["priority"] >= groups[-1]["priority"]


class TestDisposition:
    def test_auto_vs_manual(self, err_log):
        errs = scan_logs(log_paths=[err_log], window_seconds=600)
        groups = analyze(errs)
        disp = decide_disposition(groups)
        assert "partial_source_failure" in disp["auto_handled"]  # 数据源自动
        assert disp["manual_count"] >= 2                         # DB/文件人工
        assert any(m["category"] == "database" for m in disp["needs_manual"])


class TestPersist:
    def test_persist_writes_table(self, tmp_path, err_log):
        db = tmp_path / "t.db"
        errs = scan_logs(log_paths=[err_log], window_seconds=600)
        groups = analyze(errs)
        disp = decide_disposition(groups)
        n = persist(groups, disp, db_path=db, pushed=True, note="test")
        assert n == len(groups)
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT category, auto_handled, pushed FROM error_events").fetchall()
        assert len(rows) == n
        assert any(r[2] == 1 for r in rows)          # pushed 标记
        assert any(r[1] == 1 for r in rows)          # 自动处置标记
        conn.close()

    def test_persist_idempotent_schema(self, tmp_path):
        db = tmp_path / "t2.db"
        persist([], {"needs_manual": [], "auto_handled": []}, db_path=db)
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO error_events (ts, category, root_cause, count)"
                     " VALUES (1,'x','unknown',1)")
        conn.commit()
        conn.close()  # 幂等建表后可用


class TestProbes:
    def test_probe_db_readonly(self, tmp_path):
        ok, msg = probe_db(db_path=tmp_path / "nope.db")
        assert ok is False

    def test_probe_paths(self, tmp_path):
        p = tmp_path / "exists.txt"
        p.write_text("x", encoding="utf-8")
        out = probe_paths([p, tmp_path / "missing.txt"])
        by_path = {o["path"]: o for o in out}
        assert by_path[str(p)]["ok"] is True
        assert by_path[str(tmp_path / "missing.txt")]["ok"] is False


class TestReports:
    def test_fmt_report_empty(self):
        assert "无异常" in fmt_report([], 600)

    def test_fmt_summary(self, err_log):
        errs = scan_logs(log_paths=[err_log], window_seconds=600)
        groups = analyze(errs)
        disp = decide_disposition(groups)
        s = fmt_summary(groups, disp, 600, pushed=True)
        assert "发现:" in s and "自动处置" in s and "需人工" in s
        assert "已推送" in s
