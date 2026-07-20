"""Scheduled-search outcomes becoming alerts.

DB-gated. Covers both conditions and, critically, that they deduplicate the way
the anomaly path does: a breach that persists across runs updates one alert and
notifies once, rather than paging on every tick.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.alerts.fingerprint import search_failure_fingerprint, search_threshold_fingerprint
from app.alerts.search_alerts import SearchAlertEvaluator
from app.models.alert import Alert
from app.models.enums import AlertStatus, AlertTransition, ExecutionErrorType, ExecutionStatus
from app.models.search import SearchExecution
from tests.integration.factories import make_search, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class RecordingEnqueuer:
    """Stands in for the dispatcher: records intents without delivering."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[str, AlertTransition]] = []

    async def enqueue(self, alert, transition):
        self.enqueued.append((alert.fingerprint, transition))
        return []


async def _execution(session, search, **kwargs) -> SearchExecution:
    defaults = dict(
        search_id=search.id, run_key=f"run-{len(kwargs)}-{id(kwargs)}", trigger="SCHEDULED",
        query_version=1, status=ExecutionStatus.COMPLETED, result_count=0,
        threshold_breached=False, completed_at=utc(h=10, mi=5),
    )
    defaults.update(kwargs)
    execution = SearchExecution(**defaults)
    session.add(execution)
    await session.flush()
    return execution


async def _alert_for(session, fingerprint) -> Alert | None:
    return await session.scalar(select(Alert).where(Alert.fingerprint == fingerprint))


# ------------------------------------------------------------------ threshold
async def test_threshold_breach_opens_an_alert_and_notifies(db_session) -> None:
    search = await make_search(db_session, name="brute-force", threshold_value=100)
    execution = await _execution(
        db_session, search, run_key="r1", result_count=250, threshold_breached=True
    )
    enqueuer = RecordingEnqueuer()

    await SearchAlertEvaluator(db_session, enqueuer=enqueuer).evaluate(search, execution)

    fingerprint = search_threshold_fingerprint(search.id)
    alert = await _alert_for(db_session, fingerprint)
    assert alert is not None
    assert alert.status == AlertStatus.OPEN
    assert alert.evidence_snapshot["observed_value"] == 250
    assert enqueuer.enqueued == [(fingerprint, AlertTransition.OPENED)]


async def test_repeated_breach_updates_one_alert_and_notifies_once(db_session) -> None:
    search = await make_search(db_session, name="repeat", threshold_value=100)
    enqueuer = RecordingEnqueuer()
    evaluator = SearchAlertEvaluator(db_session, enqueuer=enqueuer)

    for run in ("r1", "r2", "r3"):
        execution = await _execution(
            db_session, search, run_key=run, result_count=250, threshold_breached=True
        )
        await evaluator.evaluate(search, execution)

    count = await db_session.scalar(select(func.count()).select_from(Alert))
    assert count == 1
    alert = await _alert_for(db_session, search_threshold_fingerprint(search.id))
    assert alert.occurrence_count == 3
    # The whole point of dedup: three breaches, one page.
    assert len(enqueuer.enqueued) == 1


async def test_run_back_under_threshold_resolves_the_alert(db_session) -> None:
    search = await make_search(db_session, name="recovers", threshold_value=100)
    enqueuer = RecordingEnqueuer()
    evaluator = SearchAlertEvaluator(db_session, enqueuer=enqueuer)

    breach = await _execution(
        db_session, search, run_key="r1", result_count=250, threshold_breached=True
    )
    await evaluator.evaluate(search, breach)

    clear = await _execution(
        db_session, search, run_key="r2", result_count=5, threshold_breached=False
    )
    await evaluator.evaluate(search, clear)

    alert = await _alert_for(db_session, search_threshold_fingerprint(search.id))
    assert alert.status == AlertStatus.RESOLVED
    assert [t for _, t in enqueuer.enqueued] == [
        AlertTransition.OPENED,
        AlertTransition.RESOLVED,
    ]


async def test_search_without_a_threshold_never_alerts(db_session) -> None:
    search = await make_search(db_session, name="no-threshold", threshold_value=None)
    execution = await _execution(db_session, search, run_key="r1", result_count=9999)

    await SearchAlertEvaluator(db_session).evaluate(search, execution)

    assert await db_session.scalar(select(func.count()).select_from(Alert)) == 0


async def test_failed_run_does_not_resolve_a_threshold_alert(db_session) -> None:
    """A run that errored says nothing about the threshold; treating it as
    recovery would clear a real breach on a transient error."""
    search = await make_search(db_session, name="errored", threshold_value=100)
    evaluator = SearchAlertEvaluator(db_session)

    breach = await _execution(
        db_session, search, run_key="r1", result_count=250, threshold_breached=True
    )
    await evaluator.evaluate(search, breach)

    failure = await _execution(
        db_session, search, run_key="r2", status=ExecutionStatus.FAILED,
        error_type=ExecutionErrorType.NETWORK.value, result_count=None,
    )
    await evaluator.evaluate(search, failure)

    alert = await _alert_for(db_session, search_threshold_fingerprint(search.id))
    assert alert.status == AlertStatus.OPEN


# -------------------------------------------------------------------- failure
async def test_failures_below_the_threshold_do_not_alert(db_session) -> None:
    search = await make_search(db_session, name="one-off")
    search.consecutive_failures = 1
    execution = await _execution(
        db_session, search, run_key="r1", status=ExecutionStatus.FAILED,
        error_type=ExecutionErrorType.NETWORK.value,
    )

    await SearchAlertEvaluator(db_session).evaluate(search, execution)

    assert await _alert_for(db_session, search_failure_fingerprint(search.id)) is None


async def test_repeated_failures_alert_that_the_search_is_not_running(db_session) -> None:
    search = await make_search(db_session, name="dead-detection")
    search.consecutive_failures = 3
    execution = await _execution(
        db_session, search, run_key="r1", status=ExecutionStatus.FAILED,
        error_type=ExecutionErrorType.TIMEOUT.value, error_message="timed out",
    )
    enqueuer = RecordingEnqueuer()

    await SearchAlertEvaluator(db_session, enqueuer=enqueuer).evaluate(search, execution)

    alert = await _alert_for(db_session, search_failure_fingerprint(search.id))
    assert alert is not None
    assert alert.status == AlertStatus.OPEN
    assert enqueuer.enqueued == [(alert.fingerprint, AlertTransition.OPENED)]


async def test_successful_run_resolves_the_failure_alert(db_session) -> None:
    search = await make_search(db_session, name="recovered-detection")
    search.consecutive_failures = 3
    evaluator = SearchAlertEvaluator(db_session)

    failed = await _execution(
        db_session, search, run_key="r1", status=ExecutionStatus.FAILED,
        error_type=ExecutionErrorType.TIMEOUT.value,
    )
    await evaluator.evaluate(search, failed)

    search.consecutive_failures = 0
    ok = await _execution(db_session, search, run_key="r2", result_count=3)
    await evaluator.evaluate(search, ok)

    alert = await _alert_for(db_session, search_failure_fingerprint(search.id))
    assert alert.status == AlertStatus.RESOLVED


async def test_failure_and_threshold_alerts_coexist(db_session) -> None:
    """Distinct conditions on the same search must not deduplicate together."""
    search = await make_search(db_session, name="both", threshold_value=10)
    evaluator = SearchAlertEvaluator(db_session)

    breach = await _execution(
        db_session, search, run_key="r1", result_count=99, threshold_breached=True
    )
    await evaluator.evaluate(search, breach)

    search.consecutive_failures = 3
    failure = await _execution(
        db_session, search, run_key="r2", status=ExecutionStatus.FAILED,
        error_type=ExecutionErrorType.NETWORK.value,
    )
    await evaluator.evaluate(search, failure)

    assert await _alert_for(db_session, search_threshold_fingerprint(search.id)) is not None
    assert await _alert_for(db_session, search_failure_fingerprint(search.id)) is not None
    assert await db_session.scalar(select(func.count()).select_from(Alert)) == 2
