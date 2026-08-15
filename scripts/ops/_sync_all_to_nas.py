#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 laap-AGI 全量更新 → NAS 同步（按用户约定：api.py 同步，aris_cognitive_bridge.py 不同步）

清单:
  - laap/paper_trading/*.py       23 个（多因子重构 + 守卫修复）
  - laap/evolution/true_rsi.py    M4 守卫路径兼容修复
  - laap_brain/api.py             量化每日调度器
  - tests/*.py                    4 个测试文件
  - docs/*.md                     全部论文/实施文档
  - scripts/*.py                  回测/拉取/验证脚本
  - .env.example                  环境变量示例
"""
import hashlib
import os
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"
LOCAL_ROOT = r"D:\laap-AGI"

FILES = [
    # paper_trading 全目录
    "laap/paper_trading/__init__.py",
    "laap/paper_trading/backtest_runner.py",
    "laap/paper_trading/daily_pipeline.py",
    "laap/paper_trading/db.py",
    "laap/paper_trading/decision_record.py",
    "laap/paper_trading/export_real_data.py",
    "laap/paper_trading/kline_source.py",
    "laap/paper_trading/ledger.py",
    "laap/paper_trading/llm_refine.py",
    "laap/paper_trading/market_source.py",
    "laap/paper_trading/memory_bridge.py",
    "laap/paper_trading/models.py",
    "laap/paper_trading/paper_replay.py",
    "laap/paper_trading/paper_service.py",
    "laap/paper_trading/param_evolver.py",
    "laap/paper_trading/param_extractor.py",
    "laap/paper_trading/quant_evolution.py",
    "laap/paper_trading/settle.py",
    "laap/paper_trading/significance.py",
    "laap/paper_trading/strategy.py",
    "laap/paper_trading/trade_fitness.py",
    "laap/paper_trading/trading_self.py",
    "laap/paper_trading/walkforward.py",
    # 核心
    "laap/evolution/true_rsi.py",
    "laap_brain/api.py",
    # 测试
    "tests/test_backtest_runner.py",
    "tests/test_paper_enhancements.py",
    "tests/test_quant_api.py",
    "tests/test_param_evolver.py",
    # 文档
    "docs/evidence-review.md",
    "docs/memory-evolution-closed-loop-plan.md",
    "docs/paper-honest-negative-framing.md",
    "docs/paper-observation-runbook.md",
    "docs/phase2-multi-factor-strategy-plan.md",
    "docs/phase4-gap-assessment.md",
    "docs/rsi-paper-evidence-verification.md",
    # 脚本
    "scripts/backtest_multi_symbol.py",
    "scripts/check_paper_performance.py",
    "scripts/cross_sectional_scan.py",
    "scripts/fetch_kline_600519.py",
    "scripts/fetch_universe.py",
    "scripts/index_timing_scan.py",
    "scripts/verify_oos_gate_real.py",
    "scripts/verify_system_components_real.py",
    "scripts/rsi_oos_backtest.py",
    # 环境示例
    ".env.example",
    # 注意: aris_brain/aris_cognitive_bridge.py 按用户要求不同步
]


def md5_norm(data: bytes) -> str:
    return hashlib.md5(data.replace(b"\r\n", b"\n")).hexdigest()


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PASS,
              timeout=15, banner_timeout=15, auth_timeout=15,
              allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    ok, skip = 0, 0
    for rel in FILES:
        local = os.path.join(LOCAL_ROOT, rel.replace("/", os.sep))
        if not os.path.exists(local):
            print(f"  [skip] 本地不存在: {rel}")
            skip += 1
            continue
        sftp.put(local, f"{NAS_ROOT}/{rel}")
        ok += 1
    sftp.close()
    print(f"上传完成: {ok} 文件, 跳过 {skip}")

    sftp = c.open_sftp()
    mismatch = 0
    for rel in FILES:
        local = os.path.join(LOCAL_ROOT, rel.replace("/", os.sep))
        if not os.path.exists(local):
            continue
        with open(local, "rb") as f:
            loc_md5 = md5_norm(f.read())
        try:
            with sftp.open(f"{NAS_ROOT}/{rel}", "rb") as f:
                nas = f.read()
        except Exception:
            mismatch += 1
            print(f"  [MISS] {rel}")
            continue
        if md5_norm(nas) != loc_md5:
            mismatch += 1
            print(f"  [MISMATCH] {rel}")
    sftp.close()
    print(f"校验: {'全部一致 ✅' if mismatch == 0 else f'{mismatch} 不一致 ❌'}")
    c.close()


if __name__ == "__main__":
    main()
