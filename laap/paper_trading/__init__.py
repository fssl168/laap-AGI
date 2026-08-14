"""LAAP Paper Trading — 最小真实交易循环 + 记忆×自进化闭环承载框架。

参考 DSA 的 paper_trading 精简而来，在 AGI 项目内新建，让
UnifiedMemory（记忆）+ 代码级受限递归（自进化）接上真实交易决策反馈。

包结构:
  models.py          数据模型（dataclass + 枚举）
  db.py              SQLite 持久化（schema + 连接管理）
  ledger.py          最小 OMS（下单/成交/平仓/净值）
  market_source.py   行情源（Live 真实优先 / Stub fallback）
  settle.py          日终结算（MTM）
  decision_record.py 决策留痕 + 结果回填 + 教训提炼
  memory_bridge.py   记忆桥接（沉淀/检索/注入）
  trade_fitness.py   交易适应度
  backtest_runner.py 样本外回测 runner
  quant_evolution.py 代码级受限递归编排
"""

from laap.paper_trading.models import (
    OrderStatus,
    DecisionAction,
    PaperSignal,
    PaperOrder,
    PaperTrade,
    DecisionRecord,
    OutcomeRecord,
    PaperNetValue,
)
from laap.paper_trading.db import PaperDB

__all__ = [
    "OrderStatus",
    "DecisionAction",
    "PaperSignal",
    "PaperOrder",
    "PaperTrade",
    "DecisionRecord",
    "OutcomeRecord",
    "PaperNetValue",
    "PaperDB",
]
