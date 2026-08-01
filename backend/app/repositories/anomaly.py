"""Query helpers for Phase A behavioral analytics.

Keeps the route handlers free of query construction, and keeps the pagination
and range guards in one place where they cannot be forgotten on a new endpoint.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AnomalyState,
    AnomalyType,
    Severity,
)
from app.models.explanation import AnomalyExplanation
from app.models.log_source import (
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceMetric,
)


class AnomalyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ list
    def _filtered(
        self,
        *,
        log_source_id: uuid.UUID | None = None,
        anomaly_type: AnomalyType | None = None,
        state: AnomalyState | None = None,
        severity: Severity | None = None,
        active_only: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Select:
        stmt = select(LogSourceAnomaly)
        if log_source_id is not None:
            stmt = stmt.where(LogSourceAnomaly.log_source_id == log_source_id)
        if anomaly_type is not None:
            stmt = stmt.where(LogSourceAnomaly.anomaly_type == anomaly_type)
        if state is not None:
            stmt = stmt.where(LogSourceAnomaly.state == state)
        if severity is not None:
            stmt = stmt.where(LogSourceAnomaly.severity == severity)
        if active_only:
            stmt = stmt.where(
                LogSourceAnomaly.state.in_([s.value for s in ACTIVE_ANOMALY_STATES])
            )
        if since is not None:
            stmt = stmt.where(LogSourceAnomaly.detected_at >= since)
        if until is not None:
            stmt = stmt.where(LogSourceAnomaly.detected_at < until)
        return stmt

    async def list_anomalies(
        self, *, limit: int, offset: int, **filters
    ) -> tuple[list[LogSourceAnomaly], int]:
        stmt = self._filtered(**filters)
        total = await self.session.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
        rows = list(
            (
                await self.session.scalars(
                    stmt.order_by(LogSourceAnomaly.detected_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        return rows, int(total or 0)

    async def get_detail(self, anomaly_id: uuid.UUID) -> LogSourceAnomaly | None:
        """One anomaly with its lifecycle history and evidence package.

        Eager-loaded: the investigation page needs all three, and lazy loading
        them would issue a query per contributor row on an async session that
        does not permit implicit IO.
        """
        return await self.session.scalar(
            select(LogSourceAnomaly)
            .where(LogSourceAnomaly.id == anomaly_id)
            .options(
                selectinload(LogSourceAnomaly.transitions),
                selectinload(LogSourceAnomaly.explanation_package).selectinload(
                    AnomalyExplanation.dimensions
                ),
                selectinload(LogSourceAnomaly.explanation_package).selectinload(
                    AnomalyExplanation.contributors
                ),
            )
        )

    # --------------------------------------------------------------- summary
    async def state_counts(self) -> dict[tuple[str, str], int]:
        """Anomaly counts keyed by (state, anomaly_type)."""
        rows = await self.session.execute(
            select(
                LogSourceAnomaly.state,
                LogSourceAnomaly.anomaly_type,
                func.count(),
            ).group_by(LogSourceAnomaly.state, LogSourceAnomaly.anomaly_type)
        )
        return {(str(s), str(t)): int(c) for s, t, c in rows.all()}

    async def recently_resolved(self, *, limit: int = 10) -> list[LogSourceAnomaly]:
        return list(
            (
                await self.session.scalars(
                    select(LogSourceAnomaly)
                    .where(LogSourceAnomaly.state == AnomalyState.RESOLVED)
                    .where(LogSourceAnomaly.resolved_at.is_not(None))
                    .order_by(LogSourceAnomaly.resolved_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def highest_deviation(self, *, limit: int = 10) -> list[LogSourceAnomaly]:
        """Active anomalies ranked by how far they departed from expectation.

        Ordered on deviation_ratio with NULLs last: an anomaly whose expected
        value was zero has no ratio, and sorting it as if it were zero would put
        the least-quantified incidents at the top.
        """
        return list(
            (
                await self.session.scalars(
                    select(LogSourceAnomaly)
                    .where(
                        LogSourceAnomaly.state.in_(
                            [s.value for s in ACTIVE_ANOMALY_STATES]
                        )
                    )
                    .order_by(LogSourceAnomaly.deviation_ratio.desc().nullslast())
                    .limit(limit)
                )
            ).all()
        )

    # -------------------------------------------------------------- behavior
    async def latest_bucket(self, log_source_id: uuid.UUID) -> LogSourceMetric | None:
        return await self.session.scalar(
            select(LogSourceMetric)
            .where(LogSourceMetric.log_source_id == log_source_id)
            .order_by(LogSourceMetric.bucket_start.desc())
            .limit(1)
        )

    async def metric_history(
        self,
        log_source_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> list[LogSourceMetric]:
        return list(
            (
                await self.session.scalars(
                    select(LogSourceMetric)
                    .where(
                        LogSourceMetric.log_source_id == log_source_id,
                        LogSourceMetric.bucket_start >= since,
                        LogSourceMetric.bucket_start < until,
                    )
                    .order_by(LogSourceMetric.bucket_start)
                    .limit(limit)
                )
            ).all()
        )

    async def baseline_cell(
        self, log_source_id: uuid.UUID, *, weekday: int, hour: int, metric: str
    ) -> LogSourceBaseline | None:
        return await self.session.scalar(
            select(LogSourceBaseline).where(
                LogSourceBaseline.log_source_id == log_source_id,
                LogSourceBaseline.weekday == weekday,
                LogSourceBaseline.hour == hour,
                LogSourceBaseline.metric_name == metric,
            )
        )

    async def baselines_for(
        self, log_source_id: uuid.UUID, *, metric: str
    ) -> list[LogSourceBaseline]:
        return list(
            (
                await self.session.scalars(
                    select(LogSourceBaseline)
                    .where(
                        LogSourceBaseline.log_source_id == log_source_id,
                        LogSourceBaseline.metric_name == metric,
                    )
                    .order_by(LogSourceBaseline.weekday, LogSourceBaseline.hour)
                )
            ).all()
        )

    async def monitored_sources(self) -> list[LogSource]:
        return list(
            (
                await self.session.scalars(
                    select(LogSource)
                    .where(LogSource.monitoring_enabled.is_(True))
                    .order_by(LogSource.name)
                )
            ).all()
        )

    async def active_counts_by_source(self) -> dict[uuid.UUID, int]:
        rows = await self.session.execute(
            select(LogSourceAnomaly.log_source_id, func.count())
            .where(
                LogSourceAnomaly.state.in_([s.value for s in ACTIVE_ANOMALY_STATES])
            )
            .group_by(LogSourceAnomaly.log_source_id)
        )
        return {sid: int(count) for sid, count in rows.all()}


def default_window(days: int = 1) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now - timedelta(days=days), now
