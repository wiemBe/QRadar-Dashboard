"""Scheduled-search catalog, versions, executions, and authorized manual run."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.routing import default_policy
from app.alerts.search_alerts import SearchAlertEvaluator
from app.api.deps import ProviderDep, SessionDep
from app.core.config import get_settings
from app.models.search import SearchExecution, SearchQueryVersion, SearchResultMetric
from app.repositories.audit import record_audit
from app.schemas.search import (
    ExecutionOut,
    ResultMetricPoint,
    ScheduledSearchCreate,
    ScheduledSearchOut,
    ScheduledSearchUpdate,
    SearchResultTrendOut,
    SearchVersionOut,
)
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_SEARCH_EXECUTE,
    PERM_SEARCH_WRITE,
    PermissionDenied,
    Principal,
    require_permission,
)
from app.services.concurrency import InMemoryConcurrencyLimiter
from app.services.scheduled_search import ScheduledSearchService
from app.services.search_executor import SearchExecutor

router = APIRouter(prefix="/searches", tags=["searches"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a query-string bound to UTC.

    A naive timestamp is read as UTC rather than rejected: the whole platform
    stores and reports UTC, so that is the only defensible reading, and it keeps
    `?start=2026-07-01T00:00:00` working alongside an explicit offset.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _guard(principal: Principal, permission: str) -> None:
    try:
        require_permission(principal, permission)
    except PermissionDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.get("", response_model=list[ScheduledSearchOut])
async def list_searches(session: SessionDep) -> list[ScheduledSearchOut]:
    searches = await ScheduledSearchService(session).list()
    return [ScheduledSearchOut.model_validate(s) for s in searches]


@router.post("", response_model=ScheduledSearchOut, status_code=status.HTTP_201_CREATED)
async def create_search(
    payload: ScheduledSearchCreate, session: SessionDep, principal: PrincipalDep
) -> ScheduledSearchOut:
    _guard(principal, PERM_SEARCH_WRITE)
    svc = ScheduledSearchService(session)
    search = await svc.create(payload, actor=principal.subject)
    await record_audit(
        session, action="search.create", actor=principal.subject,
        object_type="scheduled_search", object_id=search.id,
        detail={"name": search.name},
    )
    return ScheduledSearchOut.model_validate(search)


@router.get("/{search_id}", response_model=ScheduledSearchOut)
async def get_search(search_id: uuid.UUID, session: SessionDep) -> ScheduledSearchOut:
    search = await ScheduledSearchService(session).get(search_id)
    if search is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")
    return ScheduledSearchOut.model_validate(search)


@router.patch("/{search_id}", response_model=ScheduledSearchOut)
async def update_search(
    search_id: uuid.UUID,
    payload: ScheduledSearchUpdate,
    session: SessionDep,
    principal: PrincipalDep,
) -> ScheduledSearchOut:
    _guard(principal, PERM_SEARCH_WRITE)
    svc = ScheduledSearchService(session)
    search = await svc.get(search_id)
    if search is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")
    before_version = search.query_version
    search = await svc.update(search, payload, actor=principal.subject)
    await record_audit(
        session, action="search.update", actor=principal.subject,
        object_type="scheduled_search", object_id=search.id,
        detail={"version_before": before_version, "version_after": search.query_version},
    )
    return ScheduledSearchOut.model_validate(search)


@router.get("/{search_id}/versions", response_model=list[SearchVersionOut])
async def list_versions(search_id: uuid.UUID, session: SessionDep) -> list[SearchVersionOut]:
    svc = ScheduledSearchService(session)
    if await svc.get(search_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")
    return [SearchVersionOut.model_validate(v) for v in await svc.versions(search_id)]


@router.get("/{search_id}/executions", response_model=list[ExecutionOut])
async def list_executions(search_id: uuid.UUID, session: SessionDep) -> list[ExecutionOut]:
    svc = ScheduledSearchService(session)
    if await svc.get(search_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")
    return [ExecutionOut.model_validate(e) for e in await svc.executions(search_id)]


RESULTS_DEFAULT_LIMIT = 500
RESULTS_MAX_LIMIT = 5000


@router.get("/{search_id}/results", response_model=SearchResultTrendOut)
async def list_results(
    search_id: uuid.UUID,
    session: SessionDep,
    metric_key: Annotated[str, Query(min_length=1, max_length=255)] = "total",
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(gt=0, le=RESULTS_MAX_LIMIT)] = RESULTS_DEFAULT_LIMIT,
) -> SearchResultTrendOut:
    """Stored result aggregates for one search, oldest first.

    Read-only, and consistent with the other read routes on this router: no
    additional permission is introduced.

    The window is bounded on both ends and capped at RESULTS_MAX_LIMIT points.
    When more points match than the limit allows, the *most recent* ones win --
    a trend chart that silently showed the oldest 500 of 50,000 points would be
    actively misleading. The rows are re-sorted ascending before returning.
    """
    svc = ScheduledSearchService(session)
    search = await svc.get(search_id)
    if search is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")

    start = _as_utc(start)
    end = _as_utc(end)
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "start must not be after end"
        )

    # One joined query, so N points never become N execution lookups. The
    # version join is outer: an execution whose version row was pruned still
    # belongs on the chart, it just has no version id.
    stmt = (
        select(
            SearchResultMetric.bucket_start,
            SearchResultMetric.metric_key,
            SearchResultMetric.value,
            SearchResultMetric.dimensions,
            SearchExecution.id.label("execution_id"),
            SearchExecution.status.label("execution_status"),
            SearchExecution.duration_ms,
            SearchExecution.result_count,
            SearchExecution.threshold_breached,
            SearchExecution.query_version,
            SearchQueryVersion.id.label("query_version_id"),
        )
        .join(SearchExecution, SearchResultMetric.execution_id == SearchExecution.id)
        .outerjoin(
            SearchQueryVersion,
            (SearchQueryVersion.search_id == SearchExecution.search_id)
            & (SearchQueryVersion.version == SearchExecution.query_version),
        )
        .where(
            SearchResultMetric.search_id == search_id,
            SearchResultMetric.metric_key == metric_key,
        )
    )
    if start is not None:
        stmt = stmt.where(SearchResultMetric.bucket_start >= start)
    if end is not None:
        stmt = stmt.where(SearchResultMetric.bucket_start <= end)

    rows = (
        await session.execute(
            stmt.order_by(
                SearchResultMetric.bucket_start.desc(), SearchExecution.id.desc()
            ).limit(limit)
        )
    ).all()

    points = [ResultMetricPoint.model_validate(row, from_attributes=True) for row in reversed(rows)]
    return SearchResultTrendOut(
        search_id=search_id,
        metric_key=metric_key,
        threshold_value=search.threshold_value,
        threshold_operator=search.threshold_operator,
        count=len(points),
        points=points,
    )


@router.post("/{search_id}/run", response_model=ExecutionOut)
async def run_search(
    search_id: uuid.UUID,
    session: SessionDep,
    provider: ProviderDep,
    principal: PrincipalDep,
) -> ExecutionOut:
    """Manually execute a stored search. Requires search:execute.

    Executes the *stored* query only — never AQL from the request body.
    """
    _guard(principal, PERM_SEARCH_EXECUTE)
    svc = ScheduledSearchService(session)
    search = await svc.get(search_id)
    if search is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")

    settings = get_settings()
    limiter = InMemoryConcurrencyLimiter(
        per_instance=settings.ariel_max_concurrent_searches,
        global_limit=settings.ariel_global_max_concurrent_searches,
    )
    executor = SearchExecutor(session, provider, limiter, settings=settings)
    run_key = svc.manual_run_key(search, principal.subject)
    execution = await executor.execute(
        search, run_key=run_key, instance_key="default",
        trigger="MANUAL", triggered_by=principal.subject,
    )
    # A manual run is a real observation of the condition, so it raises and
    # clears the same alerts a scheduled run would — otherwise a breach found
    # by hand would be invisible to whoever is on call.
    enqueuer = NotificationDispatcher(session, notifiers={}, policy=default_policy())
    await SearchAlertEvaluator(session, settings=settings, enqueuer=enqueuer).evaluate(
        search, execution
    )
    await record_audit(
        session, action="search.execute", actor=principal.subject,
        object_type="scheduled_search", object_id=search.id,
        detail={"run_key": run_key, "status": str(execution.status)},
    )
    return ExecutionOut.model_validate(execution)
