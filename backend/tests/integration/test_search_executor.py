"""Search executor: timeout, retry, polling, idempotency, threshold, concurrency.

DB-gated (SearchExecution/SearchResultMetric use PgUUID/JSONB). Uses a FakeClock
and a no-op sleep so timeout/retry are deterministic with no real waiting.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.enums import ExecutionErrorType, ExecutionStatus
from app.models.search import SearchExecution
from app.providers.base import ProviderCapability, ProviderUnavailableError
from app.providers.dto import ArielSearchStatusDTO
from app.providers.mock import MockQRadarProvider
from app.services.concurrency import InMemoryConcurrencyLimiter
from app.services.search_executor import SearchExecutor
from tests.integration.factories import FakeClock, make_search, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _noop_sleep(_seconds: float) -> None:
    return None


def _limiter() -> InMemoryConcurrencyLimiter:
    return InMemoryConcurrencyLimiter(per_instance=2, global_limit=4)


async def test_successful_execution_stores_aggregated_metrics(db_session) -> None:
    search = await make_search(db_session)
    provider = MockQRadarProvider(seed=1337)
    executor = SearchExecutor(db_session, provider, _limiter(), sleep=_noop_sleep)

    execution = await executor.execute(search, run_key="run-1", instance_key="default")
    assert execution.status == ExecutionStatus.COMPLETED
    assert execution.ariel_search_id is not None
    assert execution.duration_ms is not None
    # A 'total' result metric is always stored; raw events never are.
    assert execution.result_count is not None


async def test_idempotent_run_key_reuses_execution(db_session) -> None:
    search = await make_search(db_session)
    provider = MockQRadarProvider(seed=1337)
    executor = SearchExecutor(db_session, provider, _limiter(), sleep=_noop_sleep)

    first = await executor.execute(search, run_key="dup", instance_key="default")
    second = await executor.execute(search, run_key="dup", instance_key="default")
    assert first.id == second.id
    count = await db_session.scalar(
        select(func.count()).select_from(SearchExecution).where(
            SearchExecution.search_id == search.id
        )
    )
    assert count == 1  # retries/re-runs of the same key never duplicate


async def test_timeout_cancels_and_records(db_session) -> None:
    class NeverCompletes(MockQRadarProvider):
        async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
            return ArielSearchStatusDTO(search_id=search_id, status="EXECUTE", progress=10)

    cancelled: list[str] = []

    class Provider(NeverCompletes):
        async def cancel_ariel_search(self, search_id: str) -> None:
            cancelled.append(search_id)

    search = await make_search(db_session, timeout_seconds=10)
    # Clock advances 5s per call so the poll loop crosses the 10s timeout fast.
    clock = FakeClock(utc(), step_seconds=5)
    executor = SearchExecutor(
        db_session, Provider(), _limiter(), clock=clock, sleep=_noop_sleep,
    )
    execution = await executor.execute(search, run_key="to", instance_key="default")
    assert execution.status == ExecutionStatus.TIMEOUT
    assert execution.error_type == ExecutionErrorType.TIMEOUT.value
    assert cancelled, "timed-out search must be cancelled"


async def test_retry_then_success_single_row(db_session, monkeypatch) -> None:
    calls = {"n": 0}

    class Flaky(MockQRadarProvider):
        async def create_ariel_search(self, aql: str):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise ProviderUnavailableError("transient network blip")
            return await super().create_ariel_search(aql)

    from app.core.config import Settings

    settings = Settings(encryption_key="x" * 44, ariel_max_retries=3,
                        ariel_retry_base_seconds=1, ariel_retry_max_seconds=2)
    search = await make_search(db_session)
    executor = SearchExecutor(
        db_session, Flaky(), _limiter(), settings=settings, sleep=_noop_sleep,
    )
    execution = await executor.execute(search, run_key="retry", instance_key="default")
    assert execution.status == ExecutionStatus.COMPLETED
    assert execution.retry_count == 2  # failed twice, succeeded on third
    count = await db_session.scalar(
        select(func.count()).select_from(SearchExecution).where(
            SearchExecution.search_id == search.id
        )
    )
    assert count == 1


async def test_validation_failure_is_not_retried(db_session) -> None:
    # An unsafe stored query fails validation and must fail immediately.
    search = await make_search(db_session, aql="SELECT * FROM events")  # unbounded
    provider = MockQRadarProvider(seed=1)
    executor = SearchExecutor(db_session, provider, _limiter(), sleep=_noop_sleep)
    execution = await executor.execute(search, run_key="bad", instance_key="default")
    assert execution.status == ExecutionStatus.FAILED
    assert execution.error_type == ExecutionErrorType.VALIDATION.value
    assert execution.retry_count == 0


async def test_threshold_breach_flagged(db_session) -> None:
    # Force a deterministic non-zero result and a threshold of 0 so GT breaches.
    class Counting(MockQRadarProvider):
        async def get_ariel_search_results(self, search_id: str, *, max_rows: int):
            from app.providers.dto import ArielSearchResultsDTO
            return ArielSearchResultsDTO(
                search_id=search_id, columns=["grouping", "event_count"],
                rows=[{"grouping": "h1", "event_count": 5}], total_count=5, truncated=False,
            )

    search = await make_search(db_session, threshold_value=0)
    executor = SearchExecutor(db_session, Counting(), _limiter(), sleep=_noop_sleep)
    execution = await executor.execute(search, run_key="thr", instance_key="default")
    assert execution.threshold_breached is True


async def test_incapable_provider_fails_validation(db_session) -> None:
    class NoAql(MockQRadarProvider):
        capabilities = frozenset({ProviderCapability.INVENTORY})

    search = await make_search(db_session)
    executor = SearchExecutor(db_session, NoAql(), _limiter(), sleep=_noop_sleep)
    execution = await executor.execute(search, run_key="x", instance_key="default")
    assert execution.status == ExecutionStatus.FAILED
    assert execution.error_type == ExecutionErrorType.VALIDATION.value
