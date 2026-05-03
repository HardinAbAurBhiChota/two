"""
Cache with in-memory TTL dict.
Set REDIS_URL env var to use Redis; omit to use in-memory.
"""
import hashlib
import json
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "600"))


class InMemoryCache:
    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}

    async def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if not entry:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int = CACHE_TTL):
        self._store[key] = (value, time.time() + ttl)

    async def connect(self): pass
    async def disconnect(self): pass


class RedisCache:
    def __init__(self, url: str):
        self._url = url
        self._client = None

    async def connect(self):
        import redis.asyncio as aioredis
        self._client = await aioredis.from_url(self._url, encoding="utf-8", decode_responses=True)
        logger.info("Redis connected.")

    async def disconnect(self):
        if self._client:
            await self._client.aclose()

    async def get(self, key: str) -> Optional[Any]:
        if not self._client:
            return None
        raw = await self._client.get(key)
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: Any, ttl: int = CACHE_TTL):
        if self._client:
            await self._client.set(key, json.dumps(value), ex=ttl)


class CacheService:
    def __init__(self):
        redis_url = os.getenv("REDIS_URL")
        self._backend = RedisCache(redis_url) if redis_url else InMemoryCache()
        logger.info(f"Cache backend: {'Redis' if redis_url else 'InMemory'}")

    async def connect(self): await self._backend.connect()
    async def disconnect(self): await self._backend.disconnect()

    def make_key(self, params: dict) -> str:
        serialized = json.dumps(params, sort_keys=True)
        return "hotels:" + hashlib.sha256(serialized.encode()).hexdigest()

    async def get(self, key: str) -> Optional[Any]:
        return await self._backend.get(key)

    async def set(self, key: str, value: Any, ttl: int = CACHE_TTL):
        await self._backend.set(key, value, ttl)


cache_service = CacheService()
