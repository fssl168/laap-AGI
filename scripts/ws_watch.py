"""LAAP WS 端点接入示例 — 实时盯盘看板。

用法:
  python ws_watch.py                              # 默认全部标的
  python ws_watch.py --symbols 600519 000001      # 指定标的
  python ws_watch.py --theme dark                 # 深色主题
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

# ═══════════════════════════════════════════════════════════════
# 颜色配置
# ═══════════════════════════════════════════════════════════════
ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
    "white": "\033[97m",
    "bg_red": "\033[41m",
    "bg_green": "\033[42m",
    "bg_yellow": "\033[43m",
}


def color(text: str, fg: str = "", bg: str = "", bold: bool = False) -> str:
    codes = []
    if bold:
        codes.append(ANSI["bold"])
    if fg:
        codes.append(ANSI.get(fg, ""))
    if bg:
        codes.append(ANSI.get(bg, ""))
    if not codes:
        return text
    return "".join(codes) + text + ANSI["reset"]


# ═══════════════════════════════════════════════════════════════
# 状态管理
# ═══════════════════════════════════════════════════════════════
class WatchState:
    """盯盘状态"""
    
    def __init__(self, symbols: List[str] = None):
        self.symbols = symbols or []
        self.prices: Dict[str, float] = {}
        self.prev_prices: Dict[str, float] = {}
        self.changes: Dict[str, float] = {}
        self.limits: Dict[str, Dict[str, bool]] = {}
        self.fallback_count: int = 0
        self.event_count: int = 0
        self.start_time: float = 0
        
    def update_tick(self, sym: str, price: float, change_pct: float,
                   is_limit_up: bool, is_limit_down: bool,
                   used_fallback: bool):
        self.prev_prices[sym] = self.prices.get(sym, price)
        self.prices[sym] = price
        self.changes[sym] = change_pct
        self.limits[sym] = {"up": is_limit_up, "down": is_limit_down}
        if used_fallback:
            self.fallback_count += 1
        self.event_count += 1
        
    def render(self) -> str:
        lines = []
        elapsed = datetime.now().strftime("%H:%M:%S")
        total = len(self.prices)
        
        lines.append(color(f"LAAP 盯盘 {elapsed} | {total} 只 | {self.event_count} 事件", 
                         "cyan", bold=True))
        lines.append("-" * 70)
        
        # 表头
        lines.append(color(f"{'代码':<8} {'价格':>10} {'涨跌':>8} {'状态':>8}", 
                         "yellow", bold=True))
        lines.append("-" * 70)
        
        # 按涨跌排序（过滤 None symbol）
        sorted_syms = sorted(
            [s for s in self.prices.keys() if s],
            key=lambda s: self.changes.get(s, 0),
            reverse=True
        )
        
        for sym in sorted_syms[:20]:  # 最多显示 20 只
            price = self.prices[sym]
            change = self.changes.get(sym, 0)
            limits = self.limits.get(sym, {})
            
            # 颜色
            if limits.get("up"):
                status = color("🔴涨停", "white", "bg_red", bold=True)
            elif limits.get("down"):
                status = color("🟢跌停", "white", "bg_green", bold=True)
            elif change > 0:
                status = color(f"+{change:.2f}%", "red")
            elif change < 0:
                status = color(f"{change:.2f}%", "green")
            else:
                status = color(f"{change:.2f}%", "white")
            
            sym_colored = color(sym.ljust(8), "blue")
            price_colored = color(f"{price:>10.2f}", "bold")
            line = f"{sym_colored} {price_colored} {status}"
            lines.append(line)
        
        if total > 20:
            lines.append(color(f"... 还有 {total - 20} 只", "dim"))
        
        # 统计
        lines.append("-" * 70)
        up_count = sum(1 for c in self.changes.values() if c > 0)
        down_count = sum(1 for c in self.changes.values() if c < 0)
        limit_up = sum(1 for l in self.limits.values() if l.get("up"))
        lines.append(color(f"涨: {up_count}  跌: {down_count}  涨停: {limit_up}"
                          f"  降级: {self.fallback_count}", "dim"))
        
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════════════
async def main(symbols: List[str], refresh_ms: int = 5000):
    url = "ws://127.0.0.1:11546/v1/quant/events/ws"
    
    # 构建订阅主题
    if symbols:
        topics = [f"market.tick.{s}.price" for s in symbols] + \
                 [f"market.orderbook.{s}" for s in symbols]
    else:
        topics = ["market.tick.*", "market.orderbook.*", 
                  "market.limitup.*", "system.status"]
    
    state = WatchState(symbols)
    state.start_time = asyncio.get_event_loop().time()
    
    # SIGINT 处理
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(graceful_exit()))
        except (NotImplementedError, OSError):
            pass
    
    async def graceful_exit():
        print("\n" + color("断开连接...", "yellow"))
        sys.exit(0)
    
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            print(color(f"✅ 已连接 | 订阅 {len(topics)} 个主题", "green"))
            print(color("按 Ctrl+C 退出\n", "dim"))
            
            last_render = 0
            while True:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                except asyncio.TimeoutError:
                    # 定期刷新界面
                    if asyncio.get_event_loop().time() - last_render >= refresh_ms / 1000:
                        os.system("cls" if os.name == "nt" else "clear")
                        print(state.render())
                        last_render = asyncio.get_event_loop().time()
                    continue
                
                if msg.type in (aiohttp.WSMsgType.CLOSED, 
                               aiohttp.WSMsgType.ERROR):
                    print(color("⚠️ 连接关闭", "yellow"))
                    break
                
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                
                try:
                    ev = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                
                state.event_count += 1
                t = ev.get("type", "")
                p = ev.get("payload", {})
                sym = p.get("symbol", "")
                
                # 处理 tick
                if "tick" in t and "price" in p:
                    state.update_tick(
                        sym,
                        p.get("price", 0),
                        p.get("change_pct", 0),
                        p.get("is_limit_up", False),
                        p.get("is_limit_down", False),
                        p.get("used_fallback", False)
                    )
                    
                    # 涨停提醒
                    if p.get("is_limit_up"):
                        print(color(f"🚀 {sym} 涨停！@ {p['price']:.2f}", 
                                   "white", "bg_red", bold=True))
                
                # 处理 orderbook
                elif "orderbook" in t:
                    bids = p.get("bids", [])
                    asks = p.get("asks", [])
                    if bids and asks:
                        spread = asks[0]["price"] - bids[0]["price"]
                        # 价差异常提醒
                        if spread > 0.1 and bids[0]["price"] > 10:
                            print(color(f"⚠️ {sym} 价差 {spread:.2f} 异常", 
                                       "yellow"))
                
                # 刷新界面
                if asyncio.get_event_loop().time() - last_render >= refresh_ms / 1000:
                    os.system("cls" if os.name == "nt" else "clear")
                    print(state.render())
                    last_render = asyncio.get_event_loop().time()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="LAAP 实时盯盘")
    ap.add_argument("--symbols", nargs="+", default=None, 
                   help="订阅标的（默认全部）")
    ap.add_argument("--refresh", type=int, default=5000,
                   help="刷新间隔(ms)")
    args = ap.parse_args()
    
    try:
        asyncio.run(main(args.symbols, args.refresh))
    except KeyboardInterrupt:
        print(color("\n已退出", "dim"))
