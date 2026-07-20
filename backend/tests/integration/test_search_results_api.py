"""GET /searches/{id}/results against a real PostgreSQL instance.

Exercises the endpoint end to end over stored SearchResultMetric rows joined to
their SearchExecution: ordering, metric_key filtering, window bounds, limit
policy, the query-version join that lets the chart mark AQL boundaries, and the
rejection paths. DB-gated.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest

from app.core.database import get_session
from app.main import create_app
from app.models.enums import ExecutionStatus
from app.models.search import SearchExecution, SearchQueryVersion, SearchResultMetric
from tests.integration.factories import make_search, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# Distinguishes "derive result_count from value" from an explicit None, which is
# what a failed execution actually stores.
_DERIVE = object()


def _client(session) -> httpx.AsyncClient:
    """In-process client sharing the test's session and event loop."""

    async def _session():
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _execution(
    session,
    search,
    *,
    run_key: str,
    bucket,
    value: float,
    query_version: int = 1,
    status: ExecutionStatus = ExecutionStatus.COMPLETED,
    duration_ms: int | None = 1200,
    result_count: int | None | object = _DERIVE,
    threshold_breached: bool = False,
    metric_key: str = "total",
) -> SearchExecution:
    """One execution plus the aggregate it produced."""
    execution = SearchExecution(
        search_id=search.id, run_key=run_key, query_version=query_version,
        trigger="SCHEDULED", status=status, started_at=bucket, completed_at=bucket,
        duration_ms=duration_ms,
        result_count=int(value) if result_count is _DERIVE else result_count,
        threshold_breached=threshold_breached,
    )
    session.add(execution)
    await session.flush()
    session.add(
        SearchResultMetric(
            execution_id=execution.id, search_id=search.id, bucket_start=bucket,
            metric_key=metric_key, value=value, dimensions={},
        )
    )
    await session.flush()
    return execution


async def _series(session, search, *, count: int, start=None, step_min: int = 5):
    base = start or utc(h=10)
    made = []
    for i in range(count):
        made.append(
            await _execution(
                session, search, run_key=f"run-{i}",
                bucket=base + timedelta(minutes=step_min * i), value=float(100 + i),
            )
        )
    return made


async def test_returns_points_in_chronological_order(db_session) -> None:
    search = await make_search(db_session, name="trend-order")
    await _series(db_session, search, count=5)

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/results")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["search_id"] == str(search.id)
    assert body["metric_key"] == "total"
    assert body["count"] == 5
    stamps = [p["bucket_start"] for p in body["points"]]
    assert stamps == sorted(stamps), "points must be oldest-first for a trend line"
    assert [p["value"] for p in body["points"]] == [100.0, 101.0, 102.0, 103.0, 104.0]


async def test_point_carries_execution_status_duration_and_threshold(db_session) -> None:
    search = await make_search(db_session, name="trend-fields", threshold_value=50.0)
    await _execution(
        db_session, search, run_key="r-ok", bucket=utc(h=10), value=120.0,
        duration_ms=987, threshold_breached=True,
    )

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/results")

    body = resp.json()
    assert body["threshold_value"] == 50.0
    assert body["threshold_operator"] == "GT"
    point = body["points"][0]
    assert point["execution_status"] == ExecutionStatus.COMPLETED.value
    assert point["duration_ms"] == 987
    assert point["result_count"] == 120
    assert point["threshold_breached"] is True
    assert point["query_version"] == 1


async def test_failed_execution_is_represented_not_hidden(db_session) -> None:
    """A failed run that still stored an aggregate must remain visible -- the
    chart has to be able to distinguish 'zero results' from 'did not run'."""
    search = await make_search(db_session, name="trend-failed")
    await _execution(db_session, search, run_key="r-ok", bucket=utc(h=10), value=100.0)
    await _execution(
        db_session, search, run_key="r-bad", bucket=utc(h=10, mi=5), value=0.0,
        status=ExecutionStatus.FAILED, duration_ms=None, result_count=None,
    )

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/results")

    points = resp.json()["points"]
    assert len(points) == 2
    assert points[1]["execution_status"] == ExecutionStatus.FAILED.value
    assert points[1]["duration_ms"] is None
    assert points[1]["result_count"] is None


async def test_query_version_boundary_is_reported(db_session) -> None:
    """Results either side of an AQL change are not comparable, so the endpoint
    must carry the version that produced each point, with its version row id."""
    search = await make_search(db_session, name="trend-versions")
    v1 = SearchQueryVersion(search_id=search.id, version=1, aql_query="SELECT 1 FROM events")
    v2 = SearchQueryVersion(search_id=search.id, version=2, aql_query="SELECT 2 FROM events")
    db_session.add_all([v1, v2])
    await db_session.flush()

    await _execution(db_session, search, run_key="r1", bucket=utc(h=10),
                     value=10.0, query_version=1)
    await _execution(db_session, search, run_key="r2", bucket=utc(h=10, mi=5),
                     value=11.0, query_version=2)

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/results")

    points = resp.json()["points"]
    assert [p["query_version"] for p in points] == [1, 2]
    assert points[0]["query_version_id"] == str(v1.id)
    assert points[1]["query_version_id"] == str(v2.id)


async def test_execution_without_a_version_row_still_returns(db_session) -> None:
    """The version join is outer: a pruned version row must not drop the point."""
    search = await make_search(db_session, name="trend-noversion")
    await _execution(db_session, search, run_key="r1", bucket=utc(h=10),
                     value=10.0, query_version=7)

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/results")

    points = resp.json()["points"]
    assert len(points) == 1
    assert points[0]["query_version"] == 7
    assert points[0]["query_version_id"] is None


async def test_metric_key_filters(db_session) -> None:
    search = await make_search(db_session, name="trend-keys")
    await _execution(db_session, search, run_key="r1", bucket=utc(h=10), value=100.0)
    await _execution(db_session, search, run_key="r2", bucket=utc(h=10, mi=5),
                     value=7.0, metric_key="10.0.0.1")

    async with _client(db_session) as client:
        default = await client.get(f"/api/v1/searches/{search.id}/results")
        scoped = await client.get(
            f"/api/v1/searches/{search.id}/results", params={"metric_key": "10.0.0.1"}
        )

    assert [p["value"] for p in default.json()["points"]] == [100.0]
    assert [p["value"] for p in scoped.json()["points"]] == [7.0]
    assert scoped.json()["metric_key"] == "10.0.0.1"


async def test_window_bounds_are_inclusive_and_utc(db_session) -> None:
    search = await make_search(db_session, name="trend-window")
    await _series(db_session, search, count=5)  # 10:00, 10:05, 10:10, 10:15, 10:20

    async with _client(db_session) as client:
        resp = await client.get(
            f"/api/v1/searches/{search.id}/results",
            params={"start": "2026-07-15T10:05:00Z", "end": "2026-07-15T10:15:00Z"},
        )

    body = resp.json()
    assert body["count"] == 3
    assert [p["value"] for p in body["points"]] == [101.0, 102.0, 103.0]


async def test_offset_bounds_are_converted_to_utc(db_session) -> None:
    search = await make_search(db_session, name="trend-offset")
    await _series(db_session, search, count=5)

    async with _client(db_session) as client:
        # 12:05+02:00 == 10:05Z. Relabelling rather than converting would return
        # every point instead of the last three.
        resp = await client.get(
            f"/api/v1/searches/{search.id}/results",
            params={"start": "2026-07-15T12:05:00+02:00"},
        )

    assert [p["value"] for p in resp.json()["points"]] == [101.0, 102.0, 103.0, 104.0]


async def test_limit_keeps_the_most_recent_points(db_session) -> None:
    """Truncating to the oldest N would render a stale chart labelled current."""
    search = await make_search(db_session, name="trend-limit")
    await _series(db_session, search, count=10)

    async with _client(db_session) as client:
        resp = await client.get(
            f"/api/v1/searches/{search.id}/results", params={"limit": 3}
        )

    body = resp.json()
    assert body["count"] == 3
    assert [p["value"] for p in body["points"]] == [107.0, 108.0, 109.0]


async def test_empty_dataset_is_an_empty_trend_not_a_404(db_session) -> None:
    search = await make_search(db_session, name="trend-empty")

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/results")

    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    assert resp.json()["points"] == []


async def test_unknown_search_is_404(db_session) -> None:
    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{uuid.uuid4()}/results")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "search not found"


async def test_inverted_range_is_rejected(db_session) -> None:
    search = await make_search(db_session, name="trend-badrange")

    async with _client(db_session) as client:
        resp = await client.get(
            f"/api/v1/searches/{search.id}/results",
            params={"start": "2026-07-15T12:00:00Z", "end": "2026-07-15T10:00:00Z"},
        )

    assert resp.status_code == 422
    assert "start must not be after end" in resp.text


async def test_excessive_limit_is_rejected(db_session) -> None:
    search = await make_search(db_session, name="trend-biglimit")

    async with _client(db_session) as client:
        too_big = await client.get(
            f"/api/v1/searches/{search.id}/results", params={"limit": 100000}
        )
        non_positive = await client.get(
            f"/api/v1/searches/{search.id}/results", params={"limit": 0}
        )

    assert too_big.status_code == 422
    assert non_positive.status_code == 422


async def test_points_are_fetched_without_an_n_plus_one(db_session) -> None:
    """Point count must not drive query count.

    The execution and version data is joined, so 3 points and 30 points cost the
    same number of round trips. Comparing two sizes catches a regression to
    per-point lookups without hard-coding an exact query count.
    """
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    small = await make_search(db_session, name="trend-n1-small")
    large = await make_search(db_session, name="trend-n1-large")
    await _series(db_session, small, count=3)
    await _series(db_session, large, count=30)

    counts: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        counts.append(statement)

    event.listen(Engine, "before_cursor_execute", _count)
    try:
        async with _client(db_session) as client:
            counts.clear()
            small_resp = await client.get(f"/api/v1/searches/{small.id}/results")
            small_queries = len(counts)
            counts.clear()
            large_resp = await client.get(f"/api/v1/searches/{large.id}/results")
            large_queries = len(counts)
    finally:
        event.remove(Engine, "before_cursor_execute", _count)

    assert small_resp.json()["count"] == 3
    assert large_resp.json()["count"] == 30
    assert large_queries == small_queries, (
        f"query count grew with result count ({small_queries} -> {large_queries}); "
        "the execution/version join has regressed to per-point lookups"
    )


async def test_other_searches_metrics_are_not_returned(db_session) -> None:
    mine = await make_search(db_session, name="trend-mine")
    theirs = await make_search(db_session, name="trend-theirs")
    await _execution(db_session, mine, run_key="r1", bucket=utc(h=10), value=1.0)
    await _execution(db_session, theirs, run_key="r1", bucket=utc(h=10), value=99.0)

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{mine.id}/results")

    assert [p["value"] for p in resp.json()["points"]] == [1.0]
