"""LAAP — 共享数据源缓存后端（两级：内存 TTL → NAS redis）。

策略:
  1. 内存 TTL 字典（毫秒级，现有 _CACHE 语义，零依赖）
  2. redis（NAS 6379/db5，跨进程/跨天持久）—— 可选，连不上自动降级内存
  3. fail-closed: redis 异常 → 静默降级内存，绝不抛错阻塞数据管线

用法:
    from laap.cache_backend import cache_get, cache_set
    cache_set("em:news:600519:10", items, ttl=3600)   # 1h
    items = cache_get("em:news:600519:10")

键规范: <域>:<子域>:<symbol>:<参数>，如 em:news:600519:10 / em:profile:600519
TTL 建议: 新闻 1h / 研报 6h / 资料 24h（数据更新频率决定）。

2026-08-18: 从 laap.paper_trading.cache_backend 提升为 laap 共享基础设施，
消除认知域（laap.agi）→ 量化域的反向耦合。laap/paper_trading/cache_backend.py
保留为薄兼容 re-export 层，保障存量量化内部导入点无需同步变更。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger("laap.cache_backend")

# 内存 TTL 缓存（进程内，与旧 _CACHE 兼容）
_MEM_CACHE: dict = {}
_MEM_TTL_DEFAULT = int(os.environ.get("CACHE_MEM_TTL", "300"))  # 5min 默认

# redis 客户端（惰性）
_redis = None
_redis_failed = False
_REDIS_URL = os.environ.get("REDIS_URL", "redis://192.168.88.251:6379/5")
_REDIS_ENABLED = os.environ.get("REDIS_CACHE_ENABLED", "1") == "1"
_REDIS_TTL_LIMIT = 7 * 24 * 3600  # redis TTL 上限 7 天


def _get_redis():
    """惰性创建 redis 客户端；失败标记禁用（fail-closed 降级内存）。"""
    global _redis, _redis_failed
    if _redis is not None or _redis_failed or not _REDIS_ENABLED:
        return _redis
    try:
        import redis as _redis_mod
        _redis = _redis_mod.Redis.from_url(
            _REDIS_URL, socket_timeout=2, socket_connect_timeout=2,
            decode_responses=True)
        _redis.ping()  # 连接探测
        logger.info(f"cache_backend: redis connected ({_REDIS_URL})")
    except Exception as e:
        _redis_failed = True
        _redis = None
        logger.warning(f"cache_backend: redis unavailable, fallback to memory ({e})")
    return _redis


def cache_get(key: str) -> Optional[Any]:
    """两级缓存读取：redis → 内存。任一失败静默降级。"""
    import json as _json

    r = _get_redis()
    if r is not None:
        try:
            raw = r.get(key)
            if raw is not None:
                return _json.loads(raw)
        except Exception as e:
            logger.warning(f"cache_backend: redis get failed ({key}): {e}")
    # 内存兜底
    hit = _MEM_CACHE.get(key)
    if hit and (time.time() - hit[0]) < hit[1]:
        return hit[2]
    return None


def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    """两级缓存写入：redis + 内存双写。失败静默（内存仍生效）。"""
    import json as _json

    ttl = ttl if ttl is not None else _MEM_TTL_DEFAULT
    r = _get_redis()
    if r is not None:
        try:
            r.set(key, _json.dumps(value, ensure_ascii=False, default=str),
                  ex=min(ttl, _REDIS_TTL_LIMIT))
        except Exception as e:
            logger.warning(f"cache_backend: redis set failed ({key}): {e}")
    # 内存双写（TTL 秒 → 内存绝对过期时间戳）
    _MEM_CACHE[key] = (time.time(), ttl, value)


def cache_delete(key: str) -> None:
    """删除缓存（两级别删）。"""
    r = _get_redis()
    if r is not None:
        try:
            r.delete(key)
        except Exception:
            pass
    _MEM_CACHE.pop(key, None)


def cache_clear_prefix(prefix: str) -> int:
    """按前缀清缓存（如 'em:news:' 清所有新闻缓存）。返回清除条数。"""
    n = 0
    r = _get_redis()
    if r is not None:
        try:
            keys = [k for k in r.scan_iter(match=f"{prefix}*", count=100)]
            if keys:
                n += r.delete(*keys)
        except Exception:
            pass
    # 内存
    for k in [k for k in _MEM_CACHE if k.startswith(prefix)]:
        _MEM_CACHE.pop(k, None)
        n += 1
    return n


def cache_stats() -> dict:
    """缓存状态（诊断用）。"""
    r = _get_redis()
    redis_ok = r is not None
    return {
        "redis_enabled": _REDIS_ENABLED,
        "redis_connected": redis_ok,
        "redis_url": _REDIS_URL if redis_ok else None,
        "mem_entries": len(_MEM_CACHE),
        "mem_ttl_default": _MEM_TTL_DEFAULT,
    }
