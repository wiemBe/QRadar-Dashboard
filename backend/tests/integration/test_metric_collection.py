"""Metric collection: idempotency, watermark, bounded backfill, advisory lock.
DB-gated."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select, text

from app.collectors.metric_collector import (
    COLLECTOR_NAME,
    MetricCollector,
    floor_to_interval,
)
from app.core.config import Settings
from app.models.log_source import LogSourceMetric
from app.providers.mock import MockQRadarProvider
from app.services.inventory_sync import InventorySyncService
from app.services.locks import collector_lock_key

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings(**kw) -> Settings:
    base = dict(encryption_key="x" * 44, collection_interval_seconds=300,
                collection_max_backfill_intervals=3)
    base.update(kw)
    return Settings(**base)


async def _seed_inventory(session):
    provider = MockQRadarProvider(seed=1337)
    svc = InventorySyncService(session, provider)
    instance = await svc.ensure_default_instance()
    await svc.sync(instance)
    return instance


@pytest.mark.asyncio(loop_scope="function")
async def test_floor_to_interval_is_utc_and_deterministic() -> None:
    # Made async only to satisfy the module-level asyncio mark cleanly.
    dt = datetime(2026, 7, 15, 10, 7, 42, tzinfo=UTC)
    floored = floor_to_interval(dt, 300)
    assert floored == datetime(2026, 7, 15, 10, 5, 0, tzinfo=UTC)


async def test_first_run_collects_one_interval(db_session) -> None:
    instance = await _seed_inventory(db_session)
    now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    collector = MetricCollector(db_session, MockQRadarProvider(seed=1337),
                                settings=_settings(), clock=lambda: now)
    report = await collector.collect(instance)
    assert report.intervals_collected == 1
    assert report.samples_written > 0
    assert report.watermark_at is not None


async def test_collection_is_idempotent(db_session) -> None:
    instance = await _seed_inventory(db_session)
    now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    collector = MetricCollector(db_session, MockQRadarProvider(seed=1337),
                                settings=_settings(), clock=lambda: now)
    await collector.collect(instance)
    count1 = await db_session.scalar(select(func.count()).select_from(LogSourceMetric))
    # Re-running at the same clock collects nothing new (watermark unchanged).
    await collector.collect(instance)
    count2 = await db_session.scalar(select(func.count()).select_from(LogSourceMetric))
    assert count1 == count2


async def test_bounded_backfill(db_session) -> None:
    instance = await _seed_inventory(db_session)
    # First run establishes a watermark well in the past.
    t0 = datetime(2026, 7, 15, 8, 3, tzinfo=UTC)
    collector0 = MetricCollector(db_session, MockQRadarProvider(seed=1337),
                                 settings=_settings(), clock=lambda: t0)
    await collector0.collect(instance)

    # Now jump 2 hours ahead: 24 intervals missed, but backfill is capped at 3+1.
    t1 = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    collector1 = MetricCollector(db_session, MockQRadarProvider(seed=1337),
                                 settings=_settings(collection_max_backfill_intervals=3),
                                 clock=lambda: t1)
    report = await collector1.collect(instance)
    assert report.intervals_collected <= 4  # never unbounded


async def test_advisory_lock_prevents_overlap(db_session) -> None:
    instance = await _seed_inventory(db_session)
    settings = _settings()
    # Derive the key the same way the collector does; it is per
    # (instance, collector) so collectors do not contend with each other.
    key = collector_lock_key(instance.id, COLLECTOR_NAME)

    url = os.environ["TEST_DATABASE_URL"].replace("+asyncpg", "+psycopg")
    holder = create_engine(url, future=True)
    conn = holder.connect()
    conn.execute(
        text("SELECT pg_advisory_lock(:ns, :key)").bindparams(
            ns=settings.collection_advisory_lock_namespace, key=key
        )
    )
    try:
        now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
        collector = MetricCollector(db_session, MockQRadarProvider(seed=1337),
                                    settings=settings, clock=lambda: now)
        report = await collector.collect(instance)
        assert report.skipped_locked is True  # another holder -> skip, don't block
    finally:
        conn.execute(
            text("SELECT pg_advisory_unlock(:ns, :key)").bindparams(
                ns=settings.collection_advisory_lock_namespace, key=key
            )
        )
        conn.close()
        holder.dispose()


class TestZeroFill:
    """A silent source must leave an explicit zero bucket, not nothing at all.

    QRadar omits a source with no events from the `GROUP BY logsourceid`
    aggregate. Without an explicit zero row the interval leaves no trace, and
    the anomaly engine -- which evaluates the newest row it can find -- would
    re-judge the last busy bucket forever and never observe the silence. That
    made live NO_EVENTS unreachable: `detect_no_events` requires a COMPLETE
    bucket whose count is zero.
    """

    @staticmethod
    async def _collect_with(session, instance, samples, now):
        provider = MockQRadarProvider(seed=1337)

        async def _metrics(window_start, window_end):
            return samples

        provider.get_log_source_metrics = _metrics  # type: ignore[method-assign]
        collector = MetricCollector(
            session, provider, settings=_settings(), clock=lambda: now
        )
        return await collector.collect(instance)

    async def test_monitored_source_absent_from_ariel_gets_a_zero_bucket(
        self, db_session
    ) -> None:
        instance = await _seed_inventory(db_session)
        now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)

        # QRadar reports nothing at all for this window: every monitored source
        # was silent.
        await self._collect_with(db_session, instance, [], now)

        rows = list(
            (await db_session.scalars(select(LogSourceMetric))).all()
        )
        assert rows, "a silent interval must still leave evidence it was observed"
        for row in rows:
            assert row.event_count == 0
            assert row.average_eps == 0.0
            # COMPLETE because the query succeeded and returned no events -- an
            # observation of silence, not an unobserved window.
            assert row.is_complete
            assert row.query_provenance.get("zero_filled") is True

    async def test_zero_rows_are_upserted_not_duplicated(self, db_session) -> None:
        instance = await _seed_inventory(db_session)
        now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)

        await self._collect_with(db_session, instance, [], now)
        first = await db_session.scalar(select(func.count()).select_from(LogSourceMetric))
        # Re-collecting the same interval overwrites rather than duplicating.
        await self._collect_with(db_session, instance, [], now)
        second = await db_session.scalar(select(func.count()).select_from(LogSourceMetric))
        assert first == second

    async def test_unmonitored_sources_are_not_zero_filled(self, db_session) -> None:
        instance = await _seed_inventory(db_session)
        now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)

        from app.models.log_source import LogSource

        sources = list((await db_session.scalars(select(LogSource))).all())
        for src in sources:
            src.monitoring_enabled = False
        await db_session.flush()

        await self._collect_with(db_session, instance, [], now)

        count = await db_session.scalar(select(func.count()).select_from(LogSourceMetric))
        assert count == 0, "an unmonitored source is never evaluated, so it needs no row"
