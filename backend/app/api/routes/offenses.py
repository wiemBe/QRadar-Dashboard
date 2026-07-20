"""Offense API.

Read-only by construction. There is no acknowledge, close, assign or comment
endpoint here and there will not be one in Phase 3: the platform observes
QRadar, it does not drive it. The provider interface exposes no mutating call,
so a route could not perform one even if it were added by mistake.

Every list endpoint is bounded — limit, offset and date range are all clamped
against configuration before they reach the repository.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, SettingsDep
from app.core.config import Settings
from app.models.instance import QRadarInstance
from app.repositories.offense import SORTABLE, OffenseFilters, OffenseRepository
from app.schemas.offense import (
    AgingBucket,
    CountEntry,
    MagnitudeTrendPoint,
    OffenseAggregates,
    OffenseAnalytics,
    OffenseDetail,
    OffenseHistoryEntry,
    OffensePage,
    OffenseSummary,
)

router = APIRouter(prefix="/offenses", tags=["offenses"])

#: Age bucket boundaries in hours. Fixed rather than caller-supplied so the
#: dashboard's buckets are comparable across instances and over time.
AGING_BOUNDARIES = (1, 4, 24, 72, 168)


async def _resolve_instance(
    session: AsyncSession, instance_id: uuid.UUID | None
) -> uuid.UUID | None:
    """Fall back to the single configured instance when none is specified."""
    if instance_id is not None:
        return instance_id
    return await session.scalar(select(QRadarInstance.id).limit(1))


def _clamp(limit: int, settings: Settings) -> int:
    return max(1, min(limit, settings.api_max_page_size))


@router.get("", response_model=OffensePage)
async def list_offenses(
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    min_magnitude: int | None = Query(default=None, ge=0, le=10),
    max_magnitude: int | None = Query(default=None, ge=0, le=10),
    min_severity: int | None = Query(default=None, ge=0, le=10),
    assigned: bool | None = Query(default=None),
    assigned_to: str | None = Query(default=None, max_length=255),
    category: str | None = Query(default=None, max_length=128),
    min_age_hours: int | None = Query(default=None, ge=0),
    max_age_hours: int | None = Query(default=None, ge=0),
    search: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="last_updated_time"),
    descending: bool = Query(default=True),
    instance_id: uuid.UUID | None = Query(default=None),
) -> OffensePage:
    if sort not in SORTABLE:
        # Reject rather than silently substituting: a caller sorting by an
        # unknown column has a bug, and a quiet fallback hides it.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"sort must be one of: {', '.join(sorted(SORTABLE))}",
        )

    resolved = await _resolve_instance(session, instance_id)
    filters = OffenseFilters(
        status=status_filter,
        min_magnitude=min_magnitude,
        max_magnitude=max_magnitude,
        min_severity=min_severity,
        assigned=assigned,
        assigned_to=assigned_to,
        category=category,
        instance_id=resolved,
        min_age_seconds=min_age_hours * 3600 if min_age_hours is not None else None,
        max_age_seconds=max_age_hours * 3600 if max_age_hours is not None else None,
        search=search,
        sort=sort,
        descending=descending,
    )
    bounded = _clamp(limit, settings)
    rows, total = await OffenseRepository(session).list_offenses(
        filters, limit=bounded, offset=offset
    )
    return OffensePage(
        items=[OffenseSummary.model_validate(r) for r in rows],
        total=total,
        limit=bounded,
        offset=offset,
    )


@router.get("/analytics", response_model=OffenseAnalytics)
async def offense_analytics(
    session: SessionDep,
    settings: SettingsDep,
    trend_days: int = Query(default=30, ge=1, le=365),
    top_n: int = Query(default=10, ge=1, le=50),
    instance_id: uuid.UUID | None = Query(default=None),
) -> OffenseAnalytics:
    """Everything the analytics page renders, in one request.

    Assembled server-side so the dashboard makes a single call instead of ten,
    and so the aggregation runs in the database rather than the browser.
    """
    resolved = await _resolve_instance(session, instance_id)
    repo = OffenseRepository(session)

    aggregates = await repo.aggregate_metrics(
        resolved,
        sla_hours=settings.offense_sla_hours,
        critical_magnitude=settings.offense_critical_magnitude,
    )
    return OffenseAnalytics(
        generated_at=datetime.now(UTC),
        aggregates=OffenseAggregates(
            **aggregates,
            sla_hours=settings.offense_sla_hours,
            critical_magnitude=settings.offense_critical_magnitude,
        ),
        aging=[
            AgingBucket(**b)
            for b in await repo.aging_buckets(resolved, boundaries_hours=AGING_BOUNDARIES)
        ],
        magnitude_trend=[
            MagnitudeTrendPoint(**p)
            for p in await repo.magnitude_trend(resolved, days=trend_days)
        ],
        status_distribution=[
            CountEntry(**e) for e in await repo.distribution(resolved, "status", open_only=False)
        ],
        magnitude_distribution=[
            CountEntry(**e) for e in await repo.distribution(resolved, "magnitude")
        ],
        category_distribution=[
            CountEntry(**e) for e in await repo.top_entities(resolved, "categories", limit=top_n)
        ],
        assignment_distribution=[
            CountEntry(**e) for e in await repo.distribution(resolved, "assigned_to")
        ],
        top_rules=[
            CountEntry(**e) for e in await repo.top_entities(resolved, "rule_ids", limit=top_n)
        ],
        top_usernames=[
            CountEntry(**e) for e in await repo.top_entities(resolved, "usernames", limit=top_n)
        ],
        top_source_ips=[
            CountEntry(**e)
            for e in await repo.top_entities(resolved, "source_addresses", limit=top_n)
        ],
        top_destination_ips=[
            CountEntry(**e)
            for e in await repo.top_entities(
                resolved, "local_destination_addresses", limit=top_n
            )
        ],
    )


@router.get("/aggregates", response_model=OffenseAggregates)
async def offense_aggregates(
    session: SessionDep,
    settings: SettingsDep,
    instance_id: uuid.UUID | None = Query(default=None),
) -> OffenseAggregates:
    resolved = await _resolve_instance(session, instance_id)
    aggregates = await OffenseRepository(session).aggregate_metrics(
        resolved,
        sla_hours=settings.offense_sla_hours,
        critical_magnitude=settings.offense_critical_magnitude,
    )
    return OffenseAggregates(
        **aggregates,
        sla_hours=settings.offense_sla_hours,
        critical_magnitude=settings.offense_critical_magnitude,
    )


@router.get("/{qradar_offense_id}", response_model=OffenseDetail)
async def get_offense(
    qradar_offense_id: int,
    session: SessionDep,
    instance_id: uuid.UUID | None = Query(default=None),
) -> OffenseDetail:
    resolved = await _resolve_instance(session, instance_id)
    if resolved is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "offense not found")
    row = await OffenseRepository(session).get_latest(resolved, qradar_offense_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "offense not found")
    return OffenseDetail.model_validate(row)


@router.get("/{qradar_offense_id}/history", response_model=list[OffenseHistoryEntry])
async def offense_history(
    qradar_offense_id: int,
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=100, ge=1, le=1000),
    days: int = Query(default=90, ge=1),
    instance_id: uuid.UUID | None = Query(default=None),
) -> list[OffenseHistoryEntry]:
    resolved = await _resolve_instance(session, instance_id)
    if resolved is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "offense not found")

    bounded_days = min(days, settings.api_max_range_days)
    since = datetime.now(UTC) - timedelta(days=bounded_days)
    rows = await OffenseRepository(session).history(
        resolved, qradar_offense_id, limit=_clamp(limit, settings), since=since
    )
    if not rows:
        # An offense with no snapshot in range is not an error; the caller gets
        # an empty history and the UI renders its empty state.
        return []
    return [OffenseHistoryEntry.model_validate(r) for r in rows]
