"""Concurrency limiting for Ariel searches.

Two caps are enforced simultaneously: per-QRadar-instance and global. QRadar
Ariel searches are expensive and the appliance has finite capacity, so we must
never dispatch more than configured in flight.

`ConcurrencyLimiter` is an interface. Production uses the Redis implementation
(atomic and shared across workers); tests use the in-memory implementation. Both
hand out a context manager that releases the slot on exit, including on error.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class ConcurrencyLimitReached(RuntimeError):
    """Raised when a slot cannot be acquired within the allowed wait."""


class ConcurrencyLimiter(ABC):
    @abstractmethod
    @asynccontextmanager
    async def slot(self, instance_key: str) -> AsyncIterator[None]:
        """Acquire one per-instance and one global slot, releasing both on exit."""
        yield  # pragma: no cover


class InMemoryConcurrencyLimiter(ConcurrencyLimiter):
    """Single-process limiter for tests and the APScheduler MVP fallback."""

    def __init__(self, per_instance: int, global_limit: int) -> None:
        self._per_instance_limit = per_instance
        self._global = asyncio.Semaphore(global_limit)
        self._per_instance: dict[str, asyncio.Semaphore] = {}

    def _instance_sem(self, key: str) -> asyncio.Semaphore:
        if key not in self._per_instance:
            self._per_instance[key] = asyncio.Semaphore(self._per_instance_limit)
        return self._per_instance[key]

    @asynccontextmanager
    async def slot(self, instance_key: str) -> AsyncIterator[None]:
        inst = self._instance_sem(instance_key)
        # Acquire global first, then per-instance, to avoid deadlock via a
        # consistent ordering.
        await self._global.acquire()
        try:
            await inst.acquire()
            try:
                yield
            finally:
                inst.release()
        finally:
            self._global.release()

    def try_slot(self, instance_key: str) -> bool:
        """Non-blocking probe used by tests to assert saturation."""
        inst = self._instance_sem(instance_key)
        if self._global.locked() or inst.locked():
            return False
        return not (inst._value == 0 or self._global._value == 0)


class RedisConcurrencyLimiter(ConcurrencyLimiter):
    """Cross-worker limiter backed by Redis counters.

    Uses INCR/DECR with a TTL guard so a crashed worker's slot is eventually
    reclaimed. Implemented for production; the executor is limiter-agnostic.
    """

    def __init__(
        self, redis, per_instance: int, global_limit: int, ttl_seconds: int = 1800
    ) -> None:
        self._redis = redis
        self._per_instance = per_instance
        self._global = global_limit
        self._ttl = ttl_seconds

    @asynccontextmanager
    async def slot(self, instance_key: str) -> AsyncIterator[None]:
        gkey = "ariel:concurrency:global"
        ikey = f"ariel:concurrency:instance:{instance_key}"
        g = await self._redis.incr(gkey)
        await self._redis.expire(gkey, self._ttl)
        try:
            if g > self._global:
                raise ConcurrencyLimitReached("global Ariel concurrency limit reached")
            i = await self._redis.incr(ikey)
            await self._redis.expire(ikey, self._ttl)
            try:
                if i > self._per_instance:
                    raise ConcurrencyLimitReached(
                        f"per-instance Ariel concurrency limit reached for {instance_key}"
                    )
                yield
            finally:
                await self._redis.decr(ikey)
        finally:
            await self._redis.decr(gkey)
