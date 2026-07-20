"""Detection-coverage API.

Coverage is reported with its evidence attached. A technique's status is never
returned as a bare label: the response carries the rule -> health -> dependency
-> telemetry chain that produced it, and keeps explicit mappings distinguishable
from inferred ones, so nobody has to trust the number on faith.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, SettingsDep
from app.models.enums import CoverageStatus
from app.models.instance import QRadarInstance
from app.models.rule import AnalyticsRule, TechniqueMapping
from app.repositories.coverage import CoverageRepository
from app.schemas.coverage import (
    CoveragePage,
    CoverageSnapshotOut,
    CoverageSummary,
    TechniqueCoverage,
    TechniqueCoverageDetail,
    TechniqueMappingIn,
    TechniqueMappingOut,
)

router = APIRouter(prefix="/coverage", tags=["coverage"])


async def _resolve_instance(
    session: AsyncSession, instance_id: uuid.UUID | None
) -> uuid.UUID | None:
    if instance_id is not None:
        return instance_id
    return await session.scalar(select(QRadarInstance.id).limit(1))


@router.get("/summary", response_model=CoverageSummary)
async def coverage_summary(
    session: SessionDep,
    instance_id: uuid.UUID | None = Query(default=None),
) -> CoverageSummary:
    resolved = await _resolve_instance(session, instance_id)
    repo = CoverageRepository(session)
    summary = await repo.summary(resolved)
    return CoverageSummary(
        generated_at=datetime.now(UTC),
        **summary,
        mapping_provenance=await repo.mapping_provenance_summary(resolved),
    )


@router.get("/techniques", response_model=CoveragePage)
async def list_techniques(
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    status_filter: CoverageStatus | None = Query(default=None, alias="status"),
    tactic: str | None = Query(default=None, max_length=128),
    instance_id: uuid.UUID | None = Query(default=None),
) -> CoveragePage:
    resolved = await _resolve_instance(session, instance_id)
    bounded = max(1, min(limit, settings.api_max_page_size))
    rows, total = await CoverageRepository(session).list_techniques(
        instance_id=resolved,
        status=status_filter,
        tactic=tactic,
        limit=bounded,
        offset=offset,
    )
    return CoveragePage(
        items=[TechniqueCoverage.model_validate(r) for r in rows],
        total=total,
        limit=bounded,
        offset=offset,
    )


@router.get("/degraded", response_model=CoveragePage)
async def degraded_coverage(
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    instance_id: uuid.UUID | None = Query(default=None),
) -> CoveragePage:
    return await list_techniques(
        session=session,
        settings=settings,
        limit=limit,
        offset=offset,
        status_filter=CoverageStatus.DEGRADED,
        tactic=None,
        instance_id=instance_id,
    )


@router.get("/missing", response_model=CoveragePage)
async def missing_coverage(
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    instance_id: uuid.UUID | None = Query(default=None),
) -> CoveragePage:
    return await list_techniques(
        session=session,
        settings=settings,
        limit=limit,
        offset=offset,
        status_filter=CoverageStatus.MISSING,
        tactic=None,
        instance_id=instance_id,
    )


@router.get("/by-rule")
async def coverage_by_rule(
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=200, ge=1, le=1000),
    instance_id: uuid.UUID | None = Query(default=None),
) -> list[dict]:
    resolved = await _resolve_instance(session, instance_id)
    return await CoverageRepository(session).rule_centric(
        resolved, limit=min(limit, settings.api_max_page_size * 5)
    )


@router.get("/by-data-source")
async def coverage_by_data_source(
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=200, ge=1, le=1000),
    instance_id: uuid.UUID | None = Query(default=None),
) -> list[dict]:
    """Which detections rest on each log source — the blast radius of a gap."""
    resolved = await _resolve_instance(session, instance_id)
    return await CoverageRepository(session).data_source_centric(
        resolved, limit=min(limit, settings.api_max_page_size * 5)
    )


@router.get("/techniques/{technique_id}", response_model=TechniqueCoverageDetail)
async def get_technique(
    technique_id: str,
    session: SessionDep,
    instance_id: uuid.UUID | None = Query(default=None),
) -> TechniqueCoverageDetail:
    resolved = await _resolve_instance(session, instance_id)
    if resolved is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "technique not found")
    row = await CoverageRepository(session).get_technique(resolved, technique_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "technique not found")
    return TechniqueCoverageDetail.model_validate(row)


@router.get(
    "/techniques/{technique_id}/history", response_model=list[CoverageSnapshotOut]
)
async def technique_history(
    technique_id: str,
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=200, ge=1, le=1000),
    days: int = Query(default=90, ge=1),
    instance_id: uuid.UUID | None = Query(default=None),
) -> list[CoverageSnapshotOut]:
    resolved = await _resolve_instance(session, instance_id)
    if resolved is None:
        return []
    bounded_days = min(days, settings.api_max_range_days)
    rows = await CoverageRepository(session).technique_history(
        resolved,
        technique_id,
        limit=max(1, min(limit, settings.api_max_page_size * 5)),
        since=datetime.now(UTC) - timedelta(days=bounded_days),
    )
    return [CoverageSnapshotOut.model_validate(r) for r in rows]


@router.post(
    "/mappings", response_model=TechniqueMappingOut, status_code=status.HTTP_201_CREATED
)
async def create_mapping(
    payload: TechniqueMappingIn,
    session: SessionDep,
    instance_id: uuid.UUID | None = Query(default=None),
) -> TechniqueMappingOut:
    """Record a SOC-owned technique mapping.

    Writes only to this platform's database. The MITRE identifier and any
    descriptive text come from the caller — the platform imports no MITRE
    content of its own.
    """
    resolved = await _resolve_instance(session, instance_id)
    if resolved is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no QRadar instance configured")

    rule = await session.scalar(
        select(AnalyticsRule).where(
            AnalyticsRule.id == payload.rule_id,
            AnalyticsRule.instance_id == resolved,
        )
    )
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found for this instance")

    existing = await session.scalar(
        select(TechniqueMapping).where(
            TechniqueMapping.instance_id == resolved,
            TechniqueMapping.technique_id == payload.technique_id,
            TechniqueMapping.rule_id == payload.rule_id,
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "mapping already exists")

    mapping = TechniqueMapping(
        instance_id=resolved,
        technique_id=payload.technique_id,
        technique_name=payload.technique_name,
        tactic=payload.tactic,
        rule_id=payload.rule_id,
        source=payload.source,
        confidence=payload.confidence,
        provenance=payload.provenance,
    )
    session.add(mapping)
    await session.flush()
    return TechniqueMappingOut.model_validate(mapping)
