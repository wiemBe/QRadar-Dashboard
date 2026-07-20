"""Log-source inventory collection.

`InventorySyncService` already performs the upsert and is used by the seed path
and the API. This collector wraps it in the operational envelope the scheduled
collectors share: an advisory lock so two workers cannot sync one console
concurrently, a watermark carrying run time and failure count, and a report the
task layer can log.

Unlike the offense collector there is no incremental window — QRadar's log
source endpoint has no usable "changed since" filter, so this is a full
inventory pass every run. The watermark therefore records *when we last
succeeded*, not a resume point; the sync is idempotent by (instance, qradar_id)
so a repeated full pass converges rather than duplicating.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderError, QRadarProvider
from app.services.inventory_sync import InventorySyncService
from app.services.locks import CollectorAdvisoryLock

logger = logging.getLogger("app.collectors.log_source")

COLLECTOR_NAME = "log_source_inventory"


@dataclass
class LogSourceSyncReport:
    instance_id: uuid.UUID
    log_sources_seen: int = 0
    created: int = 0
    updated: int = 0
    skipped_locked: bool = False
    partial_failure: bool = False
    error: str | None = None
    duration_ms: int = 0


class LogSourceCollector:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def sync(self, instance: QRadarInstance) -> LogSourceSyncReport:
        started = self._clock()
        report = LogSourceSyncReport(instance_id=instance.id)

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                report.skipped_locked = True
                return report

            watermark = await self._get_or_create_watermark(instance)

            try:
                result = await InventorySyncService(self.session, self.provider).sync(instance)
            except ProviderError as exc:
                # The watermark is deliberately not advanced: a failed pass must
                # not read downstream as a completed observation.
                watermark.consecutive_failures += 1
                watermark.last_run_at = started
                watermark.last_error = str(exc)[:512]
                await self.session.flush()
                report.partial_failure = True
                report.error = str(exc)[:512]
                logger.warning(
                    "log source inventory sync failed",
                    extra={
                        "instance_id": str(instance.id),
                        "error_class": type(exc).__name__,
                    },
                )
                return report

            report.log_sources_seen = result.log_sources_seen
            report.created = result.created
            report.updated = result.updated

            watermark.last_run_at = started
            watermark.watermark_at = started
            watermark.intervals_collected += 1
            watermark.consecutive_failures = 0
            watermark.last_error = None
            watermark.lag_seconds = 0
            await self.session.flush()

        report.duration_ms = int((self._clock() - started).total_seconds() * 1000)
        logger.info(
            "log source inventory sync complete",
            extra={
                "instance_id": str(instance.id),
                "log_sources_seen": report.log_sources_seen,
                # Not "created"/"updated": LogRecord reserves `created` for its
                # own timestamp, and logging raises KeyError rather than
                # shadowing it. That only fires when the logger is actually
                # enabled at INFO, so it hides from tests that leave logging at
                # default level and shows up in production.
                "rows_created": report.created,
                "rows_updated": report.updated,
                "duration_ms": report.duration_ms,
            },
        )
        return report

    async def _get_or_create_watermark(self, instance: QRadarInstance) -> CollectionWatermark:
        watermark = await self.session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector == COLLECTOR_NAME,
            )
        )
        if watermark is None:
            watermark = CollectionWatermark(
                instance_id=instance.id, collector=COLLECTOR_NAME
            )
            self.session.add(watermark)
            await self.session.flush()
        return watermark
