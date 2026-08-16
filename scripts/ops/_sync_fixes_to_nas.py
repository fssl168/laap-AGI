#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传修复文件到 NAS: 备份 → 上传 → md5 归一化校验。
覆盖 (2026-08-16 修订): pt_* 工具/rules_defs/rules_tools/规则引擎 qty 修复/
交易日门 quant_bridge/记忆双写超时/相关测试/使用指南/实施计划
"""
import hashlib, os, time, paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"
LOCAL_ROOT = r"D:\laap-AGI"
STAMP = time.strftime("%Y%m%d_%H%M%S")

FILES = [
    # 规则层 + API (已入库)
    "aris_brain/paper_trading_tools.py",
    "aris_brain/rules_defs.py",
    "aris_brain/rules_tools.py",
    "aris_brain/rules_engine.py",
    "aris_brain/longform_synthesizer.py",
    "laap/agi/cognitive_bus.py",
    "laap/agi/tool_router.py",
    "laap_brain/api.py",
    # paper_trading 本地资产 (NAS 主存储)
    "laap/paper_trading/quant_bridge.py",
    "laap/paper_trading/memory_bridge.py",
    "laap/paper_trading/quant_config.py",
    "laap/paper_trading/trading_self.py",
    "laap/paper_trading/paper_service.py",
    "laap/paper_trading/backtest_runner.py",
    "laap/paper_trading/daily_pipeline.py",
    "laap/paper_trading/indicators.py",
    "laap/paper_trading/strategy_templates.py",
    "laap/paper_trading/paper_replay.py",
    "laap/paper_trading/news_gate.py",
    "laap/paper_trading/news_verifier.py",
    "laap/paper_trading/news_intel.py",
    "laap/paper_trading/news_pipeline.py",
    # 测试
    "tests/test_quant_bridge_phase1.py",
    "tests/test_quant_bridge_phase2.py",
    "tests/test_quant_bridge_phase3.py",
    "tests/test_trading_day_gate.py",
    "tests/test_ledger_fees.py",
    "tests/test_paper_e2e.py",
    "tests/test_trading_self.py",
    "tests/test_laap_tools.py",
    "tests/test_data_sources.py",
    "tests/test_paper_phase4.py",
    "tests/test_market_source.py",
    "tests/test_api_security.py",
    "tests/test_indicators.py",
    "tests/test_strategy_templates.py",
    "tests/test_daily_pipeline.py",
    "tests/test_news_gate.py",
    # 文档
    "docs/paper_trading_aris_integration_plan.md",
    "docs/paper_trading_aris_usage.md",
    "docs/paper_trading_strategy_upgrade_plan.md",
    "docs/paper_trading_strategy_eval_600519.md",
    "docs/paper_trading_strategy_eval_multi.md",
    "docs/paper_trading_strategy_eval_full.md",
    # 配置
    ".env.example",
]

def md5_norm(data: bytes) -> str:
    return hashlib.md5(data.replace(b"\r\n", b"\n")).hexdigest()

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()

# 1. 备份 NAS 侧已有文件
for rel in FILES:
    remote = f"{NAS_ROOT}/{rel}"
    try:
        sftp.stat(remote)
        bak = f"{remote}.bak_sync_{STAMP}"
        sftp.rename(remote, bak)
        print(f"[BAK] {rel} -> .bak_sync_{STAMP}")
    except FileNotFoundError:
        print(f"[NEW] {rel} (NAS 原无此文件, 跳过备份)")

# 2. 上传 + md5 校验
for rel in FILES:
    local = os.path.join(LOCAL_ROOT, rel.replace("/", os.sep))
    with open(local, "rb") as f:
        data = f.read()
    remote = f"{NAS_ROOT}/{rel}"
    sftp.putfo(__import__("io").BytesIO(data), remote)
    # 回读校验
    with sftp.open(remote, "rb") as rf:
        remote_data = rf.read()
    lmd5, rmd5 = md5_norm(data), md5_norm(remote_data)
    status = "OK" if lmd5 == rmd5 else "MISMATCH!"
    print(f"[{'OK' if lmd5==rmd5 else 'FAIL'}] {rel} local={lmd5[:10]} remote={rmd5[:10]} {status}")

sftp.close(); c.close()
print("DONE")
