"""LAAP Paper Trading — 数据模型（最小真实交易循环）。

参考 DSA paper_trading 的数据模型精简而来，承载"信号→订单→成交→净值"
最小交易循环，以及记忆闭环（DecisionRecord/OutcomeRecord）的载体。

所有模型可序列化（to_dict/from_dict），供 SQLite 持久化（db.py）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELED = "canceled"


class DecisionAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class PaperSignal:
    """交易信号（参考 DSA DecisionSignal → PaperSignal）。"""
    symbol: str
    action: DecisionAction
    quantity: int = 0
    trigger_price: float = 0.0
    ts: float = field(default_factory=time.time)
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PaperSignal":
        return cls(
            symbol=d["symbol"],
            action=DecisionAction(d["action"]),
            quantity=d.get("quantity", 0),
            trigger_price=d.get("trigger_price", 0.0),
            ts=d.get("ts", time.time()),
            rationale=d.get("rationale", ""),
        )


@dataclass
class PaperOrder:
    """订单（client_request_id 幂等，参考 DSA T-13）。"""
    id: str
    signal_id: str = ""
    status: OrderStatus = OrderStatus.PENDING
    fill_price: float = 0.0
    filled_ts: Optional[float] = None
    client_request_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PaperOrder":
        return cls(
            id=d["id"],
            signal_id=d.get("signal_id", ""),
            status=OrderStatus(d["status"]),
            fill_price=d.get("fill_price", 0.0),
            filled_ts=d.get("filled_ts"),
            client_request_id=d.get("client_request_id"),
        )


@dataclass
class PaperTrade:
    """成交/持仓（平仓后含 pnl）。"""
    id: str
    order_id: str
    symbol: str
    side: DecisionAction
    quantity: int = 0
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    hold_days: Optional[int] = None
    entry_ts: float = field(default_factory=time.time)
    exit_ts: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PaperTrade":
        return cls(
            id=d["id"],
            order_id=d.get("order_id", ""),
            symbol=d.get("symbol", ""),
            side=DecisionAction(d.get("side", "buy")),
            quantity=d.get("quantity", 0),
            entry_price=d.get("entry_price", 0.0),
            exit_price=d.get("exit_price"),
            pnl=d.get("pnl"),
            pnl_pct=d.get("pnl_pct"),
            hold_days=d.get("hold_days"),
            entry_ts=d.get("entry_ts", time.time()),
            exit_ts=d.get("exit_ts"),
        )


@dataclass
class DecisionRecord:
    """决策留痕（闭环 A 载体）——决策理由 + 依据记忆 + 风险提示。"""
    trade_id: str
    symbol: str
    action: DecisionAction
    ts: float = field(default_factory=time.time)
    rationale: str = ""
    basis_memories: List[str] = field(default_factory=list)
    risk_note: str = ""
    expected: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionRecord":
        return cls(
            trade_id=d["trade_id"],
            symbol=d["symbol"],
            action=DecisionAction(d.get("action", "buy")),
            ts=d.get("ts", time.time()),
            rationale=d.get("rationale", ""),
            basis_memories=list(d.get("basis_memories", [])),
            risk_note=d.get("risk_note", ""),
            expected=d.get("expected", ""),
        )


@dataclass
class OutcomeRecord:
    """结果回填（闭环 A 载体）——结果 + 教训，verified 防单笔噪声污染。"""
    trade_id: str
    pnl_pct: float = 0.0
    hold_days: int = 0
    vs_expected: str = ""          # hit / missed / neutral
    lesson: str = ""
    lesson_type: str = ""           # 检索键，如 short_term_chase / no_stop_loss
    verified: bool = False          # 累计 >= min_confirm 次真实平仓才 True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OutcomeRecord":
        return cls(
            trade_id=d["trade_id"],
            pnl_pct=d.get("pnl_pct", 0.0),
            hold_days=d.get("hold_days", 0),
            vs_expected=d.get("vs_expected", ""),
            lesson=d.get("lesson", ""),
            lesson_type=d.get("lesson_type", ""),
            verified=bool(d.get("verified", False)),
        )


@dataclass
class PaperNetValue:
    """净值快照（供交易适应度 / 回测）。"""
    ts: float = field(default_factory=time.time)
    cash: float = 0.0
    equity: float = 0.0
    total: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PaperNetValue":
        return cls(
            ts=d.get("ts", time.time()),
            cash=d.get("cash", 0.0),
            equity=d.get("equity", 0.0),
            total=d.get("total", 0.0),
        )
