"""Phase A behavioral anomaly and investigation API.

Every endpoint is read-only, paginated, and range-bounded. There is deliberately
no QRadar mutation surface here and none may be added: the platform's
integration with QRadar is read-only by design, not by omission.

Nothing in these responses carries provider credentials, SEC headers or raw
event payloads. The explanation package exposes AQL text as provenance, which
is query structure rather than data, and is what makes a contributor claim
auditable rather than something the UI must take on faith.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import SessionDep, SettingsDep
from app.models.enums import AnomalyState, AnomalyType, Severity
from app.models.log_source import LogSource
from app.repositories.anomaly import AnomalyRepository
from app.schemas.anomaly import (
    AnomalyDetailOut,
    AnomalyListItem,
    AnomalyTransitionOut,
    BaselineCellOut,
    BehaviorSummaryOut,
    ContributorOut,
    ExplanationDimensionOut,
    ExplanationOut,
    MetricBucketOut,
    PagedAnomalies,
    SourceBehaviorOut,
)

router = APIRouter(prefix="/anomalies", tags=["anomalies"])
behavior_router = APIRouter(prefix="/behavior", tags=["behavior"])


def _validate_range(
    since: datetime | None, until: datetime | None, max_days: int
) -> tuple[datetime, datetime]:
    """Clamp and validate a caller-supplied time range.

    An unbounded range on a hypertable is a denial-of-service against our own
    database, so the ceiling is enforced here rather than trusted to the client.
    """
    now = datetime.now(UTC)
    end = until or now
    start = since or (end - timedelta(days=1))
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    if end <= start:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "range end must be after its start"
        )
    if (end - start) > timedelta(days=max_days):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"range exceeds the maximum of {max_days} days",
        )
    return start, end


# --------------------------------------------------------------------- list
@router.get("", response_model=PagedAnomalies)
async def list_anomalies(
    session: SessionDep,
    settings: SettingsDep,
    log_source_id: uuid.UUID | None = Query(default=None),
    anomaly_type: AnomalyType | None = Query(default=None),
    state: AnomalyState | None = Query(default=None),
    severity: Severity | None = Query(default=None),
    active_only: bool = Query(default=False),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PagedAnomalies:
    start, end = _validate_range(since, until, settings.api_max_range_days)
    repo = AnomalyRepository(session)
    rows, total = await repo.list_anomalies(
        limit=min(limit, settings.api_max_page_size),
        offset=offset,
        log_source_id=log_source_id,
        anomaly_type=anomaly_type,
        state=state,
        severity=severity,
        active_only=active_only,
        since=start,
        until=end,
    )
    return PagedAnomalies(
        items=[AnomalyListItem.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/summary", response_model=BehaviorSummaryOut)
async def anomaly_summary(session: SessionDep) -> BehaviorSummaryOut:
    repo = AnomalyRepository(session)
    counts = await repo.state_counts()
    sources = await repo.monitored_sources()

    def total(state: AnomalyState, atype: AnomalyType | None = None) -> int:
        if atype is not None:
            return counts.get((state.value, atype.value), 0)
        return sum(v for (s, _), v in counts.items() if s == state.value)

    active = (AnomalyState.OPEN, AnomalyState.CANDIDATE, AnomalyState.RECOVERING)
    open_total = sum(total(s) for s in active)

    # A source with no adequate baseline is counted separately from a healthy
    # one: "we cannot yet judge this source" is an observability gap, and
    # folding it into the healthy count would hide it.
    insufficient = 0
    for src in sources:
        cell = await repo.baseline_cell(
            src.id,
            weekday=datetime.now(UTC).isoweekday(),
            hour=datetime.now(UTC).hour,
            metric="average_eps",
        )
        if cell is None or not cell.is_reliable:
            insufficient += 1

    return BehaviorSummaryOut(
        open_anomalies=open_total,
        spikes=sum(total(s, AnomalyType.VOLUME_SPIKE) for s in active),
        drops=sum(total(s, AnomalyType.VOLUME_DROP) for s in active),
        silent_sources=sum(total(s, AnomalyType.NO_EVENTS) for s in active),
        candidates=total(AnomalyState.CANDIDATE),
        recovering=total(AnomalyState.RECOVERING),
        insufficient_data_sources=insufficient,
        monitored_sources=len(sources),
        recently_resolved=[
            AnomalyListItem.model_validate(a) for a in await repo.recently_resolved()
        ],
        highest_deviation=[
            AnomalyListItem.model_validate(a) for a in await repo.highest_deviation()
        ],
    )


@router.get("/{anomaly_id}", response_model=AnomalyDetailOut)
async def get_anomaly(anomaly_id: uuid.UUID, session: SessionDep) -> AnomalyDetailOut:
    repo = AnomalyRepository(session)
    anomaly = await repo.get_detail(anomaly_id)
    if anomaly is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "anomaly not found")

    detail = AnomalyDetailOut.model_validate(anomaly)
    source = await session.get(LogSource, anomaly.log_source_id)
    detail.log_source_name = source.name if source else None
    detail.transitions = [
        AnomalyTransitionOut.model_validate(t) for t in anomaly.transitions
    ]
    detail.explanation_package = _explanation_out(anomaly.explanation_package)
    return detail


def _explanation_out(package) -> ExplanationOut | None:
    """Assemble the evidence package, grouping contributors under dimensions.

    Contributors are stored flat and joined here rather than nested in storage,
    so they stay queryable across anomalies. Every requested dimension appears
    even when it has no contributors — an UNAVAILABLE dimension the UI cannot
    see is a dimension the operator will assume was clean.
    """
    if package is None:
        return None
    by_dimension: dict[str, list[ContributorOut]] = {}
    for c in package.contributors:
        by_dimension.setdefault(c.dimension, []).append(ContributorOut.model_validate(c))
    for rows in by_dimension.values():
        rows.sort(key=lambda r: r.rank)

    out = ExplanationOut.model_validate(package)
    out.dimensions = []
    for dim in package.dimensions:
        item = ExplanationDimensionOut.model_validate(dim)
        item.contributors = by_dimension.get(dim.dimension, [])
        out.dimensions.append(item)
    out.dimensions.sort(key=lambda d: d.dimension)
    return out


# ----------------------------------------------------------------- behavior
@behavior_router.get("/sources", response_model=list[SourceBehaviorOut])
async def list_source_behavior(session: SessionDep) -> list[SourceBehaviorOut]:
    """Observed vs expected volume for every monitored source."""
    repo = AnomalyRepository(session)
    sources = await repo.monitored_sources()
    active_counts = await repo.active_counts_by_source()

    out: list[SourceBehaviorOut] = []
    for src in sources:
        bucket = await repo.latest_bucket(src.id)
        cell = None
        if bucket is not None:
            cell = await repo.baseline_cell(
                src.id,
                weekday=bucket.bucket_start.isoweekday(),
                hour=bucket.bucket_start.hour,
                metric="average_eps",
            )
        out.append(_source_behavior(src, bucket, cell, active_counts.get(src.id, 0)))
    return out


def _source_behavior(src, bucket, cell, active_count: int) -> SourceBehaviorOut:
    observed = bucket.average_eps if bucket is not None else None
    expected = cell.median if (cell is not None and cell.is_reliable) else None
    # A ratio against a zero expectation does not exist. Reporting one would
    # turn "the baseline expects nothing here" into a spurious huge number.
    ratio = (
        observed / expected
        if (observed is not None and expected not in (None, 0))
        else None
    )
    if active_count > 0:
        state = AnomalyState.OPEN
    elif cell is None or not cell.is_reliable:
        state = AnomalyState.INSUFFICIENT_DATA
    else:
        state = AnomalyState.NORMAL

    return SourceBehaviorOut(
        log_source_id=src.id,
        name=src.name,
        criticality=str(src.criticality),
        observed_eps=observed,
        expected_eps=expected,
        expected_low=cell.p05 if cell is not None else None,
        expected_high=cell.p95 if cell is not None else None,
        deviation_ratio=ratio,
        state=state,
        baseline_sample_count=cell.sample_count if cell is not None else 0,
        baseline_completeness=cell.completeness if cell is not None else 0.0,
        last_bucket_at=bucket.bucket_start if bucket is not None else None,
        last_event_at=src.last_event_time,
        open_anomaly_count=active_count,
    )


@behavior_router.get(
    "/sources/{log_source_id}/metrics", response_model=list[MetricBucketOut]
)
async def source_metrics(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[MetricBucketOut]:
    if await session.get(LogSource, log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    start, end = _validate_range(since, until, settings.api_max_range_days)
    repo = AnomalyRepository(session)
    rows = await repo.metric_history(
        log_source_id, since=start, until=end, limit=limit
    )
    return [MetricBucketOut.model_validate(r) for r in rows]


@behavior_router.get(
    "/sources/{log_source_id}/baselines", response_model=list[BaselineCellOut]
)
async def source_baselines(
    log_source_id: uuid.UUID,
    session: SessionDep,
    metric: str = Query(default="average_eps", max_length=64),
) -> list[BaselineCellOut]:
    if await session.get(LogSource, log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    repo = AnomalyRepository(session)
    rows = await repo.baselines_for(log_source_id, metric=metric)
    return [BaselineCellOut.model_validate(r) for r in rows]


@behavior_router.get("/sources/{log_source_id}", response_model=SourceBehaviorOut)
async def source_behavior(
    log_source_id: uuid.UUID, session: SessionDep
) -> SourceBehaviorOut:
    src = await session.get(LogSource, log_source_id)
    if src is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    repo = AnomalyRepository(session)
    bucket = await repo.latest_bucket(log_source_id)
    cell = None
    if bucket is not None:
        cell = await repo.baseline_cell(
            log_source_id,
            weekday=bucket.bucket_start.isoweekday(),
            hour=bucket.bucket_start.hour,
            metric="average_eps",
        )
    counts = await repo.active_counts_by_source()
    return _source_behavior(src, bucket, cell, counts.get(log_source_id, 0))

