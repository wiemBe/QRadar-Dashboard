"""Offense collection against a real PostgreSQL/TimescaleDB.

These exercise the properties that only a real database can demonstrate:
snapshot history accumulating on the natural key, watermark advancement (and
deliberate non-advancement), per-instance isolation, and the advisory lock that
keeps two workers from collecting the same instance at once.

The provider is a stub that returns scripted DTOs and can be told to fail, so
QRadar behaviour is controlled precisely; the database is not mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.collectors.offense_collector import COLLECTOR_NAME, OffenseCollector
from app.core.config import get_settings
from app.models.monitoring import CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.providers.base import (
    ProviderCapability,
    ProviderError,
    ProviderUnavailableError,
    QRadarProvider,
)
from app.providers.dto import InstanceInfoDTO, OffenseDTO
from tests.integration.factories import make_instance

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


class StubProvider(QRadarProvider):
    """Returns scripted offenses; records how it was called.

    The inventory methods are inert: this stub exists to drive offense
    collection, and a collector reaching for anything else would be a change in
    behaviour worth failing on rather than silently satisfying.
    """

    capabilities = frozenset({ProviderCapability.OFFENSES})

    def __init__(self, pages: list[list[OffenseDTO]] | None = None, error=None) -> None:
        self._pages = pages if pages is not None else [[]]
        self._error = error
        self.calls: list[dict] = []

    async def get_instance_info(self) -> InstanceInfoDTO:
        return InstanceInfoDTO(version="7.5.0", reachable=True)

    async def list_log_sources(self) -> list:
        raise AssertionError("offense collection must not enumerate log sources")

    async def get_log_source(self, qradar_id: int):
        raise AssertionError("offense collection must not fetch log sources")

    async def list_log_source_types(self) -> list:
        raise AssertionError("offense collection must not enumerate log source types")

    async def list_rules(self) -> list:
        raise AssertionError("offense collection must not enumerate rules")

    async def list_offenses(self, *, open_only=True, updated_since=None, max_pages=None):
        self.calls.append(
            {"open_only": open_only, "updated_since": updated_since,
             "max_pages": max_pages}
        )
        if self._error is not None:
            raise self._error
        return self._pages.pop(0) if self._pages else []


def offense(qradar_id: int = 4242, **overrides) -> OffenseDTO:
    base = dict(
        qradar_id=qradar_id,
        description="Multiple failed logins",
        status="OPEN",
        magnitude=7,
        severity=6,
        assigned_to=None,
        offense_type=3,
        event_count=120,
        start_time=NOW - timedelta(hours=4),
        last_updated_time=NOW - timedelta(minutes=5),
        categories=["Authentication"],
        source_addresses=["10.1.2.3"],
        usernames=["svc_backup"],
        rule_ids=[500],
    )
    base.update(overrides)
    return OffenseDTO(**base)


def collector(session, provider, *, clock=None, **cfg) -> OffenseCollector:
    return OffenseCollector(
        session,
        provider,
        settings=get_settings().model_copy(update=cfg) if cfg else get_settings(),
        clock=clock or (lambda: NOW),
    )


async def snapshots(session, instance_id, qradar_offense_id: int | None = None):
    stmt = select(OffenseSnapshot).where(OffenseSnapshot.instance_id == instance_id)
    if qradar_offense_id is not None:
        stmt = stmt.where(OffenseSnapshot.qradar_offense_id == qradar_offense_id)
    return list((await session.scalars(stmt.order_by(OffenseSnapshot.captured_at))).all())


async def watermark(session, instance_id) -> CollectionWatermark | None:
    return await session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance_id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        )
    )


# ============================================================================
class TestSnapshotPersistence:
    @pytest.mark.asyncio
    async def test_first_collection_inserts_a_snapshot(self, db_session) -> None:
        inst = await make_instance(db_session)
        report = await collector(db_session, StubProvider([[offense()]])).collect(inst)

        rows = await snapshots(db_session, inst.id)
        assert len(rows) == 1
        assert report.snapshots_written == 1
        assert report.offenses_seen == 1
        assert rows[0].qradar_offense_id == 4242
        assert rows[0].status == "OPEN"
        assert rows[0].magnitude == 7
        assert rows[0].captured_at == NOW

    @pytest.mark.asyncio
    async def test_persisted_row_carries_the_normalized_fields(self, db_session) -> None:
        inst = await make_instance(db_session)
        await collector(db_session, StubProvider([[offense()]])).collect(inst)

        (row,) = await snapshots(db_session, inst.id)
        assert row.categories == ["Authentication"]
        assert row.source_addresses == ["10.1.2.3"]
        assert row.usernames == ["svc_backup"]
        assert row.rule_ids == [500]
        assert row.is_assigned is False
        # start_time 4h before capture, still open -> aged against capture time.
        assert row.age_seconds == 4 * 3600
        assert row.content_hash

    @pytest.mark.asyncio
    async def test_assignment_sets_the_is_assigned_flag(self, db_session) -> None:
        inst = await make_instance(db_session)
        await collector(
            db_session, StubProvider([[offense(assigned_to="alice")]])
        ).collect(inst)
        (row,) = await snapshots(db_session, inst.id)
        assert row.is_assigned is True
        assert row.assigned_to == "alice"

    @pytest.mark.asyncio
    async def test_closed_offense_ages_against_its_close_time(self, db_session) -> None:
        inst = await make_instance(db_session)
        closed = offense(
            status="CLOSED",
            start_time=NOW - timedelta(hours=10),
            close_time=NOW - timedelta(hours=8),
            closing_reason_id=1,
        )
        await collector(db_session, StubProvider([[closed]])).collect(inst)
        (row,) = await snapshots(db_session, inst.id)
        assert row.age_seconds == 2 * 3600

    @pytest.mark.asyncio
    async def test_unchanged_offense_writes_no_second_snapshot(self, db_session) -> None:
        """Polling a stable offense every 5 minutes must not write 288 rows a day."""
        inst = await make_instance(db_session)
        dto = offense()

        first = await collector(db_session, StubProvider([[dto]])).collect(inst)
        later = NOW + timedelta(minutes=5)
        second = await collector(
            db_session, StubProvider([[dto]]), clock=lambda: later
        ).collect(inst)

        assert first.snapshots_written == 1
        assert second.snapshots_written == 0
        assert second.unchanged == 1
        assert len(await snapshots(db_session, inst.id)) == 1

    @pytest.mark.asyncio
    async def test_changed_offense_appends_a_historical_snapshot(self, db_session) -> None:
        """History is append-only: the previous state must survive."""
        inst = await make_instance(db_session)
        await collector(db_session, StubProvider([[offense(magnitude=5)]])).collect(inst)

        later = NOW + timedelta(hours=1)
        await collector(
            db_session,
            StubProvider([[offense(magnitude=9, assigned_to="alice")]]),
            clock=lambda: later,
        ).collect(inst)

        rows = await snapshots(db_session, inst.id, 4242)
        assert len(rows) == 2
        assert [r.magnitude for r in rows] == [5, 9]
        assert [r.captured_at for r in rows] == [NOW, later]
        assert [r.is_assigned for r in rows] == [False, True]

    @pytest.mark.asyncio
    async def test_history_is_ordered_by_capture_time(self, db_session) -> None:
        inst = await make_instance(db_session)
        for i, magnitude in enumerate([3, 5, 8]):
            at = NOW + timedelta(hours=i)
            await collector(
                db_session, StubProvider([[offense(magnitude=magnitude)]]),
                clock=lambda at=at: at,
            ).collect(inst)

        rows = await snapshots(db_session, inst.id, 4242)
        assert [r.magnitude for r in rows] == [3, 5, 8]
        assert rows == sorted(rows, key=lambda r: r.captured_at)

    @pytest.mark.asyncio
    async def test_recollecting_the_same_instant_upserts_rather_than_duplicates(
        self, db_session
    ) -> None:
        """Replaying an interval must be idempotent on the natural key."""
        inst = await make_instance(db_session)
        await collector(db_session, StubProvider([[offense(magnitude=5)]])).collect(inst)
        # Same capture instant, different content: the row is overwritten.
        await collector(db_session, StubProvider([[offense(magnitude=9)]])).collect(inst)

        rows = await snapshots(db_session, inst.id, 4242)
        assert len(rows) == 1
        assert rows[0].magnitude == 9

    @pytest.mark.asyncio
    async def test_multiple_instances_are_isolated(self, db_session) -> None:
        """The same QRadar offense id on two consoles is two different offenses."""
        a = await make_instance(db_session)
        b = await make_instance(db_session)

        await collector(db_session, StubProvider([[offense(magnitude=3)]])).collect(a)
        await collector(db_session, StubProvider([[offense(magnitude=9)]])).collect(b)

        rows_a = await snapshots(db_session, a.id)
        rows_b = await snapshots(db_session, b.id)
        assert len(rows_a) == len(rows_b) == 1
        assert rows_a[0].magnitude == 3
        assert rows_b[0].magnitude == 9

    @pytest.mark.asyncio
    async def test_each_instance_keeps_its_own_watermark(self, db_session) -> None:
        a = await make_instance(db_session)
        b = await make_instance(db_session)

        await collector(db_session, StubProvider([[offense()]])).collect(a)
        assert await watermark(db_session, b.id) is None

        await collector(db_session, StubProvider([[]])).collect(b)
        assert (await watermark(db_session, a.id)).intervals_collected == 1
        assert (await watermark(db_session, b.id)).intervals_collected == 1

    @pytest.mark.asyncio
    async def test_sanitizer_runs_on_attacker_influenced_text(self, db_session) -> None:
        """Offense descriptions and usernames come from parsed events."""
        inst = await make_instance(db_session)
        hostile = offense(
            description="<script>alert(1)</script>",
            usernames=["<img src=x onerror=alert(1)>"],
        )
        await collector(db_session, StubProvider([[hostile]])).collect(inst)

        (row,) = await snapshots(db_session, inst.id)
        assert "<script>" not in (row.description or "")
        assert "onerror" not in row.usernames[0]


# ============================================================================
class TestWatermark:
    @pytest.mark.asyncio
    async def test_first_run_creates_a_watermark_and_advances_it(self, db_session) -> None:
        inst = await make_instance(db_session)
        last_seen = NOW - timedelta(minutes=2)
        await collector(
            db_session, StubProvider([[offense(last_updated_time=last_seen)]])
        ).collect(inst)

        wm = await watermark(db_session, inst.id)
        assert wm is not None
        assert wm.watermark_at == last_seen
        assert wm.intervals_collected == 1
        assert wm.consecutive_failures == 0
        assert wm.last_run_at == NOW

    @pytest.mark.asyncio
    async def test_watermark_takes_the_highest_last_updated_time(self, db_session) -> None:
        inst = await make_instance(db_session)
        newest = NOW - timedelta(minutes=1)
        page = [
            offense(1, last_updated_time=NOW - timedelta(hours=3)),
            offense(2, last_updated_time=newest),
            offense(3, last_updated_time=NOW - timedelta(hours=1)),
        ]
        await collector(db_session, StubProvider([page])).collect(inst)
        assert (await watermark(db_session, inst.id)).watermark_at == newest

    @pytest.mark.asyncio
    async def test_lag_is_measured_from_the_watermark(self, db_session) -> None:
        inst = await make_instance(db_session)
        await collector(
            db_session,
            StubProvider([[offense(last_updated_time=NOW - timedelta(minutes=30))]]),
        ).collect(inst)
        assert (await watermark(db_session, inst.id)).lag_seconds == 1800

    @pytest.mark.asyncio
    async def test_second_run_resumes_from_the_stored_watermark(self, db_session) -> None:
        inst = await make_instance(db_session)
        mark = NOW - timedelta(minutes=10)
        await collector(
            db_session, StubProvider([[offense(last_updated_time=mark)]])
        ).collect(inst)

        provider = StubProvider([[]])
        await collector(db_session, provider).collect(inst)
        assert provider.calls[0]["updated_since"] == mark

    @pytest.mark.asyncio
    async def test_first_run_is_bounded_by_the_backfill_limit(self, db_session) -> None:
        """A fresh install must not try to ingest years of offense history."""
        inst = await make_instance(db_session)
        provider = StubProvider([[]])
        await collector(db_session, provider, offense_max_backfill_hours=24).collect(inst)
        assert provider.calls[0]["updated_since"] == NOW - timedelta(hours=24)

    @pytest.mark.asyncio
    async def test_page_bound_is_passed_to_the_provider(self, db_session) -> None:
        inst = await make_instance(db_session)
        provider = StubProvider([[]])
        await collector(db_session, provider, offense_max_pages=7).collect(inst)
        assert provider.calls[0]["max_pages"] == 7

    @pytest.mark.asyncio
    async def test_collection_covers_closed_offenses_too(self, db_session) -> None:
        """Closure is a state transition we must observe, so open_only is False."""
        inst = await make_instance(db_session)
        provider = StubProvider([[]])
        await collector(db_session, provider).collect(inst)
        assert provider.calls[0]["open_only"] is False


class TestFailureSemantics:
    @pytest.mark.asyncio
    async def test_provider_failure_does_not_advance_the_watermark(
        self, db_session
    ) -> None:
        """Advancing past a span we never read would silently lose offenses."""
        inst = await make_instance(db_session)
        mark = NOW - timedelta(minutes=10)
        await collector(
            db_session, StubProvider([[offense(last_updated_time=mark)]])
        ).collect(inst)

        failing = StubProvider(error=ProviderUnavailableError("QRadar is down"))
        report = await collector(db_session, failing).collect(inst)

        wm = await watermark(db_session, inst.id)
        assert report.partial_failure is True
        assert report.error == "QRadar is down"
        assert wm.watermark_at == mark  # unmoved
        assert wm.consecutive_failures == 1
        assert wm.last_error == "QRadar is down"

    @pytest.mark.asyncio
    async def test_repeated_failures_accumulate(self, db_session) -> None:
        inst = await make_instance(db_session)
        for _ in range(3):
            await collector(
                db_session, StubProvider(error=ProviderError("boom"))
            ).collect(inst)
        assert (await watermark(db_session, inst.id)).consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_a_successful_run_clears_the_failure_state(self, db_session) -> None:
        inst = await make_instance(db_session)
        await collector(db_session, StubProvider(error=ProviderError("boom"))).collect(inst)
        await collector(db_session, StubProvider([[offense()]])).collect(inst)

        wm = await watermark(db_session, inst.id)
        assert wm.consecutive_failures == 0
        assert wm.last_error is None

    @pytest.mark.asyncio
    async def test_failed_run_writes_no_snapshots(self, db_session) -> None:
        inst = await make_instance(db_session)
        await collector(
            db_session, StubProvider(error=ProviderUnavailableError("down"))
        ).collect(inst)
        assert await snapshots(db_session, inst.id) == []

    @pytest.mark.asyncio
    async def test_failed_run_does_not_count_as_an_interval(self, db_session) -> None:
        """intervals_collected is the evidence a collection actually happened."""
        inst = await make_instance(db_session)
        await collector(db_session, StubProvider(error=ProviderError("boom"))).collect(inst)
        assert (await watermark(db_session, inst.id)).intervals_collected == 0


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_a_second_collector_skips_while_the_lock_is_held(
        self, db_schema
    ) -> None:
        """Two collectors writing the same offense would race on the snapshot key.

        Two independent sessions are required: an advisory lock is held by a
        connection, so reusing one session could not demonstrate contention.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(db_schema, future=True)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session_a, maker() as session_b:
                inst = await make_instance(session_a)
                await session_a.commit()

                inst_b = await session_b.get(type(inst), inst.id)

                # A holds the lock for the duration of its collect().
                from app.services.locks import CollectorAdvisoryLock

                async with CollectorAdvisoryLock(
                    session_a, get_settings(), inst.id, COLLECTOR_NAME
                ) as acquired:
                    assert acquired is True
                    report = await collector(
                        session_b, StubProvider([[offense()]])
                    ).collect(inst_b)

                assert report.skipped_locked is True
                assert report.snapshots_written == 0
                assert report.offenses_seen == 0
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_the_lock_is_released_after_collection(self, db_session) -> None:
        """A leaked lock would stall every subsequent tick for that instance."""
        inst = await make_instance(db_session)
        first = await collector(db_session, StubProvider([[offense()]])).collect(inst)
        second = await collector(db_session, StubProvider([[offense()]])).collect(inst)
        assert first.skipped_locked is False
        assert second.skipped_locked is False

    @pytest.mark.asyncio
    async def test_locks_are_per_instance(self, db_schema) -> None:
        """One busy console must not block collection on another."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.services.locks import CollectorAdvisoryLock

        engine = create_async_engine(db_schema, future=True)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session_a, maker() as session_b:
                inst_a = await make_instance(session_a)
                inst_b = await make_instance(session_a)
                await session_a.commit()
                inst_b_other = await session_b.get(type(inst_b), inst_b.id)

                async with CollectorAdvisoryLock(
                    session_a, get_settings(), inst_a.id, COLLECTOR_NAME
                ):
                    report = await collector(
                        session_b, StubProvider([[offense()]])
                    ).collect(inst_b_other)

                assert report.skipped_locked is False
                assert report.snapshots_written == 1
        finally:
            await engine.dispose()


class TestBatchResilience:
    @pytest.mark.asyncio
    async def test_a_good_offense_survives_alongside_a_bad_one(self, db_session) -> None:
        """One malformed record must not discard the whole batch.

        A naive `start_time` is the realistic shape of this: OffenseDTO does not
        enforce timezone awareness, so the age calculation raises TypeError. The
        collector drops that record and carries on.

        Note the boundary: only ValueError/TypeError are caught. A record that
        fails at the *database* layer instead (over-length text, constraint
        violation) aborts the transaction and the whole run. See
        docs/PHASE3-HANDOFF.md -- recorded as an open gap rather than papered
        over, because fixing it needs a per-record savepoint.
        """
        inst = await make_instance(db_session)
        bad = offense(9999, start_time=datetime(2026, 7, 20, 8, 0))

        report = await collector(
            db_session, StubProvider([[offense(1), bad, offense(2)]])
        ).collect(inst)

        assert report.dropped == [9999]
        assert report.snapshots_written == 2
        # The good records persist...
        stored = {r.qradar_offense_id for r in await snapshots(db_session, inst.id)}
        assert stored == {1, 2}
        # ...but the run is flagged partial, so the watermark stays put rather
        # than advancing past a span we did not fully read.
        assert report.partial_failure is True
        assert (await watermark(db_session, inst.id)).watermark_at is None

    @pytest.mark.asyncio
    async def test_a_partial_batch_still_records_the_run(self, db_session) -> None:
        inst = await make_instance(db_session)
        bad = offense(9999, start_time=datetime(2026, 7, 20, 8, 0))
        await collector(db_session, StubProvider([[bad]])).collect(inst)

        wm = await watermark(db_session, inst.id)
        assert wm.last_run_at == NOW
        assert wm.intervals_collected == 1

    @pytest.mark.asyncio
    async def test_empty_collection_is_a_clean_no_op(self, db_session) -> None:
        inst = await make_instance(db_session)
        report = await collector(db_session, StubProvider([[]])).collect(inst)

        assert report.offenses_seen == 0
        assert report.snapshots_written == 0
        assert report.partial_failure is False
        assert await snapshots(db_session, inst.id) == []
        # A run that saw nothing still counts as a completed interval.
        assert (await watermark(db_session, inst.id)).intervals_collected == 1

    @pytest.mark.asyncio
    async def test_snapshot_count_matches_the_report(self, db_session) -> None:
        inst = await make_instance(db_session)
        page = [offense(i, last_updated_time=NOW - timedelta(minutes=i)) for i in range(1, 6)]
        report = await collector(db_session, StubProvider([page])).collect(inst)

        total = await db_session.scalar(
            select(func.count()).select_from(OffenseSnapshot).where(
                OffenseSnapshot.instance_id == inst.id
            )
        )
        assert report.snapshots_written == total == 5
