# -*- coding: utf-8 -*-
"""WS 实时信号列表 — 最小演示（2026-08-18）。

复用本项目 WS:// 实时数据能力（GET /v1/quant/events/ws, EventBus→WebSocket 桥接），
把实时事件聚合成"信号列表"，输出为 QQ/微信通用的纯文本。

用法:
  1) 真实模式（需服务已启动, 端口 11546; 事件由 LAAP_EVENT_DRIVEN=1 驱动）:
       python scripts/market/demo_ws_signal_list.py --duration 15
  2) 模拟模式（离线演示, 不依赖服务/行情, 立即看效果）:
       python scripts/market/demo_ws_signal_list.py --simulate
  3) 推送到飞书（复用 aris_messenger.send_message）:
       python scripts/market/demo_ws_signal_list.py --simulate --push feishu
  4) 复用原 QQ/微信频道（hermes send --to, Hermes 持有 QQ Bot 凭据与适配器）:
       python scripts/market/demo_ws_signal_list.py --simulate --push qq
       python scripts/market/demo_ws_signal_list.py --simulate --push weixin
       python scripts/market/demo_ws_signal_list.py --list-targets   # 查看可用目标

QQ/微信频道复用（出站走 hermes send, 复用 Hermes 已配置凭据, LAAP 无需持 openid/secret）:
  链路: Hermes gateway/platforms/qqbot — 入站=官方 QQ Bot WebSocket 网关(Hermes 独占),
        出站=REST api.sgroup.qq.com (/v2/users/{openid}/messages, Authorization: QQBot)。
  LAAP 侧: subprocess 调 `hermes send --to qqbot:<openid>|<weixin>:<id>`。

WS 协议（服务端 laap_brain/api.py::handle_quant_events_ws）:
  上行: {"op":"subscribe","topics":[...]} / {"op":"ping"}
  下行: {"type":"...","ts":..., "payload":{...}, "source":"..."}
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# 信号展示关注的主题（默认忽略 market.tick.* 防刷屏; --show-ticks 可开）
SIGNAL_TOPICS = [
    "trade.*",
    "market.limitup.*",
    "market.fault.*",
    "market.auction.*",
    "system.status",
]
WS_URL_DEFAULT = "ws://127.0.0.1:11546/v1/quant/events/ws"


# ── 信号条目 ──────────────────────────────────────────────

def _ts_hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def fmt_signal(ev: Dict[str, Any]) -> str:
    """单条事件 → 一行纯文本信号（QQ/微信通用）。"""
    t = ev.get("type", "?")
    p = ev.get("payload") or {}
    sym = p.get("symbol", "")
    ts = _ts_hhmm(float(ev.get("ts", time.time())))
    if t.startswith("market.tick."):
        fb = " (stub)" if p.get("used_fallback") else ""
        chg = p.get("change_pct")
        chg_s = f" ({chg:+.1f}%)" if chg is not None else ""
        return (f"[{ts}] [行情] {sym} {p.get('price', '')}{chg_s}"
                f" 源:{p.get('source', '?')}{fb}")
    if t.startswith("trade."):
        action = p.get("action", "?")
        qty = p.get("qty", "")
        price = p.get("price", "")
        return (f"[{ts}] [交易] {sym} {action} {qty}股 @ {price}"
                f" (order={p.get('order_id', '')})")
    if t.startswith("market.limitup."):
        chg = p.get("change_pct")
        chg_s = f" ({chg:+.1f}%)" if chg is not None else ""
        return (f"[{ts}] [涨停] {sym} 触板 {p.get('price', '')}{chg_s}"
                f" (源:{p.get('source', '?')})")
    if t.startswith("market.auction."):
        chg = p.get("change_pct")
        chg_s = f" ({chg:+.1f}%)" if chg is not None else ""
        return f"[{ts}] [竞价] {sym} {p.get('price', '')}{chg_s}"
    if t.startswith("market.fault."):
        src = p.get("source", t.rsplit(".", 1)[-1])
        return (f"[{ts}] [故障] 源 {src} 连续失败 "
                f"{p.get('consecutive_failures', '?')} 次: {p.get('reason', '')[:60]}")
    if t == "system.status":
        keys = ", ".join(f"{k}={v}" for k, v in p.items() if k != "ts")
        return f"[{ts}] [状态] {keys[:80]}"
    return f"[{ts}] [{t}] {json.dumps(p, ensure_ascii=False)[:80]}"


def fmt_signal_list(signals: Deque[Dict[str, Any]], conn: str = "") -> str:
    """信号列表 → 多行文本（QQ/微信通用）。"""
    if not signals:
        return "📡 LAAP 信号列表: 暂无信号" + (f" | {conn}" if conn else "")
    lines = [f"📡 LAAP 信号列表 (最近 {len(signals)} 条)"]
    lines.append("─" * 30)
    for i, ev in enumerate(signals, 1):
        lines.append(f"{i}. {fmt_signal(ev)}")
    lines.append("─" * 30)
    lines.append(f"共 {len(signals)} 条信号" + (f" | {conn}" if conn else ""))
    return "\n".join(lines)


# ── 信号聚合 ──────────────────────────────────────────────

class SignalList:
    """WS 事件 → 有界信号列表（按到达顺序, 丢弃最旧）。"""

    def __init__(self, limit: int = 20, show_ticks: bool = False):
        self.signals: Deque[Dict[str, Any]] = deque(maxlen=limit)
        self.counts: Dict[str, int] = {}
        self.total = 0
        self.show_ticks = show_ticks

    def is_signal(self, ev_type: str) -> bool:
        if self.show_ticks and ev_type.startswith("market.tick."):
            return True
        for f in SIGNAL_TOPICS:
            if f.endswith(".*") and ev_type.startswith(f[:-1]):
                return True
            if f == ev_type:
                return True
        return False

    def add(self, ev: Dict[str, Any]) -> None:
        if not self.is_signal(ev.get("type", "")):
            return
        self.signals.append(ev)
        self.counts[ev.get("type", "?")] = self.counts.get(ev.get("type", "?"), 0) + 1
        self.total += 1


# ── 真实 WS 模式 ──────────────────────────────────────────

def _resolve_api_key(cli_key: str) -> str:
    """API Key 优先级: 命令行 > 环境变量 > .env 文件解析（LAAP_API_KEY=...）。

    与 python-dotenv 语义对齐: 剥离行内 `#` 注释与引号（.env 的 key 行可能带
    中文注释, 简单 split 会把注释当 key 导致 401/编码错误）。
    """
    if cli_key:
        return cli_key.strip()
    env_key = os.environ.get("LAAP_API_KEY", "").strip()
    if env_key:
        return env_key
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() != "LAAP_API_KEY":
                continue
            v = v.split("#", 1)[0].strip()  # 剥离行内注释
            return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


async def collect_from_ws(url: str, duration: float, limit: int,
                          show_ticks: bool, api_key: str = "") -> Optional[SignalList]:
    """连接 WS 收集事件 duration 秒, 返回信号列表。"""
    import websockets
    sl = SignalList(limit=limit, show_ticks=show_ticks)
    deadline = time.time() + duration
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    topics = list(SIGNAL_TOPICS)
    if show_ticks:
        topics.append("market.tick.*")  # 服务端按订阅主题过滤, 需显式订阅才推
    try:
        async with websockets.connect(url, ping_interval=None,
                                      additional_headers=headers) as ws:
            await ws.send(json.dumps({"op": "subscribe", "topics": topics}))
            print(f"✅ 已连接 {url}（收集 {duration}s 事件...）", file=sys.stderr)
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                sl.add(ev)
                # 实时打印新信号（stderr, 列表输出走 stdout）
                if sl.is_signal(ev.get("type", "")):
                    print(f"  + {fmt_signal(ev)}", file=sys.stderr)
    except (OSError, ConnectionError, websockets.exceptions.WebSocketException) as e:
        print(f"❌ WS 连接失败: {e}\n"
              f"   提示: 先启动服务 (python -m laap_brain.api), 且事件流需 "
              f"LAAP_EVENT_DRIVEN=1; 若 401 需 --api-key 或 .env 配置 "
              f"LAAP_API_KEY; 离线演示用 --simulate", file=sys.stderr)
        return None
    return sl


# ── 模拟模式（离线最小演示）────────────────────────────────

def simulate_signals(limit: int) -> SignalList:
    """本地构造示例事件 → 展示信号列表效果（不依赖服务/行情/市场时段）。"""
    sl = SignalList(limit=limit, show_ticks=False)
    now = time.time()
    samples = [
        ("trade.600519.buy", {"symbol": "600519", "action": "buy", "qty": 100,
                              "price": 1410.0, "order_id": "ord_20260818_001"}),
        ("market.limitup.000001", {"symbol": "000001", "price": 11.0,
                                   "change_pct": 10.0, "source": "tx"}),
        ("market.auction.600519", {"symbol": "600519", "price": 1405.0,
                                   "change_pct": 2.1}),
        ("market.fault.EmMarketSource",
         {"source": "EmMarketSource", "symbol": "600519",
          "consecutive_failures": 3, "reason": "RemoteDisconnected (模拟)"}),
        ("system.status", {"running": True, "symbols": 34, "interval": 5}),
        ("trade.000001.sell", {"symbol": "000001", "action": "sell", "qty": 200,
                               "price": 11.2, "order_id": "ord_20260818_002"}),
    ]
    for i, (t, p) in enumerate(samples):
        sl.add({"type": t, "ts": now - (len(samples) - i) * 8, "payload": p,
                "source": "simulate"})
    return sl


# ── 推送 ──────────────────────────────────────────────────

def _hermes_exe() -> Optional[str]:
    """定位 hermes CLI（优先 HERMES_EXE env，其次默认安装路径）。"""
    exe = os.environ.get("HERMES_EXE", "")
    if exe and os.path.exists(exe):
        return exe
    default = (Path(os.environ.get("LOCALAPPDATA", ""))
               / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe")
    if default.exists():
        return str(default)
    return None


def hermes_list_targets() -> Optional[str]:
    """hermes send --list → 可用平台目标（QQ/微信等）。"""
    exe = _hermes_exe()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "send", "--list"], capture_output=True,
                           text=True, timeout=30)
        return r.stdout or r.stderr
    except Exception as e:
        print(f"❌ hermes --list 失败: {e}", file=sys.stderr)
        return None


def push_via_hermes(text: str, target: str) -> bool:
    """复用原 QQ/微信频道: hermes send --to <target>（Hermes 持有凭据与适配器）。

    例: hermes send --to qqbot:7897F052C6EE724AF85E4AC7277BB089 --file -
    消息体经 stdin 传入（避免命令行转义/长度问题）。
    """
    exe = _hermes_exe()
    if not exe:
        print("❌ 未找到 hermes CLI (设 HERMES_EXE 或检查安装路径)", file=sys.stderr)
        return False
    try:
        r = subprocess.run([exe, "send", "--to", target, "--file", "-"],
                           input=text,
                           capture_output=True, timeout=60,
                           text=True, encoding="utf-8", errors="replace")
        out = r.stdout or ""
        err = r.stderr or ""
        if r.returncode == 0:
            print(f"✅ 已发送到 {target} (hermes send exit=0)", file=sys.stderr)
            return True
        print(f"❌ hermes send 失败 (exit={r.returncode}): {out} {err}",
              file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ hermes send 异常: {e}", file=sys.stderr)
        return False


def push_text(text: str, target: str) -> None:
    if target == "cli":
        print(text)
    elif target == "feishu":
        try:
            from aris_brain.aris_messenger import send_message
            ok = send_message(text, target="feishu")
            print(f"✅ 已推送到飞书: {'成功' if ok else '失败(检查 FEISHU_APP_ID/SECRET)'}",
                  file=sys.stderr)
        except Exception as e:
            print(f"❌ 飞书推送失败: {e}", file=sys.stderr)
    else:
        # qq / weixin / 任意 hermes 目标: 复用原频道
        push_via_hermes(text, target)


# ── 入口 ──────────────────────────────────────────────────

def _ensure_utf8() -> None:
    """Windows 控制台 GBK 无法输出 emoji → 强制 UTF-8（无 reconfigure 的旧解释器静默跳过）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> None:
    _ensure_utf8()
    ap = argparse.ArgumentParser(description="LAAP WS 实时信号列表最小演示")
    ap.add_argument("--url", default=WS_URL_DEFAULT, help="WS 端点")
    ap.add_argument("--duration", type=float, default=15.0,
                    help="真实模式收集秒数（默认 15）")
    ap.add_argument("--limit", type=int, default=20, help="信号列表条数上限")
    ap.add_argument("--simulate", action="store_true",
                    help="离线模拟模式（不依赖服务/行情）")
    ap.add_argument("--show-ticks", action="store_true",
                    help="把 market.tick.* 也计入信号（默认忽略防刷屏）")
    ap.add_argument("--api-key", default="",
                    help="API Key (Authorization: Bearer; 缺省读 env/.env 的 LAAP_API_KEY)")
    ap.add_argument("--push", choices=["cli", "feishu", "qq", "weixin"],
                    default="cli",
                    help="输出目标: cli 打印(默认) / feishu 推送 / qq 复用原QQ频道 / weixin 复用原微信频道")
    ap.add_argument("--target", default="",
                    help="hermes send 目标 (--push qq/weixin 时用; 例: "
                         "qqbot:7897F052C6EE724AF85E4AC7277BB089)")
    ap.add_argument("--list-targets", action="store_true",
                    help="列出 hermes send 可用目标(QQ/微信等)后退出")
    ap.add_argument("--json", action="store_true", help="JSON 输出信号列表")
    args = ap.parse_args()

    if args.list_targets:
        out = hermes_list_targets()
        if out is None:
            print("❌ hermes CLI 不可用 (设 HERMES_EXE 或检查安装路径)", file=sys.stderr)
            sys.exit(1)
        print(out)
        return

    sl: Optional[SignalList] = None
    if args.simulate:
        print("🧪 模拟模式（离线示例信号, 展示列表效果）", file=sys.stderr)
        sl = simulate_signals(args.limit)
    else:
        api_key = _resolve_api_key(args.api_key)
        sl = asyncio.run(collect_from_ws(args.url, args.duration,
                                         args.limit, args.show_ticks, api_key))
        if sl is None:
            sys.exit(1)

    conn = "模拟" if args.simulate else f"WS 已连接 | 收到 {sl.total} 事件"
    if args.json:
        print(json.dumps(list(sl.signals), ensure_ascii=False, indent=2))
        return
    text = fmt_signal_list(sl.signals, conn)
    if args.push == "qq":
        target = args.target or "qqbot:7897F052C6EE724AF85E4AC7277BB089"
        print(text)  # 终端也展示
        push_via_hermes(text, target)
    elif args.push == "weixin":
        target = args.target or "weixin:o9cq8027tCuiKIn6-EvcwRUzpADY@im.wechat"
        print(text)
        push_via_hermes(text, target)
    else:
        push_text(text, args.push)


if __name__ == "__main__":
    main()
