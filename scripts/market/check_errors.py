# -*- coding: utf-8 -*-
"""LAAP 错误闭环 CLI — 发现→分析→处理→总结，自动推送前端。

用法:
  1) 终端查看闭环总结（近 10 分钟）:
       python scripts/market/check_errors.py
  2) 闭环并推送 QQ（复用原频道）:
       python scripts/market/check_errors.py --push qq
  3) 推送微信:
       python scripts/market/check_errors.py --push weixin
  4) 推送飞书:
       python scripts/market/check_errors.py --push feishu
  5) 正常状态也推送（定时巡检确认存活）:
       python scripts/market/check_errors.py --push qq --report-normal
  6) JSON 输出（供其他工具消费）:
       python scripts/market/check_errors.py --json

定时接入（Windows 计划任务 / cron，周期跑即形成闭环）:
    python scripts/market/check_errors.py --window 600 --push qq
    python scripts/market/check_errors.py --window 600 --push weixin
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from laap.paper_trading.error_monitor import run_closed_loop


def _ensure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> None:
    _ensure_utf8()
    ap = argparse.ArgumentParser(description="LAAP 错误闭环巡检")
    ap.add_argument("--window", type=int, default=600,
                    help="日志扫描时间窗口秒（默认 600）")
    ap.add_argument("--push", choices=["cli", "qq", "weixin", "feishu"],
                    default="cli", help="推送通道")
    ap.add_argument("--target", default="",
                    help="hermes target 覆盖（默认 qqbot/weixin 主会话）")
    ap.add_argument("--report-normal", action="store_true",
                    help="无异常时也推送'正常'状态（定时巡检用）")
    ap.add_argument("--no-persist", action="store_true",
                    help="不写入 error_events 留痕表")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    result = run_closed_loop(
        window_seconds=args.window,
        push_channel=args.push,
        push_target=args.target,
        report_normal=args.report_normal,
        persist_result=not args.no_persist,
    )

    if args.json:
        print(json.dumps({
            "found": result["found"],
            "analyses": result["analyses"],
            "disposition": result["disposition"],
            "db_ok": result["db_ok"],
            "missing_paths": result["missing_paths"],
            "pushed": result["pushed"],
            "persisted": result["persisted"],
        }, ensure_ascii=False, indent=2, default=str))
        return

    print(result["summary"])
    if args.push == "cli":
        # 终端模式：附高优先级明细
        for a in result["analyses"]:
            if a["priority"] == 2:
                print(f"\n🔴 [{a['category']}] {a['sample'][:140]}")
                print(f"   ↳ {a['action']}")


if __name__ == "__main__":
    main()
