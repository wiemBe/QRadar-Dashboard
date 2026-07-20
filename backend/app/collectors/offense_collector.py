"""Offense collection.

Guarantees, and why each one is needed:

  * **Incremental** — collection resumes from a per-instance watermark keyed on
    QRadar's own `last_persisted_time`, so a run costs a page or two rather than
    a full offense crawl.
  * **Bounded** — the first run and any gap-fill reach back at most
    `offense_max_backfill_hours`, and at most `offense_max_pages` pages are
    walked per run. A fresh install must never try to ingest years of history.
  * **No overlapping runs** — a PostgreSQL advisory lock keyed on (instance,
    collector) is held for the duration; a second worker skips rather than
    queues. Two collectors writing the same offense concurrently would race on
    the snapshot key and double-count the watermark.
  * **Idempotent** — snapshots are upserted on their natural key, so replaying an
    interval overwrites rather than duplicates.
  * **Change-driven** — a snapshot row is written only when the meaningful fields
    change, detected via a content hash. Polling a stable offense every five
    minutes would otherwise write 288 identical rows a day and make the history
    view useless.
  * **History preserving** — a changed offense appends a new snapshot; it never
    overwrites the previous one. Offense aging and assignment latency are
    questions about history.
  * **UTC everywhere** — QRadar epoch milliseconds are converted at the provider
    boundary and never re-interpreted here.

Explicitly NOT done: no offense is closed, assigned or otherwise mutated, and
raw contributing events are never fetched. Both are out of scope for Phase 3 and
the provider interface has no method for either.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.providers.base import ProviderError, QRadarProvider
from app.providers.dto import OffenseDTO
from app.security.sanitizer import sanitize_text
from app.services.locks import CollectorAdvisoryLock

logger = logging.getLogger("app.collectors.offense")

COLLECTOR_NAME = "offense_snapshot"

#: Fields whose change makes an offense meaningfully different. Deliberately
#: excludes capture time and any counter QRadar bumps continuously without an
#: analyst-visible state change.
_HASHED_FIELDS = (
    "description",
    "status",
    "magnitude",
    "severity",
    "credibility",
    "relevance",
    "assigned_to",
    "offense_type",
    "offense_source",
    "source_network",
    "event_count",
    "flow_count",
    "device_count",
    "source_count",
    "destination_count",
    "close_time",
    "closing_reason_id",
    "categories",
    "source_addresses",
    "local_destination_addresses",
    "usernames",
    "log_source_ids",
    "rule_ids",
)


@dataclass
class OffenseCollectionReport:
    instance_id: uuid.UUID
    offenses_seen: int = 0
    snapshots_written: int = 0
    unchanged: int = 0
    skipped_locked: bool = False
    partial_failure: bool = False
    error: str | None = None
    watermark_at: datetime | None = None
    lag_seconds: int | None = None
    duration_ms: int = 0
    dropped: list[int] = field(default_factory=list)


def offense_content_hash(dto: OffenseDTO) -> str:
    """Stable hash over the meaningful fields of an offense.

    JSON with sorted keys so the digest does not depend on field ordering, and
    datetimes as epoch seconds so a re-parse cannot perturb it.
    """
    payload: dict[str, object] = {}
    for name in _HASHED_FIELDS:
        value = getattr(dto, name, None)
        if isinstance(value, datetime):
            value = int(value.timestamp())
        payload[name] = value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


class OffenseCollector:
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

    async def collect(self, instance: QRadarInstance) -> OffenseCollectionReport:
        started = self._clock()
        report = OffenseCollectionReport(instance_id=instance.id)

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                # Another worker owns this instance. Skipping is correct: the
                # next tick will pick up whatever this run would have.
                report.skipped_locked = True
                logger.info(
                    "offense collection skipped; another worker holds the lock",
                    extra={"instance_id": str(instance.id)},
                )
                return report

            watermark = await self._get_or_create_watermark(instance)
            since = self._resume_point(watermark, started)

            try:
                offenses = await self.provider.list_offenses(
                    open_only=False,
                    updated_since=since,
                    max_pages=self.settings.offense_max_pages,
                )
            except ProviderError as exc:
                # Record the failure against the watermark and leave it
                # unadvanced, so the next run retries the same span rather than
                # skipping over offenses we never saw.
                watermark.consecutive_failures += 1
                watermark.last_run_at = started
                watermark.last_error = str(exc)[:512]
                await self.session.flush()
                report.partial_failure = True
                report.error = str(exc)[:512]
                logger.warning(
                    "offense collection failed",
                    extra={"instance_id": str(instance.id), "error_class": type(exc).__name__},
                )
                return report

            report.offenses_seen = len(offenses)
            high_water = since

            for dto in offenses:
                try:
                    written = await self._store(instance, dto, captured_at=started)
                except (ValueError, TypeError):
                    # One malformed offense must not abort the batch; record it
                    # and keep going, but mark the run partial so the operator
                    # knows this cycle was incomplete.
                    report.partial_failure = True
                    report.dropped.append(dto.qradar_id)
                    logger.warning(
                        "dropping offense that could not be stored",
                        extra={"qradar_offense_id": dto.qradar_id},
                    )
                    continue

                if written:
                    report.snapshots_written += 1
                else:
                    report.unchanged += 1

                if dto.last_updated_time is not None and (
                    high_water is None or dto.last_updated_time > high_water
                ):
                    high_water = dto.last_updated_time

            # Only advance the watermark on a clean run. Advancing past a span
            # we failed to fully read would silently lose those offenses.
            if not report.partial_failure and high_water is not None:
                watermark.watermark_at = high_water
                watermark.lag_seconds = max(
                    0, int((started - high_water).total_seconds())
                )
            watermark.last_run_at = started
            watermark.intervals_collected += 1
            if not report.partial_failure:
                watermark.consecutive_failures = 0
                watermark.last_error = None
            await self.session.flush()

            report.watermark_at = watermark.watermark_at
            report.lag_seconds = watermark.lag_seconds

        report.duration_ms = int((self._clock() - started).total_seconds() * 1000)
        logger.info(
            "offense collection complete",
            extra={
                "instance_id": str(instance.id),
                "offenses_seen": report.offenses_seen,
                "snapshots_written": report.snapshots_written,
                "unchanged": report.unchanged,
                "duration_ms": report.duration_ms,
                "lag_seconds": report.lag_seconds,
                "partial_failure": report.partial_failure,
            },
        )
        return report

    def _resume_point(
        self, watermark: CollectionWatermark, now: datetime
    ) -> datetime | None:
        """Where to resume, clamped by the maximum backfill.

        A watermark older than the backfill limit is treated as the limit: a
        collector that has been down for a month must not attempt a month-long
        catch-up in one run.
        """
        floor = now - timedelta(hours=self.settings.offense_max_backfill_hours)
        if watermark.watermark_at is None:
            return floor
        wm = watermark.watermark_at
        if wm.tzinfo is None:
            wm = wm.replace(tzinfo=UTC)
        return max(wm, floor)

    async def _store(
        self, instance: QRadarInstance, dto: OffenseDTO, *, captured_at: datetime
    ) -> bool:
        """Append a snapshot if the offense changed. Returns True if written."""
        content_hash = offense_content_hash(dto)
        latest = await self.session.scalar(
            select(OffenseSnapshot.content_hash)
            .where(
                OffenseSnapshot.instance_id == instance.id,
                OffenseSnapshot.qradar_offense_id == dto.qradar_id,
            )
            .order_by(OffenseSnapshot.captured_at.desc())
            .limit(1)
        )
        if latest == content_hash:
            return False

        age_seconds: int | None = None
        if dto.start_time is not None:
            end = dto.close_time or captured_at
            age_seconds = max(0, int((end - dto.start_time).total_seconds()))

        values = {
            "instance_id": instance.id,
            "qradar_offense_id": dto.qradar_id,
            "captured_at": captured_at,
            # QRadar text is attacker-influenced; sanitize on the way in so no
            # consumer can forget to do it on the way out.
            "description": sanitize_text(dto.description),
            "status": dto.status,
            "magnitude": dto.magnitude,
            "severity": dto.severity,
            "credibility": dto.credibility,
            "relevance": dto.relevance,
            "assigned_to": sanitize_text(dto.assigned_to),
            "is_assigned": bool(dto.assigned_to),
            "offense_type": dto.offense_type,
            "offense_type_name": sanitize_text(dto.offense_type_name),
            "offense_source": sanitize_text(dto.offense_source),
            "source_network": sanitize_text(dto.source_network),
            "event_count": dto.event_count,
            "flow_count": dto.flow_count,
            "device_count": dto.device_count,
            "source_count": dto.source_count,
            "destination_count": dto.destination_count,
            "start_time": dto.start_time,
            "last_updated_time": dto.last_updated_time,
            "close_time": dto.close_time,
            "age_seconds": age_seconds,
            "closing_reason_id": dto.closing_reason_id,
            "closing_reason": sanitize_text(dto.closing_reason),
            "categories": [sanitize_text(c) for c in dto.categories],
            "source_addresses": dto.source_addresses,
            "local_destination_addresses": dto.local_destination_addresses,
            "usernames": [sanitize_text(u) for u in dto.usernames],
            "log_source_ids": dto.log_source_ids,
            "rule_ids": dto.rule_ids,
            "content_hash": content_hash,
        }

        # Idempotent on the natural key: re-running the same capture instant
        # overwrites rather than duplicating.
        stmt = pg_insert(OffenseSnapshot).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instance_id", "qradar_offense_id", "captured_at"],
            set_={k: v for k, v in values.items()
                  if k not in ("instance_id", "qradar_offense_id", "captured_at")},
        )
        await self.session.execute(stmt)
        return True

    async def _get_or_create_watermark(
        self, instance: QRadarInstance
    ) -> CollectionWatermark:
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
