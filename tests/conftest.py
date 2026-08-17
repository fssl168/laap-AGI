"""LAAP 测试公共 fixture 与配置。

- laap_api_live: 要求 localhost:11546 运行 LAAP daemon；未运行则跳过网络测试。
"""
import os
import urllib.request

import pytest

# 测试隔离：存储后端强制 sqlite（不连 NAS PG16）
os.environ.setdefault("PAPER_TRADING_DB_BACKEND", "sqlite")
# 认知引擎 DB 隔离 (2026-08-17): 强制 sqlite + 临时库, 不碰生产 data/laap.db / PG
os.environ.setdefault("COGNITIVE_DB_BACKEND", "sqlite")
os.environ.setdefault(
    "COGNITIVE_DB_PATH",
    os.path.join(os.environ.get("TEMP", "/tmp"), "laap_test_cognitive.db"))

LAAP_API_BASE = "http://localhost:11546"


@pytest.fixture
def laap_api_live():
    """网络测试前置: LAAP daemon 必须可达, 否则跳过。"""
    try:
        with urllib.request.urlopen(f"{LAAP_API_BASE}/health", timeout=2) as resp:
            if resp.status != 200:
                pytest.skip("LAAP API not healthy at " + LAAP_API_BASE)
    except Exception:
        pytest.skip("LAAP API not running at " + LAAP_API_BASE)
    return LAAP_API_BASE
