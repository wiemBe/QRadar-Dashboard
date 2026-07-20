"""PostgreSQL advisory locks for background collection.

Every collector that writes per-instance state takes one of these for the
duration of a run. The lock is *non-blocking*: a second worker that finds the
lock held skips the run instead of queueing behind it. Queueing would be worse
than skipping — the work is periodic, so a delayed run just does the next tick's
work twice, while a queue of blocked workers ties up the pool.

The lock key combines the QRadar instance with the collector name, so the
offense collector and the metric collector never contend with each other: they
touch different tables and have no reason to serialize.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings


def collector_lock_key(instance_id: uuid.UUID, collector: str = "") -> int:
    """Stable 31-bit advisory-lock key for one (instance, collector) pair.

    `collector=""` reproduces the historical instance-only key, so an existing
    deployment's held locks keep their meaning across this change.
    """
    key = instance_id.int & 0x7FFFFFFF
    if collector:
        digest = hashlib.sha256(collector.encode()).digest()[:4]
        key ^= int.from_bytes(digest, "big") & 0x7FFFFFFF
    return key & 0x7FFFFFFF


class CollectorAdvisoryLock:
    """Session-level advisory lock scoped to (instance, collector)."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        instance_id: uuid.UUID,
        collector: str = "",
    ) -> None:
        self.session = session
        self._namespace = settings.collection_advisory_lock_namespace
        self._key = collector_lock_key(instance_id, collector)
        self._acquired = False

    def _is_postgres(self) -> bool:
        bind = self.session.bind
        # SQLite (unit tests) has no advisory locks; treat as always-acquired.
        return bind is not None and bind.dialect.name == "postgresql"

    async def __aenter__(self) -> bool:
        if not self._is_postgres():
            self._acquired = True
            return True
        result = await self.session.execute(
            text("SELECT pg_try_advisory_lock(:ns, :key)").bindparams(
                ns=self._namespace, key=self._key
            )
        )
        self._acquired = bool(result.scalar())
        return self._acquired

    async def __aexit__(self, *exc: object) -> None:
        if not self._acquired:
            return
        if self._is_postgres():
            await self.session.execute(
                text("SELECT pg_advisory_unlock(:ns, :key)").bindparams(
                    ns=self._namespace, key=self._key
                )
            )
