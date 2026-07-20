"""Execute a stored, approved ScheduledSearch against a provider's Ariel API.

Responsibilities and the guarantees they satisfy:

  * Idempotency: one SearchExecution per (search_id, run_key). Retries reuse the
    row, so retries never create duplicate execution records (also enforced by a
    DB unique constraint).
  * Never executes frontend-supplied AQL: only the stored query string of a
    persisted search is run, and it is re-validated defensively here too.
  * Enforces timeout, max time range, max rows, and per-instance/global
    concurrency.
  * Polls Ariel rather than using QRadar's blocking wait, cancelling on timeout.
  * Retries retryable failures with exponential backoff + jitter, up to a cap.
  * Records QRadar search id, lifecycle state, duration, typed failure reason and
    retry count.
  * Stores aggregated result metrics only — never raw events.

Clock and sleep are injected so lifecycle/timeout/retry tests are deterministic.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import ExecutionErrorType, ExecutionStatus
from app.models.search import ScheduledSearch, SearchExecution, SearchResultMetric
from app.providers.base import ProviderCapability, QRadarProvider
from app.services.aql_validator import validate_aql
from app.services.ariel_errors import (
    ArielSearchFailed,
    ArielTimeout,
    ResultParsingError,
    classify,
    is_retryable,
)
from app.services.backoff import backoff_delay
from app.services.concurrency import ConcurrencyLimiter

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SearchExecutor:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        limiter: ConcurrencyLimiter,
        *,
        settings: Settings | None = None,
        clock: Clock = _utcnow,
        sleep: Sleep | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.limiter = limiter
        self.settings = settings or get_settings()
        self.clock = clock
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not crypto
        if sleep is None:
            import asyncio

            sleep = asyncio.sleep
        self.sleep = sleep

    async def execute(
        self,
        search: ScheduledSearch,
        *,
        run_key: str,
        instance_key: str,
        trigger: str = "SCHEDULED",
        triggered_by: str | None = None,
    ) -> SearchExecution:
        execution = await self._get_or_create_execution(
            search, run_key=run_key, trigger=trigger, triggered_by=triggered_by
        )
        # Idempotent short-circuit: a completed run is never re-run.
        if execution.status == ExecutionStatus.COMPLETED:
            return execution

        if not self.provider.supports(ProviderCapability.AQL_EXECUTION):
            return self._fail(
                execution,
                ExecutionErrorType.VALIDATION,
                "configured provider cannot execute AQL",
            )

        max_attempts = self.settings.ariel_max_retries + 1
        while True:
            execution.status = ExecutionStatus.RUNNING
            execution.started_at = execution.started_at or self.clock()
            execution.error_type = None
            execution.error_message = None
            await self.session.flush()

            start = self.clock()
            try:
                results = await self._run_once(search, execution)
            except Exception as exc:
                error_type = classify(exc)
                # attempt number that just failed (1-based): retries so far + 1.
                attempt = execution.retry_count + 1
                if not is_retryable(error_type) or attempt >= max_attempts:
                    return self._fail(execution, error_type, str(exc), final=True)

                # Retryable: record this retry and schedule the next attempt with
                # backoff + jitter.
                execution.retry_count = attempt
                delay = backoff_delay(
                    attempt,
                    base_seconds=self.settings.ariel_retry_base_seconds,
                    max_seconds=self.settings.ariel_retry_max_seconds,
                    rng=self._rng,
                )
                execution.next_retry_at = self._add_seconds(self.clock(), delay)
                execution.error_type = error_type
                execution.error_message = str(exc)
                await self.session.flush()
                await self.sleep(delay)
                continue

            # Success.
            duration = (self.clock() - start).total_seconds()
            execution.duration_ms = int(duration * 1000)
            execution.completed_at = self.clock()
            execution.result_count = results["total_count"]
            execution.truncated = results["truncated"]
            execution.ariel_status = "COMPLETED"
            execution.status = ExecutionStatus.COMPLETED
            await self._store_metrics(search, execution, results)
            self._apply_threshold(search, execution, results["total_count"])
            await self.session.flush()
            return execution

    # ------------------------------------------------------------------ steps
    async def _run_once(self, search: ScheduledSearch, execution: SearchExecution) -> dict:
        # Defensive re-validation: only stored queries reach here, but validating
        # again guarantees a query that slipped past an older policy is caught.
        validate_aql(
            search.aql_query,
            max_time_range_hours=search.max_time_range_hours,
            max_result_rows=search.max_result_rows,
            settings=self.settings,
        )

        async with self.limiter.slot(execution_instance_key(execution, search)):
            handle = await self.provider.create_ariel_search(search.aql_query)
            execution.ariel_search_id = handle.search_id
            execution.ariel_status = handle.status
            await self.session.flush()

            status = await self._poll_until_terminal(search, execution)
            if status.status == "ERROR":
                raise ArielSearchFailed("; ".join(status.error_messages) or "Ariel search failed")

            try:
                results = await self.provider.get_ariel_search_results(
                    handle.search_id, max_rows=search.max_result_rows
                )
            except (ArielTimeout, ArielSearchFailed):
                raise
            except Exception as exc:
                raise ResultParsingError(f"failed to fetch/parse results: {exc}") from exc

            return {
                "columns": results.columns,
                "rows": results.rows,
                "total_count": results.total_count,
                "truncated": results.truncated,
            }

    async def _poll_until_terminal(self, search: ScheduledSearch, execution: SearchExecution):
        timeout = min(search.timeout_seconds, self.settings.ariel_max_timeout_seconds)
        poll = self.settings.ariel_poll_interval_seconds
        start = self.clock()
        while True:
            status = await self.provider.get_ariel_search_status(execution.ariel_search_id)
            execution.ariel_status = status.status
            if status.is_terminal:
                return status
            elapsed = (self.clock() - start).total_seconds()
            if elapsed >= timeout:
                await self._safe_cancel(execution.ariel_search_id)
                raise ArielTimeout(f"search exceeded timeout of {timeout}s")
            await self.sleep(poll)

    async def _safe_cancel(self, search_id: str) -> None:
        # Best-effort cancel while already handling a timeout; failure here must
        # not mask the original error.
        import contextlib

        with contextlib.suppress(Exception):
            await self.provider.cancel_ariel_search(search_id)

    async def _store_metrics(
        self, search: ScheduledSearch, execution: SearchExecution, results: dict
    ) -> None:
        bucket = _floor_minute(execution.completed_at or self.clock())
        # Always store the total. Aggregated only — never raw events.
        self.session.add(
            SearchResultMetric(
                execution_id=execution.id,
                search_id=search.id,
                bucket_start=bucket,
                metric_key="total",
                value=float(results["total_count"]),
                dimensions={},
            )
        )
        # Grouped rows become per-dimension metrics (bounded by max_result_rows).
        for row in results["rows"]:
            key, value = _row_to_metric(row)
            if key is None:
                continue
            self.session.add(
                SearchResultMetric(
                    execution_id=execution.id,
                    search_id=search.id,
                    bucket_start=bucket,
                    metric_key=key[:255],
                    value=value,
                    dimensions=row,
                )
            )

    def _apply_threshold(
        self, search: ScheduledSearch, execution: SearchExecution, value: int
    ) -> None:
        if search.threshold_value is None:
            execution.threshold_breached = False
            return
        op = search.threshold_operator
        t = search.threshold_value
        breached = {
            "GT": value > t,
            "GE": value >= t,
            "LT": value < t,
            "LE": value <= t,
            "EQ": value == t,
        }.get(op, value > t)
        execution.threshold_breached = bool(breached)

    # ------------------------------------------------------------- persistence
    async def _get_or_create_execution(
        self, search: ScheduledSearch, *, run_key: str, trigger: str, triggered_by: str | None
    ) -> SearchExecution:
        existing = await self.session.scalar(
            select(SearchExecution).where(
                SearchExecution.search_id == search.id,
                SearchExecution.run_key == run_key,
            )
        )
        if existing is not None:
            return existing
        execution = SearchExecution(
            search_id=search.id,
            run_key=run_key,
            trigger=trigger,
            triggered_by=triggered_by,
            query_version=search.query_version,
            status=ExecutionStatus.PENDING,
        )
        self.session.add(execution)
        await self.session.flush()
        return execution

    def _fail(
        self,
        execution: SearchExecution,
        error_type: ExecutionErrorType,
        message: str,
        *,
        final: bool = True,
    ) -> SearchExecution:
        execution.error_type = error_type.value
        execution.error_message = message[:2000]
        execution.completed_at = self.clock()
        if error_type == ExecutionErrorType.TIMEOUT:
            execution.status = ExecutionStatus.TIMEOUT
        elif error_type == ExecutionErrorType.CANCELLED:
            execution.status = ExecutionStatus.CANCELLED
        else:
            execution.status = ExecutionStatus.FAILED
        return execution

    def _add_seconds(self, when: datetime, seconds: float) -> datetime:
        from datetime import timedelta

        return when + timedelta(seconds=seconds)


def execution_instance_key(execution: SearchExecution, search: ScheduledSearch) -> str:
    # Searches are not yet instance-scoped in the model; use a stable key so the
    # global + per-"instance" caps both apply. Phase 3 wires real instance ids.
    return "default"


def _floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def _row_to_metric(row: dict) -> tuple[str | None, float]:
    """Turn one aggregated result row into (metric_key, value).

    Convention: the first non-numeric column is the grouping label; a column
    named like a count supplies the value, else the row counts as 1.
    """
    if not row:
        return None, 0.0
    label = None
    value = 1.0
    for col, val in row.items():
        count_cols = {"count", "event_count", "total", "cnt"}
        if isinstance(val, (int, float)) and col.lower() in count_cols:
            value = float(val)
        elif label is None and isinstance(val, str):
            label = val
    if label is None:
        # Fall back to the first column's stringified value.
        first_col = next(iter(row))
        label = str(row[first_col])
    return label, value
