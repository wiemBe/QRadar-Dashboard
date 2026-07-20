"""Cron-driven dispatch of stored scheduled searches.

The executor (app/services/search_executor.py) knows how to run one search; this
module decides *which* searches are due and *when* they run again. Kept separate
so the scheduling policy is testable without an Ariel lifecycle.

Policy decisions worth stating explicitly:

  * **Cron is parsed with APScheduler's CronTrigger**, already a pinned
    dependency, rather than a hand-rolled parser. `next_run_at` is persisted so
    the schedule survives a worker restart and two workers agree on tick
    identity.
  * **First sight seeds, it does not run.** A search whose `next_run_at` is
    NULL gets its next fire computed and is *not* executed this cycle, so
    deploying N searches never fires N searches at once.
  * **Missed ticks coalesce.** If the worker was down, an overdue search runs
    once and is then scheduled from *now*, not from the missed instant. Firing
    every backlogged tick would stampede QRadar with searches whose results are
    already stale, which is worse than the gap it would fill.
  * **Overlap is refused.** A search with a run still in flight is skipped
    rather than queued; combined with the run-key unique constraint this makes
    a double-run structurally impossible.
  * **A cycle is bounded** by `search_max_dispatch_per_cycle`.
  * **A bad cron expression never breaks the cycle.** It is reported and
    skipped, leaving the other searches to run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.search_alerts import SearchAlertEvaluator
from app.core.config import Settings, get_settings
from app.models.enums import ExecutionStatus
from app.models.search import ScheduledSearch, SearchExecution
from app.services.scheduled_search import ScheduledSearchService
from app.services.search_executor import SearchExecutor

logger = logging.getLogger("app.search_scheduler")


class InvalidCronExpression(ValueError):
    """The stored schedule_cron/schedule_timezone cannot be parsed."""


@dataclass
class SchedulerReport:
    considered: int = 0
    dispatched: list[str] = field(default_factory=list)
    seeded: list[str] = field(default_factory=list)
    skipped_running: list[str] = field(default_factory=list)
    invalid_cron: list[str] = field(default_factory=list)


def next_fire_time(
    cron: str, timezone: str, *, after: datetime | None, now: datetime
) -> datetime:
    """Next scheduled instant for `cron`, strictly after `after`.

    With `after=None` this is the first fire at or after `now` — used to seed a
    search the scheduler has not seen before.
    """
    try:
        trigger = CronTrigger.from_crontab(cron, timezone=timezone)
    except Exception as exc:  # APScheduler raises bare ValueError/KeyError here
        raise InvalidCronExpression(f"{cron!r} ({timezone}): {exc}") from exc

    fire = trigger.get_next_fire_time(after, now)
    if fire is None:
        raise InvalidCronExpression(f"{cron!r} ({timezone}) has no future fire time")
    return fire.astimezone(UTC)


class SearchScheduler:
    def __init__(
        self,
        session: AsyncSession,
        executor: SearchExecutor,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        alert_evaluator: SearchAlertEvaluator | None = None,
    ) -> None:
        self.session = session
        self.executor = executor
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.alerts = alert_evaluator

    async def run_due(self, *, now: datetime | None = None) -> SchedulerReport:
        now = now or self._clock()
        report = SchedulerReport()

        searches = list(
            (
                await self.session.scalars(
                    select(ScheduledSearch)
                    .where(ScheduledSearch.enabled.is_(True))
                    .order_by(ScheduledSearch.next_run_at.nulls_first())
                )
            ).all()
        )
        report.considered = len(searches)

        for search in searches:
            if len(report.dispatched) >= self.settings.search_max_dispatch_per_cycle:
                break
            await self._process(search, now, report)

        await self.session.flush()
        return report

    async def _process(
        self, search: ScheduledSearch, now: datetime, report: SchedulerReport
    ) -> None:
        try:
            # Unseen search: compute the next fire and wait for it.
            if search.next_run_at is None:
                search.next_run_at = next_fire_time(
                    search.schedule_cron, search.schedule_timezone, after=None, now=now
                )
                report.seeded.append(search.name)
                return

            if _as_utc(search.next_run_at) > now:
                return

            if await self._has_run_in_flight(search, now):
                report.skipped_running.append(search.name)
                return

            scheduled_for = _as_utc(search.next_run_at)
            # Reschedule from `now`, not from the missed tick, so a backlog
            # coalesces into one run instead of replaying every tick.
            search.next_run_at = next_fire_time(
                search.schedule_cron, search.schedule_timezone, after=now, now=now
            )
        except InvalidCronExpression as exc:
            # Leave next_run_at as-is so fixing the expression resumes the
            # schedule; a broken cron must not take the whole cycle down.
            logger.warning(
                "invalid cron for scheduled search",
                extra={"search": search.name, "error": str(exc)},
            )
            report.invalid_cron.append(search.name)
            return

        await self._dispatch(search, scheduled_for, report)

    async def _dispatch(
        self, search: ScheduledSearch, scheduled_for: datetime, report: SchedulerReport
    ) -> None:
        run_key = ScheduledSearchService.scheduled_run_key(search, scheduled_for)
        execution = await self.executor.execute(
            search, run_key=run_key, instance_key="default", trigger="SCHEDULED"
        )

        search.last_run_at = scheduled_for
        if execution.status == ExecutionStatus.COMPLETED:
            search.consecutive_failures = 0
        else:
            search.consecutive_failures += 1

        await self.session.flush()
        report.dispatched.append(search.name)

        if self.alerts is not None:
            await self.alerts.evaluate(search, execution)

    async def _has_run_in_flight(self, search: ScheduledSearch, now: datetime) -> bool:
        """True if a previous run is still going.

        Bounded by age: a worker killed mid-execution leaves a row stuck in
        RUNNING forever, and treating that as live would silently retire the
        search. Past twice its own timeout a run is presumed dead and no longer
        blocks the schedule — the run-key unique constraint still prevents the
        replacement run from duplicating a row.
        """
        stale_before = now - timedelta(seconds=search.timeout_seconds * 2)
        candidates = await self.session.scalars(
            select(SearchExecution).where(
                SearchExecution.search_id == search.id,
                SearchExecution.status.in_(
                    [ExecutionStatus.PENDING, ExecutionStatus.RUNNING]
                ),
            )
        )
        for execution in candidates:
            started = execution.started_at or execution.created_at
            if started is None or _as_utc(started) > stale_before:
                return True
        return False


def _as_utc(value: datetime) -> datetime:
    """Postgres returns tz-aware values; SQLite hands back naive ones. Compare
    everything in UTC so the two backends agree on whether a tick is due."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
