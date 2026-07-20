"""Analytics rule and rule-health API.

Read-only against QRadar. The one mutating endpoint here (`PATCH /rules/{id}`)
writes SOC-owned metadata in *this platform's* database — ownership, MITRE
mapping, expected firing rate. Nothing on the update schema corresponds to a
QRadar field, so no request shape can imply enabling, disabling or editing a
rule in the SIEM.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, SettingsDep
from app.core.config import Settings
from app.models.enums import RuleHealthStatus
from app.models.instance import QRadarInstance
from app.repositories.rule import SORTABLE, RuleFilters, RuleRepository
from app.schemas.rule import (
    RuleDetail,
    RuleHealthSnapshotOut,
    RuleHealthSummaryEntry,
    RulePage,
    RuleStateTransitionOut,
    RuleSummary,
    RuleUpdate,
)

router = APIRouter(prefix="/rules", tags=["rules"])


async def _resolve_instance(
    session: AsyncSession, instance_id: uuid.UUID | None
) -> uuid.UUID | None:
    if instance_id is not None:
        return instance_id
    return await session.scalar(select(QRadarInstance.id).limit(1))


async def _page(
    session: AsyncSession,
    settings: Settings,
    *,
    filters: RuleFilters,
    limit: int,
    offset: int,
) -> RulePage:
    bounded = max(1, min(limit, settings.api_max_page_size))
    rules, total = await RuleRepository(session).list_rules(
        filters, limit=bounded, offset=offset
    )
    return RulePage(
        items=[RuleSummary.model_validate(r) for r in rules],
        total=total,
        limit=bounded,
        offset=offset,
    )


@router.get("", response_model=RulePage)
async def list_rules(
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    enabled: bool | None = Query(default=None),
    health_status: RuleHealthStatus | None = Query(default=None),
    include_building_blocks: bool = Query(default=False),
    origin: str | None = Query(default=None, max_length=64),
    owner: str | None = Query(default=None, max_length=255),
    technique: str | None = Query(default=None, max_length=32),
    search: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="name"),
    descending: bool = Query(default=False),
    instance_id: uuid.UUID | None = Query(default=None),
) -> RulePage:
    if sort not in SORTABLE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"sort must be one of: {', '.join(sorted(SORTABLE))}",
        )
    filters = RuleFilters(
        instance_id=await _resolve_instance(session, instance_id),
        enabled=enabled,
        health_status=health_status,
        # None means "both"; the repository default excludes building blocks.
        is_building_block=None if include_building_blocks else False,
        origin=origin,
        owner=owner,
        technique=technique,
        search=search,
        sort=sort,
        descending=descending,
    )
    return await _page(session, settings, filters=filters, limit=limit, offset=offset)


@router.get("/health-summary", response_model=list[RuleHealthSummaryEntry])
async def health_summary(
    session: SessionDep,
    instance_id: uuid.UUID | None = Query(default=None),
) -> list[RuleHealthSummaryEntry]:
    resolved = await _resolve_instance(session, instance_id)
    rows = await RuleRepository(session).health_summary(resolved)
    return [RuleHealthSummaryEntry(**r) for r in rows]


def _health_view(
    name: str, health: RuleHealthStatus, description: str
) -> Callable[..., Awaitable[RulePage]]:
    """Register one canned rule-health view.

    Inactive/noisy/disabled/degraded are the four lists an operator actually
    works from, so each gets a stable URL rather than requiring the caller to
    know the right filter combination.
    """

    @router.get(f"/{name}", response_model=RulePage, name=f"rules_{name}", summary=description)
    async def _view(
        session: SessionDep,
        settings: SettingsDep,
        limit: int = Query(default=50, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        instance_id: uuid.UUID | None = Query(default=None),
    ) -> RulePage:
        filters = RuleFilters(
            instance_id=await _resolve_instance(session, instance_id),
            health_status=health,
            is_building_block=False,
            sort="name",
        )
        return await _page(session, settings, filters=filters, limit=limit, offset=offset)

    return _view


_health_view("inactive", RuleHealthStatus.INACTIVE, "Rules that have stopped firing")
_health_view("noisy", RuleHealthStatus.NOISY, "Rules firing above expectation")
_health_view("disabled", RuleHealthStatus.DISABLED, "Rules disabled in QRadar")
_health_view(
    "dependency-degraded",
    RuleHealthStatus.DEPENDENCY_DEGRADED,
    "Rules whose required telemetry is unavailable",
)
_health_view(
    "never-observed",
    RuleHealthStatus.NEVER_OBSERVED,
    "Enabled rules that have never been seen firing",
)


@router.get("/{rule_id}", response_model=RuleDetail)
async def get_rule(rule_id: uuid.UUID, session: SessionDep) -> RuleDetail:
    rule = await RuleRepository(session).get(rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    return RuleDetail.model_validate(rule)


@router.get("/{rule_id}/health-history", response_model=list[RuleHealthSnapshotOut])
async def rule_health_history(
    rule_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=100, ge=1, le=1000),
    days: int = Query(default=90, ge=1),
) -> list[RuleHealthSnapshotOut]:
    repo = RuleRepository(session)
    if await repo.get(rule_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    bounded_days = min(days, settings.api_max_range_days)
    since = datetime.now(UTC) - timedelta(days=bounded_days)
    rows = await repo.health_history(
        rule_id, limit=max(1, min(limit, settings.api_max_page_size)), since=since
    )
    return [RuleHealthSnapshotOut.model_validate(r) for r in rows]


@router.get("/{rule_id}/transitions", response_model=list[RuleStateTransitionOut])
async def rule_transitions(
    rule_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[RuleStateTransitionOut]:
    repo = RuleRepository(session)
    if await repo.get(rule_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    rows = await repo.transitions(rule_id, limit=max(1, min(limit, settings.api_max_page_size)))
    return [RuleStateTransitionOut.model_validate(r) for r in rows]


@router.patch("/{rule_id}", response_model=RuleDetail)
async def update_rule_metadata(
    rule_id: uuid.UUID, payload: RuleUpdate, session: SessionDep
) -> RuleDetail:
    """Update SOC-owned metadata. QRadar is never written to."""
    repo = RuleRepository(session)
    rule = await repo.get(rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await session.flush()
    return RuleDetail.model_validate(rule)
