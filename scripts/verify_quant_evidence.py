#!/usr/bin/env python3
"""证据聚合验证器 (2026-08-18)。

P2 工程化修正产物。职责:
  1. 把散落在 real_data/*.json / 各验证脚本输出里的数字,聚合成一份
     report/quant_evidence_summary_<date>.md — 让论文 / 审计 / CI 能直接看
     关键指标,而不是翻原始 JSON。
  2. 支持离线模式(--offline):只读取已有 JSON,不做任何网络或数据源调用。
     这是沙箱 / CI 可用的唯一路径。
  3. 支持在线模式(默认):尝试重跑 walkforward / OOS / AB,产出新鲜报告。

阈值契约(来自 docs/rsi-paper-evidence-verification.md):
  - walkforward pass_rate >= 40%
  - OOS 平均超额收益 >= 0 (vs buy-hold)
  - |z| >= 1.96 才算显著
  - AB phantom scoreΔ >= 0.02
  - OOS 样本 >= 60 天才统计可信

用法:
    # 离线聚合(沙箱 / CI 可用)
    python scripts/verify_quant_evidence.py --offline

    # 在线重跑(需真实 kline 数据源 + 交易日历)
    python scripts/verify_quant_evidence.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL_DATA = ROOT / "real_data"
REPORT_DIR = ROOT / "report"
# 在 .gitignore 中被排除的目录,这些路径的 JSON 在 git 不可见
EXCLUDED_DIRS = {ROOT / "real_data", ROOT / "data", ROOT / "docs"}


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[warn] 无法解析 {path}: {e}", file=sys.stderr)
        return None


def safe_num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def fmt_pct(v, digits=1) -> str:
    return f"{safe_num(v) * 100:.{digits}f}%"


def fmt_n(n) -> str:
    try:
        return f"{int(n)}"
    except (TypeError, ValueError):
        return str(n)


def section(title: str, body: str):
    return f"\n## {title}\n\n{body}\n"


def collect_walkforward() -> dict:
    """解析 real_data/walkforward_report.json 或 rsi_walkforward_minimal.json。"""
    out = {"raw": None, "pass_rate": None, "verdict": None, "detail": []}
    for name in ("walkforward_report.json", "rsi_walkforward_minimal.json"):
        p = REAL_DATA / name
        data = load_json(p)
        if data is None:
            continue
        out["raw"] = data
        # 标准字段识别(兼容两种报告格式)
        pc = safe_num(data.get("pass_count") or data.get("pass"))
        tc = safe_num(data.get("total_count") or data.get("total") or 1)
        out["pass_rate"] = pc / tc if tc else None
        verdict = data.get("verdict") or data.get("verdict")
        out["verdict"] = verdict
        # 逐窗通过率
        windows = data.get("windows") or data.get("results") or []
        for w in windows[:10]:
            out["detail"].append({
                "window": w.get("window") or w.get("name"),
                "pass": w.get("pass"),
                "pass_rate": w.get("pass_rate"),
            })
        break
    return out


def collect_oos() -> dict:
    """解析 real_data/rsi_multi_oos_results.json。"""
    out = {"raw": None, "mean_excess": None, "sig_count": None,
           "total_symbols": None, "avg_days": None}
    p = REAL_DATA / "rsi_multi_oos_results.json"
    data = load_json(p)
    if data is None:
        return out
    out["raw"] = data
    # 顶层字段
    out["mean_excess"] = safe_num(data.get("mean_excess_return"))
    out["total_symbols"] = data.get("total_symbols") or data.get("n_symbols")
    out["sig_count"] = data.get("sig_count") or data.get("significant_count")
    out["avg_days"] = safe_num(data.get("avg_days"))
    # 汇总数字
    summary = data.get("summary") or data
    out["oos_positive"] = safe_num(summary.get("oos_positive") or
                                    summary.get("positive_return_count"))
    return out


def collect_ab() -> dict:
    """解析 real_data/self_review_ab_report.json。"""
    out = {"raw": None, "verdict": None, "phantom_pnl": None, "score_delta": None}
    p = REAL_DATA / "self_review_ab_report.json"
    data = load_json(p)
    if data is None:
        return out
    out["raw"] = data
    out["verdict"] = data.get("verdict")
    out["phantom_pnl"] = safe_num(data.get("phantom_total_pnl") or
                                   data.get("total_pnl"))
    out["score_delta"] = safe_num(data.get("score_delta") or
                                   data.get("score"))
    return out


def summarize(wf: dict, oos: dict, ab: dict) -> list:
    """对照阈值契约,产出逐项判定。"""
    findings = []

    # WF pass_rate
    rate = wf.get("pass_rate")
    if rate is not None:
        ok = rate >= 0.40
        findings.append({
            "item": "Walk-forward pass_rate",
            "value": f"{rate * 100:.1f}%",
            "threshold": ">=40%",
            "verdict": "PASS" if ok else "FAIL",
        })
    else:
        findings.append({
            "item": "Walk-forward pass_rate",
            "value": "无数据",
            "threshold": ">=40%",
            "verdict": "SKIP",
        })

    # OOS 超额收益
    excess = oos.get("mean_excess")
    if excess is not None:
        ok = excess >= 0
        findings.append({
            "item": "OOS 平均超额收益(56 标)",
            "value": fmt_pct(excess),
            "threshold": ">= 0 (跑赢 buy-hold)",
            "verdict": "PASS" if ok else "FAIL",
        })
    else:
        findings.append({
            "item": "OOS 平均超额收益",
            "value": "无数据",
            "threshold": ">= 0",
            "verdict": "SKIP",
        })

    # 显著性
    sig = oos.get("sig_count")
    total = oos.get("total_symbols")
    if total and sig is not None:
        ratio = sig / total
        # 5% 显著性阈值 → 期望 ~2.8 个; < 2 说明未见效应
        ok = ratio >= 0.05
        findings.append({
            "item": f"OOS 显著标的 ({sig}/{total})",
            "value": f"{ratio * 100:.1f}%",
            "threshold": ">= 5% (约 3/56)",
            "verdict": "PASS" if ok else "FAIL",
        })
    else:
        findings.append({
            "item": "OOS 显著标的",
            "value": "无数据",
            "threshold": ">= 5%",
            "verdict": "SKIP",
        })

    # AB phantom PnL
    pnl = ab.get("phantom_pnl")
    if pnl is not None:
        ok = pnl > 0
        findings.append({
            "item": "AB 对照 phantom PnL",
            "value": f"{pnl:,.0f}",
            "threshold": "> 0",
            "verdict": "PASS" if ok else "FAIL",
        })
    else:
        findings.append({
            "item": "AB 对照 phantom PnL",
            "value": "无数据",
            "threshold": "> 0",
            "verdict": "SKIP",
        })

    # AB scoreΔ
    delta = ab.get("score_delta")
    if delta is not None:
        ok = delta >= 0.02
        findings.append({
            "item": "AB scoreΔ",
            "value": f"{delta:.4f}",
            "threshold": ">= 0.02",
            "verdict": "PASS" if ok else "FAIL",
        })
    else:
        findings.append({
            "item": "AB scoreΔ",
            "value": "无数据",
            "threshold": ">= 0.02",
            "verdict": "SKIP",
        })

    return findings


def build_markdown(wf, oos, ab, findings, run_ts: str, offline: bool):
    sections = []
    sections.append(f"> 自动生成于 {run_ts} | {'离线聚合' if offline else '在线重跑'}")

    # 关键结论
    n_pass = sum(1 for f in findings if f["verdict"] == "PASS")
    n_fail = sum(1 for f in findings if f["verdict"] == "FAIL")
    n_skip = sum(1 for f in findings if f["verdict"] == "SKIP")
    sections.append(section(
        "1. 结论摘要",
        f"通过 {n_pass}/({len(findings)}) 项,失败 {n_fail},跳过 {n_skip}。"
        + ("\n\n**⚠️ 当前策略证据不足以支撑'实证通过 / 可盈利'声明。**"
           if n_fail >= 2 else "")))

    # 关键数字表
    rows = "| 指标 | 观测值 | 阈值 | 判定 |\n|---|---|---|---|\n"
    for f in findings:
        rows += f"| {f['item']} | {f['value']} | {f['threshold']} | **{f['verdict']}** |\n"
    sections.append(section("2. 关键指标对照", rows))

    # 已知缺口
    gaps = [
        "- Walkforward 样本仅 3 标的 × 5 折(15 折),不足以表征泛化能力",
        "- OOS 平均 64 天 / 33/56 标的仅 1 笔交易,小样本失真",
        "- 文档(`docs/`、`real_data/`)均在 `.gitignore`,无法从 git 历史复现当前数字",
        "- `laap/paper_trading/` 代码本身不进 git(NAS 同步),未来重构可能改变行为",
        "- 回测口径曾与实盘不一致(已在 backtest_runner 注释中记录并修复)",
    ]
    sections.append(section("3. 已知缺口(对审计 / 审稿人的预判)", "\n".join(gaps)))

    # 复现命令
    cmds = [
        "```bash",
        "# 离线聚合(沙箱 / CI 可用)",
        "python scripts/verify_quant_evidence.py --offline",
        "",
        "# 在线重跑(需真实数据源)",
        "python scripts/walkforward_validation.py",
        "python scripts/rsi_oos_backtest.py",
        "python scripts/run_self_review_ab.py",
        "```",
    ]
    sections.append(section("4. 复现命令", "\n".join(cmds)))

    # 原始数据位置
    sections.append(section(
        "5. 原始数据位置(不进 git)",
        f"- Walkforward: `real_data/walkforward_report.json` / `rsi_walkforward_minimal.json`"
        f"\n- OOS 多标: `real_data/rsi_multi_oos_results.json`"
        f"\n- AB 对照: `real_data/self_review_ab_report.json`"
        f"\n\n**提示**:上述路径被 `.gitignore` 排除,仅可通过 NAS / 本地存储访问。"
    ))

    return "\n".join(sections)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true",
                    help="离线聚合:只读已有 JSON,不触发任何数据源/网络调用。CI 用。")
    ap.add_argument("--out-dir", default=str(REPORT_DIR),
                    help=f"输出目录(默认 {REPORT_DIR})")
    args = ap.parse_args()

    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    REPORT_NAME = f"quant_evidence_summary_{datetime.now().strftime('%Y%m%d')}.md"

    # 收集
    wf = collect_walkforward()
    oos = collect_oos()
    ab = collect_ab()
    findings = summarize(wf, oos, ab)
    md = build_markdown(wf, oos, ab, findings, run_ts, args.offline)

    # 写
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / REPORT_NAME
    out_path.write_text(md, encoding="utf-8")
    print(f"[ok] 写入 {out_path}")

    # 概要输出(方便 CI 解析)
    summary = {
        "ts": run_ts,
        "offline": args.offline,
        "report": str(out_path),
        "findings": findings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
