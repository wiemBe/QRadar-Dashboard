"""Scheduled-search catalog, versions, executions, and authorized manual run."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.routing import default_policy
from app.alerts.search_alerts import SearchAlertEvaluator
from app.api.deps import ProviderDep, SessionDep
from app.core.config import get_settings
from app.repositories.audit import record_audit
from app.schemas.search import (
    ExecutionOut,
    ScheduledSearchCreate,
    ScheduledSearchOut,
    ScheduledSearchUpdate,
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
