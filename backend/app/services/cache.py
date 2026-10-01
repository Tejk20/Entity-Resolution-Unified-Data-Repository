"""Redis helpers: search-result caching and small counters."""
from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis
from redis.asyncio.connection import ConnectionPool

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_client: aioredis.Redis | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=50,
            decode_responses=True,
            socket_keepalive=True,
            health_check_interval=30,
        )
    return _pool


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.Redis(connection_pool=get_pool())
    return _client


async def close_redis() -> None:
    global _client, _pool
    if _client is not None:
        await _client.aclose()
        _client = None
    if _pool is not None:
        await _pool.disconnect()
        _pool = None


async def cache_get(key: str) -> Any | None:
    try:
        raw = await get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:  # cache must never break a request
        logger.debug("cache get failed for %s: %s", key, exc)
        return None


async def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    try:
        await get_redis().set(
            key, json.dumps(value, default=str), ex=ttl or settings.SEARCH_CACHE_TTL
        )
    except Exception as exc:
        logger.debug("cache set failed for %s: %s", key, exc)


async def cache_invalidate_prefix(prefix: str) -> int:
    try:
        r = get_redis()
        removed = 0
        async for key in r.scan_iter(match=f"{prefix}*", count=500):
            removed += await r.delete(key)
        return removed
    except Exception as exc:
        logger.debug("cache invalidate failed for %s: %s", prefix, exc)
        return 0


async def incr_metric(name: str, amount: int = 1) -> int:
    try:
        return int(await get_redis().incrby(f"metric:{name}", amount))
    except Exception:
        return 0
