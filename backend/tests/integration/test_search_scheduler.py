"""Cron-driven dispatch of scheduled searches.

DB-gated (ScheduledSearch/SearchExecution use PgUUID/JSONB). A no-op sleep and a
fixed clock keep the Ariel lifecycle deterministic; the MockQRadarProvider means
no QRadar is contacted.

The properties under test are the ones that keep a scheduler from becoming an
incident of its own: it must not fire everything at once on first deploy, must
not replay a backlog, must not double-run, and must not let one bad cron
expression stop the rest.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.enums import ExecutionStatus
from app.models.search import SearchExecution
from app.providers.mock import MockQRadarProvider
from app.services.concurrency import InMemoryConcurrencyLimiter
from app.services.search_executor import SearchExecutor
from app.services.search_scheduler import SearchScheduler
from tests.integration.factories import make_search, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _noop_sleep(_seconds: float) -> None:
    return None


def _scheduler(session, **kwargs) -> SearchScheduler:
    executor = SearchExecutor(
        session,
        MockQRadarProvider(seed=1337),
        InMemoryConcurrencyLimiter(per_instance=2, global_limit=4),
        sleep=_noop_sleep,
    )
    return SearchScheduler(session, executor, **kwargs)


async def _execution_count(session, search) -> int:
    return await session.scalar(
        select(func.count()).select_from(SearchExecution).where(
            SearchExecution.search_id == search.id
        )
    )


async def test_first_sight_seeds_the_schedule_without_running(db_session) -> None:
    # Deploying N searches must not immediately fire N Ariel searches.
    search = await make_search(db_session, name="seed-me")
    assert search.next_run_at is None

    report = await _scheduler(db_session).run_due(now=utc(h=10, mi=3))

    assert report.seeded == ["seed-me"]
    assert report.dispatched == []
    assert search.next_run_at is not None
    assert await _execution_count(db_session, search) == 0


async def test_due_search_runs_and_advances_the_schedule(db_session) -> None:
    now = utc(h=10, mi=6)
    search = await make_search(db_session, name="due", next_run_at=utc(h=10, mi=5))

    report = await _scheduler(db_session).run_due(now=now)

    assert report.dispatched == ["due"]
    assert search.last_run_at == utc(h=10, mi=5)
    assert search.next_run_at == utc(h=10, mi=10)
    assert await _execution_count(db_session, search) == 1


async def test_search_not_yet_due_is_left_alone(db_session) -> None:
    search = await make_search(db_session, name="early", next_run_at=utc(h=10, mi=15))

    report = await _scheduler(db_session).run_due(now=utc(h=10, mi=6))

    assert report.dispatched == []
    assert search.last_run_at is None
    assert await _execution_count(db_session, search) == 0


async def test_disabled_search_is_never_dispatched(db_session) -> None:
    search = await make_search(
        db_session, name="off", enabled=False, next_run_at=utc(h=10, mi=0)
    )

    report = await _scheduler(db_session).run_due(now=utc(h=10, mi=6))

    assert report.considered == 0
    assert await _execution_count(db_session, search) == 0


async def test_backlog_coalesces_into_one_run(db_session) -> None:
    """A worker down for 40 minutes must not replay eight ticks at once."""
    search = await make_search(db_session, name="backlog", next_run_at=utc(h=10, mi=5))

    scheduler = _scheduler(db_session)
    report = await scheduler.run_due(now=utc(h=10, mi=47))

    assert report.dispatched == ["backlog"]
    assert await _execution_count(db_session, search) == 1
    # Rescheduled from now, not from the missed tick.
    assert search.next_run_at == utc(h=10, mi=50)

    # The very next cycle finds nothing to do rather than continuing to catch up.
    followup = await scheduler.run_due(now=utc(h=10, mi=48))
    assert followup.dispatched == []
    assert await _execution_count(db_session, search) == 1


async def test_repeated_cycle_at_same_instant_does_not_double_run(db_session) -> None:
    # A duplicate beat delivery maps to the same run key, so no second execution.
    search = await make_search(db_session, name="dup", next_run_at=utc(h=10, mi=5))
    scheduler = _scheduler(db_session)

    await scheduler.run_due(now=utc(h=10, mi=6))
    await scheduler.run_due(now=utc(h=10, mi=6))

    assert await _execution_count(db_session, search) == 1


async def test_run_in_flight_blocks_a_second_dispatch(db_session) -> None:
    search = await make_search(db_session, name="busy", next_run_at=utc(h=10, mi=5))
    db_session.add(
        SearchExecution(
            search_id=search.id, run_key="in-flight", trigger="SCHEDULED",
            query_version=1, status=ExecutionStatus.RUNNING, started_at=utc(h=10, mi=4),
        )
    )
    await db_session.flush()

    report = await _scheduler(db_session).run_due(now=utc(h=10, mi=6))

    assert report.skipped_running == ["busy"]
    assert report.dispatched == []
    # The tick was not consumed, so it runs once the in-flight run clears.
    assert search.next_run_at == utc(h=10, mi=5)


async def test_stale_in_flight_run_does_not_retire_the_search(db_session) -> None:
    """A worker killed mid-run leaves RUNNING forever; that must not silently
    stop the search from ever executing again."""
    search = await make_search(
        db_session, name="stale", timeout_seconds=300, next_run_at=utc(h=10, mi=5)
    )
    db_session.add(
        SearchExecution(
            search_id=search.id, run_key="abandoned", trigger="SCHEDULED",
            query_version=1, status=ExecutionStatus.RUNNING,
            # Older than 2x the search's own timeout.
            started_at=utc(h=9, mi=0),
        )
    )
    await db_session.flush()

    report = await _scheduler(db_session).run_due(now=utc(h=10, mi=6))

    assert report.dispatched == ["stale"]


async def test_invalid_cron_is_reported_and_does_not_stop_the_cycle(db_session) -> None:
    broken = await make_search(
        db_session, name="broken", schedule_cron="not a cron", next_run_at=utc(h=10, mi=5)
    )
    await make_search(db_session, name="healthy", next_run_at=utc(h=10, mi=5))

    report = await _scheduler(db_session).run_due(now=utc(h=10, mi=6))

    assert report.invalid_cron == ["broken"]
    assert report.dispatched == ["healthy"]
    assert await _execution_count(db_session, broken) == 0
    # next_run_at is preserved, so fixing the expression resumes the schedule.
    assert broken.next_run_at == utc(h=10, mi=5)


async def test_cycle_dispatch_is_bounded(db_session) -> None:
    from app.core.config import get_settings

    settings = get_settings().model_copy(update={"search_max_dispatch_per_cycle": 2})
    for i in range(5):
        await make_search(db_session, name=f"s{i}", next_run_at=utc(h=10, mi=5))

    report = await _scheduler(db_session, settings=settings).run_due(now=utc(h=10, mi=6))

    assert len(report.dispatched) == 2
    assert report.considered == 5


async def test_consecutive_failures_tracked_and_reset(db_session) -> None:
    search = await make_search(db_session, name="flaky", next_run_at=utc(h=10, mi=5))

    # A provider that cannot execute AQL makes the run fail deterministically.
    failing = MockQRadarProvider(seed=1337)
    failing_executor = SearchExecutor(
        db_session, failing, InMemoryConcurrencyLimiter(per_instance=2, global_limit=4),
        sleep=_noop_sleep,
    )
    failing_executor.provider = _NoAqlProvider(failing)
    scheduler = SearchScheduler(db_session, failing_executor)

    await scheduler.run_due(now=utc(h=10, mi=6))
    assert search.consecutive_failures == 1

    # A later successful cycle clears the streak.
    healthy_scheduler = _scheduler(db_session)
    await healthy_scheduler.run_due(now=utc(h=10, mi=11))
    assert search.consecutive_failures == 0


class _NoAqlProvider:
    """Wraps a provider but reports no AQL capability, so the executor records a
    validation failure without any network interaction."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def supports(self, capability) -> bool:
        return False

    def __getattr__(self, name):
        return getattr(self._inner, name)
