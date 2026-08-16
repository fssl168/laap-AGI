"""TradingSelf（交易自我）测试：人格推导 / 判断审核 / 下达指令 / 反思。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading.trading_self import TradingSelf


@pytest.fixture()
def self_model():
    from laap.agi.self_model import EmergentSelfModel
    return EmergentSelfModel(agent_name="TestSelf")


@pytest.fixture()
def memory():
    from laap.agi.unified_memory import UnifiedMemory
    return UnifiedMemory()


# ════════════════════════════════════════════════════════════
# 人格 / 身份推导
# ════════════════════════════════════════════════════════════

def test_identity_from_personality_traits():
    """curiosity 高 → 风险偏好高；loyalty 高 → 保守。"""
    aggressive = TradingSelf(personality={"traits": {"curiosity": 0.95, "loyalty": 0.3}})
    conservative = TradingSelf(personality={"traits": {"curiosity": 0.2, "loyalty": 0.95}})
    assert aggressive.trading_identity()["risk_appetite"] > \
        conservative.trading_identity()["risk_appetite"]
    assert conservative.trading_identity()["discipline"] >= \
        aggressive.trading_identity()["discipline"]


def test_identity_bounds():
    ident = TradingSelf(personality={"traits": {}}).trading_identity()
    for k, v in ident.items():
        assert 0.0 <= v <= 1.0


def test_identity_statement_nonempty():
    st = TradingSelf(personality={}).identity_statement()
    assert "我是" in st and "风险偏好" in st


# ════════════════════════════════════════════════════════════
# 判断 / 审核（有意义和利益的决策）
# ════════════════════════════════════════════════════════════

def test_judge_approve_buy_no_ooos(self_model, memory):
    """实盘信号场景（无 OOS）：无顾虑 → approve，含 meaning + benefit。"""
    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    d = ts.judge("buy", symbol="600519", qty=100, price=100.0, cash=1_000_000.0)
    assert d["verdict"] == "approve"
    assert "meaning" in d and d["meaning"]
    assert "benefit_text" in d and d["benefit_text"]


def test_judge_rejects_buy_position_over_identity_limit(self_model, memory):
    """仓位超人格上限 → 不 approve（约束人格纪律）。"""
    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    # 激进测试：直接设低仓位上限
    ts.personality = {"traits": {"curiosity": 0.1, "loyalty": 0.9}}  # 保守
    d = ts.judge("buy", symbol="600519", qty=8000, price=100.0, cash=1_000_000.0)
    # 8000*100/1e6 = 80%，应超保守人格上限
    assert d["verdict"] in ("reject", "abstain")
    assert any("仓位" in r for r in d["reasons"])


def test_judge_buy_requires_positive_oos_when_provided(self_model, memory):
    """进化提案场景（给 OOS）：OOS 非正收益 → reject。"""
    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    d = ts.judge("buy", symbol="600519", oos={"cumulative_return": -0.02,
                                              "sharpe_ratio": -0.5})
    assert d["verdict"] == "reject"
    assert any("OOS" in r for r in d["reasons"])


def test_judge_memory_negative_lesson_cautions(self_model, memory):
    """决策 5 衰减：近期净负面教训 >= 2 → 买入审慎（abstain，仍保护）。"""
    from laap.paper_trading.models import OutcomeRecord
    from laap.paper_trading.memory_bridge import encode_lesson
    for tid in ("t1", "t2"):
        outcome = OutcomeRecord(trade_id=tid, pnl_pct=-0.08, hold_days=2,
                                vs_expected="missed", lesson="追高亏损",
                                lesson_type="short_term_chase")
        encode_lesson(memory, outcome, symbol="600519")

    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    d = ts.judge("buy", symbol="600519", qty=100, price=100.0, cash=1_000_000.0)
    assert any("负面教训" in r for r in d["reasons"])
    assert d["verdict"] == "abstain"


def test_judge_single_negative_lesson_does_not_veto(self_model, memory):
    """决策 5 修复：单笔亏损不再永久否决后续交易（net=1 < 2 → approve）。"""
    from laap.paper_trading.models import OutcomeRecord
    from laap.paper_trading.memory_bridge import encode_lesson
    outcome = OutcomeRecord(trade_id="t1", pnl_pct=-0.08, hold_days=2,
                            vs_expected="missed", lesson="追高亏损",
                            lesson_type="short_term_chase")
    encode_lesson(memory, outcome, symbol="600519")

    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    d = ts.judge("buy", symbol="600519", qty=100, price=100.0, cash=1_000_000.0)
    assert d["verdict"] == "approve"
    assert not any("负面教训" in r for r in d["reasons"])


def test_judge_positive_lesson_forgives(self_model, memory):
    """决策 5 宽恕：正面教训抵消负面（2 负 + 2 正 → net=0 → approve）。"""
    from laap.paper_trading.models import OutcomeRecord
    from laap.paper_trading.memory_bridge import encode_lesson
    for tid in ("n1", "n2"):
        encode_lesson(memory, OutcomeRecord(trade_id=tid, pnl_pct=-0.08, hold_days=2,
                                            vs_expected="missed", lesson="追高亏损",
                                            lesson_type="short_term_chase"),
                      symbol="600519")
    for tid in ("p1", "p2"):
        encode_lesson(memory, OutcomeRecord(trade_id=tid, pnl_pct=0.05, hold_days=3,
                                            vs_expected="met", lesson="顺势盈利",
                                            lesson_type="trend_ok"),
                      symbol="600519")

    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    d = ts.judge("buy", symbol="600519", qty=100, price=100.0, cash=1_000_000.0)
    assert d["verdict"] == "approve"


def test_position_scale_calibrated_to_strategy(self_model, memory):
    """决策：仓位上限锚定策略实际 position_scale，不否决策略自身配置。"""
    ts = TradingSelf(personality={}, preset="balanced", self_model=self_model,
                     memory=memory, strategy_position_scale=0.8)
    assert ts.trading_identity()["position_scale_max"] >= 0.8
    # 0.8 仓位是策略自身配置 → 不因 persona 被否
    d = ts.judge("buy", symbol="600519", qty=8000, price=100.0, cash=1_000_000.0)
    assert d["verdict"] == "approve"


def test_judge_sell_not_blocked_by_negative_lesson(self_model, memory):
    """卖出是风控动作，不受负面教训阻止。"""
    from laap.paper_trading.models import OutcomeRecord
    from laap.paper_trading.memory_bridge import encode_lesson
    outcome = OutcomeRecord(trade_id="t1", pnl_pct=-0.08, hold_days=2,
                            vs_expected="missed", lesson="追高亏损",
                            lesson_type="short_term_chase")
    encode_lesson(memory, outcome, symbol="600519")

    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    d = ts.judge("sell", symbol="600519")
    assert d["verdict"] == "approve"  # 卖不受负面教训阻止


def test_judge_self_efficacy_low_cautions(self_model, memory):
    """经验不足/自我效能低 → 观望。"""
    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    # 人为压低熟练度：记录大量失败
    for _ in range(10):
        ts.self_model.record_experience("trading", outcome_score=0.2, is_success=False)
    d = ts.judge("buy", symbol="600519")
    assert any(("经验不足" in r or "自我效能" in r) for r in d["reasons"])


def test_abstain_cooldown_releases(self_model, memory):
    """决策 5 冷却：连续弃权达上限 → 强制放行重新检验（不永久锁死）。"""
    from laap.paper_trading.models import OutcomeRecord
    from laap.paper_trading.memory_bridge import encode_lesson
    for tid in ("t1", "t2", "t3"):  # 净负面 3 >= 2 → 记忆门禁弃权
        encode_lesson(memory, OutcomeRecord(trade_id=tid, pnl_pct=-0.08, hold_days=2,
                                            vs_expected="missed", lesson="追高亏损",
                                            lesson_type="short_term_chase"),
                      symbol="600519")

    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    verdicts = [ts.judge("buy", symbol="600519", qty=100, price=100.0,
                         cash=1_000_000.0)["verdict"] for _ in range(6)]
    assert "abstain" in verdicts
    assert "approve" in verdicts  # 冷却触发放行
    assert ts._abstain_streak < 5  # 冷却后计数已重置


def test_sell_not_blocked_by_low_self_efficacy(self_model, memory):
    """决策 5：卖出是风控动作，低自我效能不得阻止平仓。"""
    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    for _ in range(10):
        ts.self_model.record_experience("trading", outcome_score=0.2, is_success=False)
    d = ts.judge("sell", symbol="600519")
    assert d["verdict"] == "approve"


# ════════════════════════════════════════════════════════════
# 下达指令 + 反思
# ════════════════════════════════════════════════════════════

@pytest.fixture()
def loop(tmp_path, memory):
    from laap.paper_trading.db import PaperDB
    from laap.paper_trading.market_source import StubMarketSource
    from laap.paper_trading.paper_service import PaperClosedLoop
    db = PaperDB(db_path=str(tmp_path / "pt.db"))
    return PaperClosedLoop(db, StubMarketSource(base_prices={"600519": 100.0}),
                           memory, initial_cash=1_000_000.0, enforce_t1=False)


def test_issue_buy_executes_with_self_rationale(loop, self_model, memory):
    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    decision = ts.judge("buy", symbol="600519", qty=100, price=100.0,
                        cash=loop.ledger.cash)
    assert decision["verdict"] == "approve"
    r = ts.issue(loop, "buy", "600519", 100, 100.0, decision,
                 allow_fallback=True)  # stub 测试行情显式允许
    assert r["trade_id"]
    # 决策留痕 rationale 应含 [self] 主体声明 + [benefit]
    conn = loop.db.conn()
    row = conn.execute("SELECT rationale FROM decisions WHERE decision_id=?",
                       (r["decision_id"],)).fetchone()
    conn.close()
    assert "[self]" in row["rationale"]
    assert "[benefit]" in row["rationale"]


def test_issue_sell_closes_and_reflects(loop, self_model, memory):
    ts = TradingSelf(personality={}, self_model=self_model, memory=memory)
    # 先买入
    buy_d = ts.judge("buy", symbol="600519", qty=100, price=100.0,
                     cash=loop.ledger.cash)
    ts.issue(loop, "buy", "600519", 100, 100.0, buy_d,
             allow_fallback=True)  # stub 测试行情显式允许
    assert len(loop.ledger.open_positions()) == 1

    sell_d = ts.judge("sell", symbol="600519")
    r = ts.issue(loop, "sell", "600519", 0, 105.0, sell_d)
    assert r["action"] == "sell"
    assert len(loop.ledger.open_positions()) == 0
    # 反思更新自我模型
    rep = ts.reflect_on_trade("600519", r.get("outcome"))
    assert rep["reflected"] is True


def test_run_daily_cycle_with_trading_self(tmp_path):
    """run_daily_cycle 挂 TradingSelf：审核通过才执行，产出 self_verdict。"""
    from laap.paper_trading.db import PaperDB
    from laap.paper_trading.market_source import StubMarketSource
    from laap.paper_trading.paper_service import PaperClosedLoop
    from laap.paper_trading.trading_self import TradingSelf
    from laap.paper_trading import strategy
    from laap.agi.unified_memory import UnifiedMemory
    from laap.agi.self_model import EmergentSelfModel

    db = PaperDB(db_path=str(tmp_path / "pt.db"))
    memory = UnifiedMemory()
    ts = TradingSelf(personality={}, self_model=EmergentSelfModel("T"),
                     memory=memory)
    loop = PaperClosedLoop(db, StubMarketSource(base_prices={"600519": 100.0}),
                           memory, initial_cash=1_000_000.0,
                           enforce_t1=False, trading_self=ts)

    # 上涨 OHLCV（触发 buy 信号）
    closes = [100.0 + i * 1.0 for i in range(20)] + \
             [120.0 - i * 1.5 for i in range(8)] + \
             [108.0 + i * 0.55 for i in range(15)]
    ohlcv = []
    for i, c in enumerate(closes):
        vol = 300_000.0 if i == len(closes) - 1 else 100_000.0
        ohlcv.append((c - 0.1, c, c + 0.2, c - 0.2, vol))

    result = loop.run_daily_cycle(["600519"], dict(strategy.STRATEGY_PARAMS),
                                  ohlcv_map={"600519": ohlcv})
    sig = result["signals"][0]
    # TradingSelf 审核通过 → buy 且带 self_verdict
    assert sig["action"] == "buy"
    assert sig.get("self_verdict") == "approve"
    assert "net_value" in result
