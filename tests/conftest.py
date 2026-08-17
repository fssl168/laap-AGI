"""LAAP 测试公共 fixture 与配置。

- laap_api_live: 要求 localhost:11546 运行 LAAP daemon；未运行则跳过网络测试。
"""
import os
import urllib.request

import pytest

# 测试隔离：存储后端强制 sqlite（不连 NAS PG16）
# 2026-08-18: setdefault→直接赋值。Hermes 会话 shell 环境自带
# PAPER_TRADING_DB_BACKEND=postgres（source 过 .env），setdefault 不覆盖已存在值，
# 导致测试静默连 PG（_brief 的 SQLite date() 语法在 PG 上崩）。测试必须物理隔离。
os.environ["PAPER_TRADING_DB_BACKEND"] = "sqlite"
# K线库隔离 (2026-08-18): 与交易库同纪律——不连 NAS PG16 watchlist_kline_store
os.environ["KLINE_DB_BACKEND"] = "sqlite"
# 认知引擎 DB 隔离 (2026-08-17): 强制 sqlite + 临时库, 不碰生产 data/laap.db / PG
os.environ["COGNITIVE_DB_BACKEND"] = "sqlite"
os.environ.setdefault(
    "COGNITIVE_DB_PATH",
    os.path.join(os.environ.get("TEMP", "/tmp"), "laap_test_cognitive.db"))
# API 鉴权隔离 (2026-08-18): 置空 LAAP_API_KEY。api 模块 import 时会 load_dotenv
# 从 .env 注入真实 key，导致假设"默认开放 API"的端点测试（test_laap_api/test_laap_tools）
# 被 auth_middleware 拒 401。load_dotenv(override=False) 不会覆盖已存在的空值；
# 鉴权行为由 test_mcp_tools 用 monkeypatch.setenv/delenv 单测覆盖。
os.environ["LAAP_API_KEY"] = ""

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
