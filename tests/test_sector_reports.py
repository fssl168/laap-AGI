# -*- coding: utf-8 -*-
"""_sector_reports（板块研报聚合）诚实提示测试。

背景：查询「机器人」板块研报曾返回误导性提示「数据源降级/不可用」——
由两类原因叠加：① 带后缀代码（600511.SH）未规范化导致拉取失败；
② 「该股无研报」与「数据源降级」未区分。本测试守护修复后的提示语义。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


def _mk_report(symbol, title, rating="买入", org="某券商", date="2026-08-15"):
    from laap.paper_trading.news_intel import ResearchReport
    return ResearchReport(symbol, title=title, rating=rating, org=org, date=date)


@pytest.fixture
def tools(monkeypatch, tmp_path):
    import aris_brain.paper_trading_tools as tools
    monkeypatch.setenv("STOCK_LIST", "600519,600114,000410")
    # 测试隔离：清缓存、屏蔽 LLM 链（确定性兜底）、DB/REPORT 目录重定向到 tmp
    tools._SECTOR_REPORT_CACHE.clear()
    monkeypatch.setattr(tools, "_build_sector_llm_call", lambda: None)
    monkeypatch.setattr(tools, "DB_PATH", str(tmp_path / "pt.db"))
    monkeypatch.setattr(tools, "REPORT_DIR", str(tmp_path / "report"))
    # 2026-08-18 修复: _persist_sector_report 走 _db()(PaperDB→生产PG/SQLite),
    # 测试直接查 tmp DB_PATH 会 no such table。patch _db 也指向 tmp 库。
    def _tmp_db():
        import sqlite3
        conn = sqlite3.connect(tools.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    monkeypatch.setattr(tools, "_db", _tmp_db)
    return tools


def _patch_fetch(monkeypatch, fn):
    from laap.paper_trading import news_intel
    monkeypatch.setattr(news_intel, "fetch_research_reports", fn)
    monkeypatch.setattr(news_intel, "fetch_stock_profile",
                        lambda *a, **k: (None, {"source": "stub"}))


def test_sector_reports_found(tools, monkeypatch):
    """关键词命中研报标题 → 返回聚合结果，无降级提示。"""

    def _fetch(code, max_results=10):
        if code == "600114":
            return [_mk_report(code, "粉末冶金龙头，主业延伸至机器人+AI后续可期")], \
                {"source": "eastmoney", "used_fallback": False}
        return [_mk_report(code, f"{code} 常规研报")], \
            {"source": "eastmoney", "used_fallback": False}

    _patch_fetch(monkeypatch, _fetch)
    out = tools._sector_reports("机器人")
    assert "「机器人」板块研报" in out
    assert "机器人+AI后续可期" in out
    assert "降级" not in out


def test_sector_reports_no_match_clean(tools, monkeypatch):
    """无命中且数据源全部正常 → 不带任何降级/无数据后缀。"""

    def _fetch(code, max_results=10):
        return [_mk_report(code, f"{code} 常规研报")], \
            {"source": "eastmoney", "used_fallback": False}

    _patch_fetch(monkeypatch, _fetch)
    out = tools._sector_reports("白酒")
    assert "未找到与「白酒」相关的研报" in out
    assert "降级" not in out
    assert "暂无研报" not in out


def test_sector_reports_no_match_some_no_data(tools, monkeypatch):
    """部分标的源可用但无研报（no_data=True）→ 提示「暂无研报」，不误报降级。"""

    def _fetch(code, max_results=10):
        if code == "600114":
            return [], {"source": "none", "used_fallback": True, "no_data": True}
        return [_mk_report(code, f"{code} 常规研报")], \
            {"source": "eastmoney", "used_fallback": False}

    _patch_fetch(monkeypatch, _fetch)
    out = tools._sector_reports("白酒")
    assert "未找到与「白酒」相关的研报" in out
    assert "暂无研报" in out
    assert "降级" not in out


def test_sector_reports_no_match_degraded(tools, monkeypatch):
    """数据源网络/服务降级（no_data=False）→ 提示「降级/不可用」。"""

    def _fetch(code, max_results=10):
        return [], {"source": "none", "used_fallback": True, "no_data": False,
                    "fallback_reason": "ConnectionError down"}

    _patch_fetch(monkeypatch, _fetch)
    out = tools._sector_reports("白酒")
    assert "未找到与「白酒」相关的研报" in out
    assert "降级/不可用" in out


def test_sector_reports_found_with_degraded_footer(tools, monkeypatch):
    """有命中但部分标的降级 → 展示结果 + 降级脚注。"""

    def _fetch(code, max_results=10):
        if code == "600519":
            return [], {"source": "none", "used_fallback": True, "no_data": False}
        return [_mk_report(code, "机器人+AI 可期")], \
            {"source": "eastmoney", "used_fallback": False}

    _patch_fetch(monkeypatch, _fetch)
    out = tools._sector_reports("机器人")
    assert "「机器人」板块研报" in out
    assert "⚠️ 部分数据源降级/不可用" in out


def test_sector_kw_tokens():
    from aris_brain.paper_trading_tools import _sector_kw_tokens
    # 复合板块名 → 整词 + 2 字滑动窗口
    assert _sector_kw_tokens("新能源材料") == \
        ["新能源材料", "新能", "能源", "源材", "材料"]
    # 短词（<4 字）保持整词精确匹配，不展开
    assert _sector_kw_tokens("白酒") == ["白酒"]
    assert _sector_kw_tokens("机器人") == ["机器人"]


def test_sector_reports_compound_keyword_window_match(tools, monkeypatch):
    """复合板块名整词未命中 → 按子词 OR 匹配（新能源材料 → 新能源/材料），并注明命中词。"""

    def _fetch(code, max_results=10):
        if code == "600519":
            return [_mk_report(code, "2025年半年报点评：新能源出口持续突破")], \
                {"source": "eastmoney", "used_fallback": False}
        if code == "600114":
            return [_mk_report(code, "积极推进锆铪分离项目建设，新材料赛道加速布局")], \
                {"source": "eastmoney", "used_fallback": False}
        return [_mk_report(code, f"{code} 常规研报")], \
            {"source": "eastmoney", "used_fallback": False}

    _patch_fetch(monkeypatch, _fetch)
    out = tools._sector_reports("新能源材料")
    assert "「新能源材料」板块研报" in out
    assert "新能源出口持续突破" in out      # 命中子词「新能源」
    assert "新材料赛道加速布局" in out      # 命中子词「材料」
    assert "关键词拆解命中" in out
    assert "常规研报" not in out            # 无相关子词的研报不混入


def test_sector_reports_short_keyword_exact_only(tools, monkeypatch):
    """短板块名（白酒）保持整词精确匹配：只含子词的研报不误入。"""

    def _fetch(code, max_results=10):
        return [_mk_report(code, "直营化改革成效凸显")], \
            {"source": "eastmoney", "used_fallback": False}

    _patch_fetch(monkeypatch, _fetch)
    out = tools._sector_reports("白酒")
    assert "未找到与「白酒」相关的研报" in out


# ════════════════════════════════════════════════════════════
# 四段式结构化研报输出（一、定位与驱动；二、关键环节；三、框架；四、风险）
# ════════════════════════════════════════════════════════════

def test_sector_report_four_sections(tools, monkeypatch):
    """命中时输出四段式研报：含四个章节头 + 证据 + 免责脚注。"""

    def _fetch(code, max_results=10):
        if code == "600114":
            return [_mk_report(code, "粉末冶金龙头，主业延伸至机器人+AI后续可期",
                               rating="买入", org="华金证券")], \
                {"source": "eastmoney", "used_fallback": False}
        return [_mk_report(code, f"{code} 常规研报")], \
            {"source": "eastmoney", "used_fallback": False}

    _patch_fetch(monkeypatch, _fetch)
    out = tools._sector_reports("机器人")
    assert "## 一、板块定位与核心驱动" in out
    assert "## 二、关键细分方向梳理" in out
    assert "## 三、当前阶段的选股/选赛道框架" in out
    assert "## 四、风险提示" in out
    assert "机器人+AI后续可期" in out            # 证据表含研报标题
    assert "华金证券" in out                      # 证据表含机构
    assert "不构成投资建议" in out                # 免责脚注
    # 确定性兜底（LLM 未就绪）：定位段明确说明
    assert "产业逻辑自动梳理暂不可用" in out


def test_sector_report_llm_synthesis(tools, monkeypatch):
    """LLM 可用时，定位段使用 LLM 合成文本。"""

    def _fetch(code, max_results=10):
        return [_mk_report(code, "机器人核心部件 放量")], \
            {"source": "eastmoney", "used_fallback": False}

    _patch_fetch(monkeypatch, _fetch)
    monkeypatch.setattr(tools, "_build_sector_llm_call",
                        lambda: lambda prompt, system="", max_tokens=800:
                        "机器人板块处于精密制造与人工智能的交叉中游，"
                        "核心是关节执行器的降本曲线与国产替代进程，"
                        "自选股池内粉末冶金标的兼具确定性业绩与机器人期权。")
    out = tools._sector_reports("机器人")
    assert "精密制造与人工智能的交叉中游" in out   # LLM 合成文本进入定位段
    assert "机器人核心部件 放量" in out


def test_sector_report_cache(tools, monkeypatch):
    """同 sector 短窗口内命中缓存，不重复拉取/合成。"""
    calls = []

    def _fetch(code, max_results=10):
        calls.append(code)
        return [_mk_report(code, "机器人 主题")], \
            {"source": "eastmoney", "used_fallback": False}

    _patch_fetch(monkeypatch, _fetch)
    tools._SECTOR_REPORT_CACHE.clear()
    out1 = tools._sector_reports("机器人")
    out2 = tools._sector_reports("机器人")
    assert out1 == out2
    assert len(calls) == 3                        # 仅首次拉取（600519/600114/000410）
    tools._SECTOR_REPORT_CACHE.clear()


def test_sector_report_char_limit(tools, monkeypatch):
    """研报总字符 ≤2000（大量命中时压缩证据/硬截断兜底）。"""

    def _fetch(code, max_results=10):
        return [_mk_report(code, f"{code} 第{i+1}条研报标题：机器人核心部件与材料环节深度梳理")
                for i in range(5)], {"source": "eastmoney", "used_fallback": False}

    _patch_fetch(monkeypatch, _fetch)
    out = tools._sector_reports("机器人")
    assert len(out) <= 2000
    assert "## 一、板块定位与核心驱动" in out
    assert "## 二、关键细分方向梳理" in out
    assert "## 四、风险提示" in out


# ════════════════════════════════════════════════════════════
# 研报落库（sha1 哈希）+ md 源文件保存 report/（YYYYMMDD_板块.md）
# ════════════════════════════════════════════════════════════

def test_sector_report_filename(tools):
    from aris_brain.paper_trading_tools import _sector_report_filename
    from datetime import datetime as _dt
    today = _dt.now().strftime('%Y%m%d')
    # 带内容哈希 → 同日多版本格式 YYYYMMDD_板块_<hash8>.md
    assert _sector_report_filename("机器人", "6be8cf74abcd1234") == \
        f"{today}_机器人_6be8cf74.md"
    # 非法字符清洗 + 哈希后缀
    assert _sector_report_filename("新能源 材料/锂电", "abcdef12") == \
        f"{today}_新能源_材料_锂电_abcdef12.md"
    # 无哈希 → 不带后缀（向后兼容）
    assert _sector_report_filename("机器人") == f"{today}_机器人.md"


def test_sector_report_persist(tools, monkeypatch):
    """研报落库（sector_reports 表）+ md 源文件保存 report/（YYYYMMDD_板块_<hash8>.md）。"""
    import re as _re
    import sqlite3

    def _fetch(code, max_results=10):
        return [_mk_report(code, "粉末冶金龙头，主业延伸至机器人+AI后续可期",
                           rating="买入", org="华金证券")], \
            {"source": "eastmoney", "used_fallback": False}

    _patch_fetch(monkeypatch, _fetch)
    tools._SECTOR_REPORT_CACHE.clear()
    out = tools._sector_reports("机器人")

    # md 源文件存在，文件名 = YYYYMMDD_机器人_<hash8>.md，且 hash8 与内容一致
    files = [f for f in os.listdir(tools.REPORT_DIR) if f.endswith(".md")]
    assert len(files) == 1
    fname = files[0]
    assert _re.fullmatch(r"\d{8}_机器人_[0-9a-f]{8}\.md", fname), fname
    fpath = os.path.join(tools.REPORT_DIR, fname)
    with open(fpath, encoding="utf-8") as f:
        body = f.read().strip()
    assert "## 一、板块定位与核心驱动" in body
    assert "已保存" not in body
    import hashlib
    assert hashlib.sha1(body.encode("utf-8")).hexdigest()[:8] == \
        fname.split("_")[-1].split(".")[0]

    # 落库：sector_reports 表一行，content 哈希主键
    conn = sqlite3.connect(tools.DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM sector_reports").fetchall()
    conn.close()
    assert len(rows) == 1
    row = rows[0]
    assert row["sector"] == "机器人"
    assert row["content"] == body
    assert row["char_count"] == len(body)
    assert row["report_hash"] == hashlib.sha1(body.encode("utf-8")).hexdigest()

    # 返回文本含保存提示（含文件名）
    assert "已保存" in out
    assert fname in out
