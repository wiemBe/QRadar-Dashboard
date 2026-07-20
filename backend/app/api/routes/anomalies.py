"""Anomaly listing API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import SessionDep
from app.models.log_source import LogSourceAnomaly
from app.schemas.alert import AnomalyOut

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


@router.get("", response_model=list[AnomalyOut])
async def list_anomalies(
    session: SessionDep,
    log_source_id: uuid.UUID | None = Query(default=None),
    open_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AnomalyOut]:
    stmt = select(LogSourceAnomaly).order_by(LogSourceAnomaly.detected_at.desc()).limit(limit)
    if log_source_id is not None:
        stmt = stmt.where(LogSourceAnomaly.log_source_id == log_source_id)
    if open_only:
        stmt = stmt.where(LogSourceAnomaly.resolved_at.is_(None))
    rows = (await session.scalars(stmt)).all()
    return [AnomalyOut.model_validate(r) for r in rows]
