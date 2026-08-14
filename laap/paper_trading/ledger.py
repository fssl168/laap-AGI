"""LAAP Paper Trading — 最小 OMS（PaperLedger）。

参考 DSA TradingEngine 精简而来：下单（幂等）/成交/撤单/平仓/净值快照。
SQLite 持久化（db.py），现金账户维护在内存 + 净值快照落库。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from laap.paper_trading.db import PaperDB
from laap.paper_trading.models import (
    DecisionAction,
    OrderStatus,
    PaperNetValue,
    PaperOrder,
    PaperSignal,
    PaperTrade,
)

logger = logging.getLogger("laap.paper_trading.ledger")


class PaperLedger:
    """最小订单管理系统（OMS）。"""

    def __init__(self, db: PaperDB, initial_cash: float = 1_000_000.0):
        self.db = db
        self.initial_cash = initial_cash
        # 现金从最新净值恢复，否则用初始资金
        self.cash = self._restore_cash() if self._latest_net_value() else initial_cash

    # ════════════════════════════════════════════════════════
    # 下单 / 成交 / 撤单
    # ════════════════════════════════════════════════════════

    def submit_signal(self, signal: PaperSignal,
                      client_request_id: Optional[str] = None,
                      memory: Any = None) -> PaperOrder:
        """下单（client_request_id 幂等，参考 DSA T-13）。

        Args:
            signal: 交易信号
            client_request_id: 幂等键；同一 id 重复提交返回同一订单。
            memory: UnifiedMemory（可选）。闭环 A-2：下单前注入记忆 prompt，
                使 rationale 携带历史经验依据。
        """
        # 幂等：client_request_id 已存在 → 返回已有订单
        if client_request_id:
            existing = self._order_by_client_request_id(client_request_id)
            if existing is not None:
                return existing

        # A-2 参与推理：注入记忆（延迟导入，避免顶层循环依赖）
        if memory is not None:
            try:
                from laap.paper_trading.memory_bridge import inject_memory_prompt
                prompt = inject_memory_prompt(memory, signal.symbol, signal.action.value)
                signal.rationale = (signal.rationale + "\n[memory]\n" + prompt).strip()
            except Exception as e:
                logger.warning(f"memory injection skipped: {e}")

        signal_id = signal.symbol + ":" + str(int(signal.ts * 1000))
        order_id = "ord_" + uuid.uuid4().hex[:12]

        conn = self.db.conn()
        try:
            conn.execute(
                "INSERT INTO signals (id, symbol, action, quantity, trigger_price, ts, rationale) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (signal_id, signal.symbol, signal.action.value, signal.quantity,
                 signal.trigger_price, signal.ts, signal.rationale),
            )
            conn.execute(
                "INSERT INTO orders (id, signal_id, status, client_request_id) "
                "VALUES (?, ?, ?, ?)",
                (order_id, signal_id, OrderStatus.PENDING.value, client_request_id),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(f"submit_signal: {signal.symbol} {signal.action.value} "
                    f"qty={signal.quantity} -> {order_id}")
        return PaperOrder(
            id=order_id, signal_id=signal_id, status=OrderStatus.PENDING,
            client_request_id=client_request_id,
        )

    def fill_order(self, order_id: str, fill_price: float) -> PaperTrade:
        """成交：订单 pending→filled，生成持仓 trade。"""
        conn = self.db.conn()
        try:
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if row is None:
                raise KeyError(f"order not found: {order_id}")
            if row["status"] != OrderStatus.PENDING.value:
                raise ValueError(f"order not pending: {order_id} status={row['status']}")

            sig = conn.execute(
                "SELECT * FROM signals WHERE id=?", (row["signal_id"],)).fetchone()
            if sig is None:
                raise KeyError(f"signal not found: {row['signal_id']}")

            now = time.time()
            conn.execute(
                "UPDATE orders SET status=?, fill_price=?, filled_ts=? WHERE id=?",
                (OrderStatus.FILLED.value, fill_price, now, order_id),
            )
            trade_id = "trd_" + uuid.uuid4().hex[:12]
            conn.execute(
                "INSERT INTO trades (id, order_id, symbol, side, quantity, entry_price, entry_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (trade_id, order_id, sig["symbol"], sig["action"],
                 sig["quantity"], fill_price, now),
            )
            # 现金结算：buy 减现金
            if sig["action"] == DecisionAction.BUY.value:
                self.cash -= fill_price * sig["quantity"]
            conn.commit()
        finally:
            conn.close()

        return PaperTrade(
            id=trade_id, order_id=order_id, symbol=sig["symbol"],
            side=DecisionAction(sig["action"]), quantity=sig["quantity"],
            entry_price=fill_price, entry_ts=now,
        )

    def cancel_order(self, order_id: str) -> PaperOrder:
        """撤单：仅 pending 可撤。"""
        conn = self.db.conn()
        try:
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if row is None:
                raise KeyError(f"order not found: {order_id}")
            if row["status"] != OrderStatus.PENDING.value:
                raise ValueError(f"order not pending: {order_id}")
            conn.execute(
                "UPDATE orders SET status=? WHERE id=?",
                (OrderStatus.CANCELED.value, order_id),
            )
            conn.commit()
        finally:
            conn.close()
        return PaperOrder(id=order_id, status=OrderStatus.CANCELED)

    def close_trade(self, trade_id: str, exit_price: float) -> PaperTrade:
        """平仓：算 pnl / pnl_pct / hold_days，卖出现金回笼。"""
        conn = self.db.conn()
        try:
            row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
            if row is None:
                raise KeyError(f"trade not found: {trade_id}")
            if row["exit_price"] is not None:
                raise ValueError(f"trade already closed: {trade_id}")

            now = time.time()
            entry = row["entry_price"]
            qty = row["quantity"]
            pnl = (exit_price - entry) * qty
            pnl_pct = (exit_price - entry) / entry if entry else 0.0
            hold_days = int((now - row["entry_ts"]) / 86400)

            conn.execute(
                "UPDATE trades SET exit_price=?, pnl=?, pnl_pct=?, hold_days=?, exit_ts=? "
                "WHERE id=?",
                (exit_price, pnl, pnl_pct, hold_days, now, trade_id),
            )
            # 现金结算：sell 回笼现金
            if row["side"] == DecisionAction.BUY.value:
                self.cash += exit_price * qty
            conn.commit()
        finally:
            conn.close()

        return PaperTrade(
            id=trade_id, order_id=row["order_id"], symbol=row["symbol"],
            side=DecisionAction(row["side"]), quantity=row["quantity"],
            entry_price=entry, exit_price=exit_price, pnl=pnl,
            pnl_pct=pnl_pct, hold_days=hold_days, exit_ts=now,
        )

    # ════════════════════════════════════════════════════════
    # 持仓 / 净值
    # ════════════════════════════════════════════════════════

    def open_positions(self) -> List[PaperTrade]:
        """未平仓持仓（exit_price IS NULL）。"""
        conn = self.db.conn()
        try:
            rows = conn.execute(
                "SELECT * FROM trades WHERE exit_price IS NULL").fetchall()
        finally:
            conn.close()
        return [PaperTrade.from_dict(dict(r)) for r in rows]

    def snapshot_net_value(self, market) -> PaperNetValue:
        """持仓 MTM 估值 + 现金 → 净值快照落库。

        Args:
            market: MarketSource，提供 get_price(symbol) 实时/回放价。
        """
        equity = 0.0
        for pos in self.open_positions():
            price, _meta = market.get_price(pos.symbol)
            equity += pos.quantity * price
        total = self.cash + equity
        nv = PaperNetValue(ts=time.time(), cash=self.cash, equity=equity, total=total)
        conn = self.db.conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO net_values (ts, cash, equity, total) VALUES (?, ?, ?, ?)",
                (nv.ts, nv.cash, nv.equity, nv.total),
            )
            conn.commit()
        finally:
            conn.close()
        return nv

    def net_values(self) -> List[PaperNetValue]:
        """净值序列（升序）。"""
        conn = self.db.conn()
        try:
            rows = conn.execute("SELECT * FROM net_values ORDER BY ts").fetchall()
        finally:
            conn.close()
        return [PaperNetValue.from_dict(dict(r)) for r in rows]

    def stats(self) -> Dict[str, Any]:
        """账本统计。"""
        conn = self.db.conn()
        try:
            n_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            n_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            n_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            n_open = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE exit_price IS NULL").fetchone()[0]
        finally:
            conn.close()
        return {
            "cash": round(self.cash, 2),
            "signals": n_signals,
            "orders": n_orders,
            "trades": n_trades,
            "open_positions": n_open,
        }

    # ════════════════════════════════════════════════════════
    # 内部
    # ════════════════════════════════════════════════════════

    def _order_by_client_request_id(self, client_request_id: str) -> Optional[PaperOrder]:
        conn = self.db.conn()
        try:
            row = conn.execute(
                "SELECT * FROM orders WHERE client_request_id=?",
                (client_request_id,)).fetchone()
        finally:
            conn.close()
        return PaperOrder.from_dict(dict(row)) if row else None

    def _latest_net_value(self) -> Optional[PaperNetValue]:
        conn = self.db.conn()
        try:
            row = conn.execute(
                "SELECT * FROM net_values ORDER BY ts DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        return PaperNetValue.from_dict(dict(row)) if row else None

    def _restore_cash(self) -> float:
        nv = self._latest_net_value()
        return nv.cash if nv else self.initial_cash
