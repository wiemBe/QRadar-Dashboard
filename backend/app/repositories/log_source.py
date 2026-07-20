"""Data access for log sources. Keeps SQL out of the route handlers."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.log_source import LogSource, LogSourceAnomaly


class LogSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(
        self,
        *,
        instance_id: uuid.UUID | None = None,
        monitored_only: bool = False,
    ) -> list[LogSource]:
        stmt = select(LogSource).order_by(LogSource.name)
        if instance_id is not None:
            stmt = stmt.where(LogSource.instance_id == instance_id)
        if monitored_only:
            stmt = stmt.where(LogSource.monitoring_enabled.is_(True))
        return list((await self.session.scalars(stmt)).all())

    async def get(self, log_source_id: uuid.UUID) -> LogSource | None:
        return await self.session.get(LogSource, log_source_id)

    async def get_by_qradar_id(
        self, instance_id: uuid.UUID, qradar_id: int
    ) -> LogSource | None:
        stmt = select(LogSource).where(
            LogSource.instance_id == instance_id, LogSource.qradar_id == qradar_id
        )
        return await self.session.scalar(stmt)

    async def open_anomaly_counts(self) -> dict[uuid.UUID, int]:
        """Map of log_source_id -> count of unresolved, unsuppressed anomalies."""
        stmt = (
            select(LogSourceAnomaly.log_source_id, func.count())
            .where(
                LogSourceAnomaly.resolved_at.is_(None),
                LogSourceAnomaly.suppressed.is_(False),
            )
            .group_by(LogSourceAnomaly.log_source_id)
        )
        rows = await self.session.execute(stmt)
        return {row[0]: row[1] for row in rows}

    async def open_anomalies_for(
        self, log_source_id: uuid.UUID
    ) -> list[LogSourceAnomaly]:
        stmt = (
            select(LogSourceAnomaly)
            .where(
                LogSourceAnomaly.log_source_id == log_source_id,
                LogSourceAnomaly.resolved_at.is_(None),
            )
            .order_by(LogSourceAnomaly.detected_at.desc())
        )
        return list((await self.session.scalars(stmt)).all())

    # -- aggregate counts for the SOC overview ------------------------------
    async def overview_counts(self) -> dict[str, int]:
        total = await self.session.scalar(select(func.count()).select_from(LogSource)) or 0
        monitored = (
            await self.session.scalar(
                select(func.count())
                .select_from(LogSource)
                .where(LogSource.monitoring_enabled.is_(True))
            )
            or 0
        )
        maintenance = (
            await self.session.scalar(
                select(func.count())
                .select_from(LogSource)
                .where(LogSource.maintenance_mode.is_(True))
            )
            or 0
        )
        healthy = (
            await self.session.scalar(
                select(func.count())
                .select_from(LogSource)
                .where(LogSource.health_score >= 70)
            )
            or 0
        )
        avg_health = await self.session.scalar(
            select(func.avg(LogSource.health_score)).where(
                LogSource.monitoring_enabled.is_(True)
            )
        )
        return {
            "total": total,
            "monitored": monitored,
            "maintenance": maintenance,
            "healthy": healthy,
            "avg_health": round(float(avg_health), 1) if avg_health is not None else None,
        }

    async def anomalous_source_count(self) -> int:
        stmt = select(func.count(func.distinct(LogSourceAnomaly.log_source_id))).where(
            LogSourceAnomaly.resolved_at.is_(None),
            LogSourceAnomaly.suppressed.is_(False),
        )
        return await self.session.scalar(stmt) or 0
