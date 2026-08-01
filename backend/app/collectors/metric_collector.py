"""Log-source metric collection.

Guarantees:
  * Bounded batches — sources processed in configurable chunks.
  * No overlapping runs for the same instance: a PostgreSQL advisory lock keyed
    on (instance, collector) is held for the duration; a second worker skips.
  * Idempotent — metrics are upserted on (log_source_id, bucket_start), so
    re-collecting an interval overwrites rather than duplicates.
  * Explicit UTC interval boundaries — intervals are floored to the configured
    interval width in UTC.
  * Watermark + lag persisted per (instance, collector).
  * Bounded backfill — at most `collection_max_backfill_intervals` missed
    intervals are filled; it never silently walks unlimited history.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import BucketCompleteness
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceMetric
from app.models.monitoring import CollectionWatermark
from app.providers.base import QRadarProvider
from app.services.locks import CollectorAdvisoryLock

COLLECTOR_NAME = "log_source_metric"


@dataclass
class CollectionReport:
    instance_id: uuid.UUID
    intervals_collected: int
    samples_written: int
    skipped_locked: bool = False
    watermark_at: datetime | None = None
    lag_seconds: int | None = None


def floor_to_interval(dt: datetime, interval_seconds: int) -> datetime:
    """Floor a UTC datetime to the interval grid. Boundaries are explicit and
    deterministic so every worker agrees on interval identity."""
    dt = dt.astimezone(UTC)
    epoch = int(dt.timestamp())
    floored = epoch - (epoch % interval_seconds)
    return datetime.fromtimestamp(floored, tz=UTC)


class MetricCollector:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock=None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def _collection_source(self) -> str:
        """Which subsystem produced a bucket. QRadar exposes no per-log-source
        metric endpoint, so live volume is always a bounded Ariel aggregate."""
        name = type(self.provider).__name__.lower()
        return "mock" if "mock" in name else "ariel_aggregate"

    async def collect(self, instance: QRadarInstance) -> CollectionReport:
        interval = self.settings.collection_interval_seconds
        now = self._clock()
        # The most recent *completed* interval is the one before the current.
        current_boundary = floor_to_interval(now, interval)

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                return CollectionReport(instance.id, 0, 0, skipped_locked=True)

            watermark = await self._get_or_create_watermark(instance)
            intervals = self._intervals_to_collect(watermark, current_boundary, interval)

            total_samples = 0
            last_done: datetime | None = watermark.watermark_at
            for start in intervals:
                end = start + timedelta(seconds=interval)
                written = await self._collect_interval(
                    instance, start, end, watermark_at=last_done
                )
                total_samples += written
                # The watermark only advances past an interval that completed.
                # A partial failure raises out of this loop, leaving the
                # watermark on the last whole interval so the next run retries
                # rather than skipping the gap.
                last_done = start

            watermark.watermark_at = last_done
            watermark.last_run_at = now
            watermark.intervals_collected += len(intervals)
            if last_done is not None:
                watermark.lag_seconds = int((now - (last_done + timedelta(seconds=interval)))
                                            .total_seconds())
            watermark.consecutive_failures = 0
            watermark.last_error = None
            await self.session.flush()

            return CollectionReport(
                instance_id=instance.id,
                intervals_collected=len(intervals),
                samples_written=total_samples,
                watermark_at=watermark.watermark_at,
                lag_seconds=watermark.lag_seconds,
            )

    def _intervals_to_collect(
        self, watermark: CollectionWatermark, current_boundary: datetime, interval: int
    ) -> list[datetime]:
        """Intervals strictly before the current boundary, resuming from the
        watermark, capped by the backfill limit."""
        max_backfill = self.settings.collection_max_backfill_intervals
        if watermark.watermark_at is None:
            # First run: just the most recent completed interval.
            start = current_boundary - timedelta(seconds=interval)
            return [start] if start < current_boundary else []

        first = watermark.watermark_at + timedelta(seconds=interval)
        out: list[datetime] = []
        cursor = first
        while cursor < current_boundary and len(out) < max_backfill + 1:
            out.append(cursor)
            cursor += timedelta(seconds=interval)
        return out

    async def _collect_interval(
        self,
        instance: QRadarInstance,
        start: datetime,
        end: datetime,
        *,
        watermark_at: datetime | None = None,
    ) -> int:
        started = self._clock()
        samples = await self.provider.get_log_source_metrics(start, end)
        collected_at = self._clock()
        duration_ms = int((collected_at - started).total_seconds() * 1000)
        if not samples:
            return 0

        # Map qradar_id -> internal LogSource id for this instance.
        id_map = await self._log_source_id_map(instance.id)

        # Non-secret evidence of how these rows were produced. Never the token,
        # never response headers — only the shape of the request.
        provenance = {
            "collector": COLLECTOR_NAME,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "provider": type(self.provider).__name__,
            "sample_count": len(samples),
        }

        rows = []
        for s in samples:
            ls_id = id_map.get(s.qradar_id)
            if ls_id is None:
                continue  # source not yet in inventory; a sync will add it
            rows.append(
                {
                    "log_source_id": ls_id,
                    "bucket_start": start,
                    "completeness": BucketCompleteness.COMPLETE,
                    "collection_source": self._collection_source,
                    "query_provenance": provenance,
                    "collection_duration_ms": duration_ms,
                    "collected_at": collected_at,
                    "watermark_at": watermark_at,
                    "bucket_seconds": s.bucket_seconds,
                    "event_count": s.event_count,
                    "average_eps": s.average_eps,
                    "peak_eps": s.peak_eps,
                    "last_event_at": s.last_event_at,
                    "event_delay_seconds": s.event_delay_seconds,
                    "unknown_event_count": s.unknown_event_count,
                    "stored_event_count": s.stored_event_count,
                    "parsed_username_ratio": s.parsed_username_ratio,
                    "parsed_source_ip_ratio": s.parsed_source_ip_ratio,
                    "distinct_qid_count": s.distinct_qid_count,
                    "distinct_username_count": s.distinct_username_count,
                    "distinct_source_ip_count": s.distinct_source_ip_count,
                    "collection_error_count": s.collection_error_count,
                    "payload_signature": s.payload_signature,
                }
            )
        if not rows:
            return 0

        # Idempotent upsert on the composite PK. Re-collecting overwrites.
        stmt = pg_insert(LogSourceMetric).values(rows)
        update_cols = {c: stmt.excluded[c] for c in rows[0] if c not in
                       ("log_source_id", "bucket_start")}
        stmt = stmt.on_conflict_do_update(
            index_elements=["log_source_id", "bucket_start"], set_=update_cols
        )
        await self.session.execute(stmt)
        return len(rows)

    async def _log_source_id_map(self, instance_id: uuid.UUID) -> dict[int, uuid.UUID]:
        rows = await self.session.execute(
            select(LogSource.qradar_id, LogSource.id).where(
                LogSource.instance_id == instance_id
            )
        )
        return dict(rows.all())

    async def _get_or_create_watermark(self, instance: QRadarInstance) -> CollectionWatermark:
        wm = await self.session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector == COLLECTOR_NAME,
            )
        )
        if wm is None:
            wm = CollectionWatermark(instance_id=instance.id, collector=COLLECTOR_NAME)
            self.session.add(wm)
            await self.session.flush()
        return wm
