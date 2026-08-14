"""P3 端到端 E2E 测试：记忆闭环 + 自进化闭环全链路。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.db import PaperDB
from laap.paper_trading.paper_service import PaperClosedLoop, build_paper_closed_loop
from laap.paper_trading.market_source import StubMarketSource
from laap.paper_trading.models import DecisionAction
from laap.paper_trading.memory_bridge import retrieve_for_symbol


# ════════════════════════════════════════════════════════════
# 记忆闭环 E2E：决策→下单→成交→平仓→教训沉淀→下次命中
# ════════════════════════════════════════════════════════════

def test_memory_closed_loop_e2e(tmp_path):
    from laap.agi.unified_memory import UnifiedMemory

    db = PaperDB(db_path=str(tmp_path / "pt.db"))
    market = StubMarketSource(base_prices={"600519": 100.0})
    memory = UnifiedMemory()
    loop = PaperClosedLoop(db, market, memory, initial_cash=1_000_000.0)

    # 第 1 天：决策买入 → 下单（注入记忆）→ 成交
    r1 = loop.decide_and_trade(
        "600519", DecisionAction.BUY, 100, 100.0,
        rationale="放量突破 20 日线", expected="+5%", risk_note="仓位≤5%")
    trade_id = r1["trade_id"]
    assert trade_id
    # 决策留痕落库
    conn = db.conn()
    n_dec = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    conn.close()
    assert n_dec == 1

    # 第 2 天：平仓（亏损 → short_term_chase 教训）
    r2 = loop.close_and_learn(trade_id, "600519", exit_price=95.0, expected="+5%")
    assert r2["outcome"]["lesson_type"] == "short_term_chase"
    assert r2["episode_id"]  # 教训已沉淀进 UnifiedMemory

    # 第 3 天：再次决策 → 检索应命中历史教训（A-2 参与推理）
    hits = retrieve_for_symbol(memory, "600519")
    assert any("short_term_chase" in str(h.get("content", "")) for h in hits)

    # 教训已落 outcomes 表
    conn = db.conn()
    n_out = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    conn.close()
    assert n_out == 1


def test_build_paper_closed_loop(tmp_path):
    """统一装配（build_paper_closed_loop）可独立构造。"""
    from laap.agi.unified_memory import UnifiedMemory
    loop = build_paper_closed_loop(
        repo_root=str(tmp_path),
        market=StubMarketSource(),
        memory=UnifiedMemory(),
    )
    assert loop.ledger.stats()["cash"] > 0
    assert loop.market is not None
    assert loop.memory is not None


# ════════════════════════════════════════════════════════════
# 自进化闭环 E2E：attach → evolve → approve → 审计
# ════════════════════════════════════════════════════════════

class _AnalyzerStub:
    analyzed_files: set = set()
    targets_found: list = []


@pytest.fixture()
def engine(monkeypatch, tmp_path):
    """轻量 CodeEvolutionEngine（同 test_true_rsi 模式）。"""
    from laap.agi.code_evolution import CodeEvolutionEngine as CEE
    from laap.agi.code_evolution import GitIntegrator, PatchGenerator, SandboxTester
    from laap.agi.evolution_audit import EvolutionAuditLog

    monkeypatch.setattr(CEE, "__init__", lambda self, repo_root="", llm_fn=None: None)
    e = object.__new__(CEE)
    e.repo_root = str(tmp_path)
    e.mutations = []
    e.deployed_count = 0
    e.rollback_count = 0
    e.audit = EvolutionAuditLog(repo_root=str(tmp_path))
    e.patcher = PatchGenerator(llm_generate_fn=None)
    e.tester = SandboxTester(restrict_resources=False)
    e.git = GitIntegrator(str(tmp_path))
    e.analyzer = _AnalyzerStub()
    e.created_at = 0.0
    e.qa = None
    e._lock = threading.Lock()
    e.scope_guard = None
    e.deploy_gate = None
    return e


def test_quant_evolution_closed_loop_e2e(engine, monkeypatch):
    from laap.paper_trading.backtest_runner import BacktestRunner
    from laap.paper_trading.quant_evolution import QuantEvolutionEngine

    prices = [100.0 + i * 0.5 for i in range(120)]
    qe = QuantEvolutionEngine(engine, BacktestRunner(), prices).attach()

    # 双守卫就位
    assert engine.scope_guard is not None
    assert engine.deploy_gate is not None

    # evolve（隔离真实扫描，验证编排不抛错）
    monkeypatch.setattr(engine, "scan_targets", lambda directory="": [])
    results = qe.evolve(max_mutations=1)
    assert isinstance(results, list)

    # stats 反映代码级受限递归模式
    st = qe.stats()
    assert st["mode"] == "quant-code-evolution"


def test_deploy_gate_blocked_audited(engine):
    """deploy_gate 拒绝时落审计（fail-closed）。"""
    from laap.agi.code_evolution import CodeTarget
    from laap.paper_trading.quant_evolution import QuantEvolutionEngine
    from laap.paper_trading.backtest_runner import BacktestRunner

    # 极短 price_series → OOS 门禁必拒
    qe = QuantEvolutionEngine(engine, BacktestRunner(), [100.0, 101.0]).attach()
    target = CodeTarget(file_path="laap/paper_trading/strategy.py",
                        function_name="f", current_code="def f():\n    return 1\n")
    # 直接调 deploy_gate 验证 fail-closed
    from laap.agi.code_evolution import CodeMutation
    m = CodeMutation(id="m1", target=target,
                     mutated_code="def f():\n    return 2\n")
    ok, reason = engine.deploy_gate(m, engine)
    assert ok is False
    assert "too short" in reason
