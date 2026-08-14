# -*- coding: utf-8 -*-
"""
paper_trading 工具包 — 让Aris接管全功能

所有工具通过调用 paper_trading/cli.py 的 dispatch() 实现，
避免直接导入带来的依赖问题。
"""

import sys, os, json, subprocess
from pathlib import Path
from typing import Dict, Any, Optional

# paper_trading 项目路径
PT_ROOT = r"D:\leanpython\daily_stock_analysis"
if PT_ROOT not in sys.path:
    sys.path.insert(0, PT_ROOT)

CMD = r"D:\leanpython\daily_stock_analysis\paper_trading\cli.py"


def _run_pt(args: list, timeout: int = 60) -> str:
    """运行 paper_trading CLI，返回输出。"""
    try:
        r = subprocess.run(
            [sys.executable, CMD] + args,
            capture_output=True, text=True, timeout=timeout,
            cwd=PT_ROOT, encoding='utf-8', errors='replace'
        )
        out = r.stdout[-3000:] if len(r.stdout) > 3000 else r.stdout
        err = r.stderr[-800:] if len(r.stderr) > 800 else r.stderr
        return out + (f"\n[stderr]\n{err}" if err else "")
    except subprocess.TimeoutExpired:
        return "[超时]"
    except FileNotFoundError:
        return f"[错误] paper_trading/cli.py 不存在: {CMD}"
    except Exception as e:
        return f"[错误] {e}"


# ─── 账户管理 ──────────────────────────────────────────────

def pt_account_list() -> str:
    """列出所有虚拟账户。"""
    return _run_pt(["account", "list"])


def pt_account_create(name: str = "default") -> str:
    """创建新虚拟账户。"""
    return _run_pt(["account", "create", "--name", name])


def pt_account_show(account_id: int) -> str:
    """查看账户详情。"""
    return _run_pt(["account", "show", "--account-id", str(account_id)])


def pt_account_positions(account_id: int) -> str:
    """查看账户持仓。"""
    return _run_pt(["account", "positions", "--account-id", str(account_id)])


def pt_account_orders(account_id: int) -> str:
    """查看账户委托。"""
    return _run_pt(["account", "orders", "--account-id", str(account_id)])


def pt_account_trades(account_id: int) -> str:
    """查看账户成交。"""
    return _run_pt(["account", "trades", "--account-id", str(account_id)])


def pt_account_signals(account_id: int) -> str:
    """查看账户信号。"""
    return _run_pt(["account", "signals", "--account-id", str(account_id)])


def pt_account_netvalue(account_id: int) -> str:
    """查看净值曲线。"""
    return _run_pt(["account", "net-value", "--account-id", str(account_id)])


def pt_account_delete(account_id: int) -> str:
    """删除账户。"""
    return _run_pt(["account", "delete", "--account-id", str(account_id)])


# ─── 策略管理 ──────────────────────────────────────────────

def pt_strategy_list() -> str:
    """列出所有策略。"""
    return _run_pt(["strategy", "list"])


def pt_strategy_show(name: str) -> str:
    """查看策略详情。"""
    return _run_pt(["strategy", "show", "--name", name])


def pt_strategy_scaffold(name: str) -> str:
    """创建策略模板。"""
    return _run_pt(["strategy", "scaffold", "--name", name])


def pt_strategy_evaluate(name: str, account_id: int = 1) -> str:
    """评估策略。"""
    return _run_pt(["strategy", "evaluate", "--name", name, "--account-id", str(account_id)])


def pt_strategy_import(file: str) -> str:
    """导入策略文件。"""
    return _run_pt(["strategy", "import", "--file", file])


def pt_strategy_lifecycle(account_id: int) -> str:
    """策略生命周期管理。"""
    return _run_pt(["strategy", "lifecycle", "--account-id", str(account_id)])


# ─── 回测分析 ──────────────────────────────────────────────

def pt_backtest_run(strategy: str, account_id: int = 1, start: str = None, end: str = None) -> str:
    """运行回测。"""
    args = ["backtest", "run", "--strategy", strategy, "--account-id", str(account_id)]
    if start:
        args.extend(["--start", start])
    if end:
        args.extend(["--end", end])
    return _run_pt(args)


# ─── 实时监听 ──────────────────────────────────────────────

def pt_listen_start(account_id: int = 1) -> str:
    """启动实时监听。"""
    return _run_pt(["listen", "start", "--account-id", str(account_id)])


def pt_listen_stop(account_id: int = 1) -> str:
    """停止实时监听。"""
    return _run_pt(["listen", "stop", "--account-id", str(account_id)])


def pt_listen_status(account_id: int = 1) -> str:
    """查看监听状态。"""
    return _run_pt(["listen", "status", "--account-id", str(account_id)])


# ─── 订单操作 ──────────────────────────────────────────────

def pt_order_submit(account_id: int, symbol: str, side: str, qty: float, price: float = 0.0, order_type: str = "limit") -> str:
    """提交订单。"""
    return _run_pt([
        "order", "submit",
        "--account-id", str(account_id),
        "--symbol", symbol,
        "--side", side,  # buy/sell
        "--qty", str(qty),
        "--price", str(price) if price else "0",
        "--type", order_type  # limit/market
    ])


def pt_order_cancel(order_id: str, account_id: int = 1) -> str:
    """撤销订单。"""
    return _run_pt(["order", "cancel", "--order-id", order_id, "--account-id", str(account_id)])


def pt_order_list(account_id: int = 1) -> str:
    """查看订单列表。"""
    return _run_pt(["order", "list", "--account-id", str(account_id)])


# ─── 风控管理 ──────────────────────────────────────────────

def pt_risk_check(account_id: int = 1) -> str:
    """检查风控状态。"""
    return _run_pt(["risk", "check", "--account-id", str(account_id)])


def pt_risk_config(account_id: int = 1) -> str:
    """查看风控配置。"""
    return _run_pt(["risk", "config", "--account-id", str(account_id)])


def pt_risk_snapshot(account_id: int = 1) -> str:
    """获取风控快照。"""
    return _run_pt(["risk", "snapshot", "--account-id", str(account_id)])


# ─── 绩效分析 ──────────────────────────────────────────────

def pt_performance(account_id: int = 1) -> str:
    """查看绩效报告。"""
    return _run_pt(["performance", "show", "--account-id", str(account_id)])


def pt_performance_summary(account_id: int = 1) -> str:
    """绩效摘要。"""
    return _run_pt(["performance", "summary", "--account-id", str(account_id)])


# ─── 系统健康 ──────────────────────────────────────────────

def pt_health() -> str:
    """系统健康检查。"""
    return _run_pt(["health"])


def pt_system_status() -> str:
    """系统状态概览。"""
    return _run_pt(["status"])


# ─── 工具注册表 ────────────────────────────────────────────

PAPER_TRADING_TOOLS: Dict[str, Dict[str, Any]] = {
    # 账户
    "pt_account_list": {"fn": pt_account_list, "desc": "列出所有虚拟账户"},
    "pt_account_create": {"fn": pt_account_create, "desc": "创建新虚拟账户 (参数: name)"},
    "pt_account_show": {"fn": pt_account_show, "desc": "查看账户详情 (参数: account_id)"},
    "pt_account_positions": {"fn": pt_account_positions, "desc": "查看账户持仓 (参数: account_id)"},
    "pt_account_orders": {"fn": pt_account_orders, "desc": "查看账户委托 (参数: account_id)"},
    "pt_account_trades": {"fn": pt_account_trades, "desc": "查看账户成交 (参数: account_id)"},
    "pt_account_signals": {"fn": pt_account_signals, "desc": "查看账户信号 (参数: account_id)"},
    "pt_account_netvalue": {"fn": pt_account_netvalue, "desc": "查看净值曲线 (参数: account_id)"},
    "pt_account_delete": {"fn": pt_account_delete, "desc": "删除账户 (参数: account_id)"},
    # 策略
    "pt_strategy_list": {"fn": pt_strategy_list, "desc": "列出所有策略"},
    "pt_strategy_show": {"fn": pt_strategy_show, "desc": "查看策略详情 (参数: name)"},
    "pt_strategy_scaffold": {"fn": pt_strategy_scaffold, "desc": "创建策略模板 (参数: name)"},
    "pt_strategy_evaluate": {"fn": pt_strategy_evaluate, "desc": "评估策略 (参数: name, account_id)"},
    "pt_strategy_import": {"fn": pt_strategy_import, "desc": "导入策略文件 (参数: file)"},
    "pt_strategy_lifecycle": {"fn": pt_strategy_lifecycle, "desc": "策略生命周期管理 (参数: account_id)"},
    # 回测
    "pt_backtest_run": {"fn": pt_backtest_run, "desc": "运行回测 (参数: strategy, account_id, start, end)"},
    # 监听
    "pt_listen_start": {"fn": pt_listen_start, "desc": "启动实时监听"},
    "pt_listen_stop": {"fn": pt_listen_stop, "desc": "停止实时监听"},
    "pt_listen_status": {"fn": pt_listen_status, "desc": "查看监听状态"},
    # 订单
    "pt_order_submit": {"fn": pt_order_submit, "desc": "提交订单 (参数: account_id, symbol, side, qty, price, order_type)"},
    "pt_order_cancel": {"fn": pt_order_cancel, "desc": "撤销订单 (参数: order_id, account_id)"},
    "pt_order_list": {"fn": pt_order_list, "desc": "查看订单列表"},
    # 风控
    "pt_risk_check": {"fn": pt_risk_check, "desc": "检查风控状态"},
    "pt_risk_config": {"fn": pt_risk_config, "desc": "查看风控配置"},
    "pt_risk_snapshot": {"fn": pt_risk_snapshot, "desc": "获取风控快照"},
    # 绩效
    "pt_performance": {"fn": pt_performance, "desc": "查看绩效报告"},
    "pt_performance_summary": {"fn": pt_performance_summary, "desc": "绩效摘要"},
    # 系统
    "pt_health": {"fn": pt_health, "desc": "系统健康检查"},
    "pt_system_status": {"fn": pt_system_status, "desc": "系统状态概览"},
}


def register_paper_trading_tools(registry) -> int:
    """
    将 paper_trading 工具注册到 ARIS 规则引擎。
    返回注册的工具数量。
    """
    count = 0
    for name, info in PAPER_TRADING_TOOLS.items():
        registry.register(name, info["fn"], info["desc"])
        count += 1
    return count


if __name__ == "__main__":
    # 测试：直接运行
    print("paper_trading_tools.py 加载成功")
    print(f"共注册 {len(PAPER_TRADING_TOOLS)} 个工具")
    print("\n可用工具:")
    for name in sorted(PAPER_TRADING_TOOLS.keys()):
        print(f"  - {name}: {PAPER_TRADING_TOOLS[name]['desc']}")
