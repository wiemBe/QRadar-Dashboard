"""SOC overview aggregation service."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.enums import AlertStatus
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.offense import OffenseSnapshot
from app.repositories.log_source import LogSourceRepository
from app.schemas.log_source import (
    OverviewAlerts,
    OverviewCounts,
    OverviewOffenses,
    SocOverview,
)


class OverviewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.ls_repo = LogSourceRepository(session)

    async def build(self) -> SocOverview:
        instance = await self.session.scalar(select(QRadarInstance).limit(1))

        counts = await self.ls_repo.overview_counts()
        anomalous = await self.ls_repo.anomalous_source_count()
        silent = await self._silent_count()

        return SocOverview(
            instance_status=str(instance.status) if instance else "UNKNOWN",
            instance_version=instance.qradar_version if instance else None,
            generated_at=datetime.now(UTC),
            average_health_score=counts["avg_health"],
            log_sources=OverviewCounts(
                total_log_sources=counts["total"],
                monitored_log_sources=counts["monitored"],
                healthy_log_sources=counts["healthy"],
                silent_log_sources=silent,
                anomalous_log_sources=anomalous,
                in_maintenance=counts["maintenance"],
            ),
            offenses=await self._offense_overview(),
            alerts=await self._alert_overview(),
        )

    async def _silent_count(self) -> int:
        """Monitored, enabled, not in maintenance, but health says freshness is
        dead. In Phase 1 we approximate with a null/old last_event_time; the
        anomaly engine refines this in Phase 2."""
        stmt = (
            select(func.count())
            .select_from(LogSource)
            .where(
                LogSource.monitoring_enabled.is_(True),
                LogSource.maintenance_mode.is_(False),
                LogSource.enabled.is_(True),
                LogSource.last_event_time.is_(None),
            )
        )
        return await self.session.scalar(stmt) or 0

    async def _offense_overview(self) -> OverviewOffenses:
        """Built from the most recent snapshot per offense.

        In Phase 1 (mock) there may be no snapshots yet; we degrade to zeros
        rather than failing the whole overview.
        """
        latest_captured = await self.session.scalar(
            select(func.max(OffenseSnapshot.captured_at))
        )
        if latest_captured is None:
            return OverviewOffenses(active=0, critical=0, unassigned=0)

        base = select(OffenseSnapshot).where(
            OffenseSnapshot.captured_at == latest_captured,
            OffenseSnapshot.status == "OPEN",
        )
        rows = list((await self.session.scalars(base)).all())
        active = len(rows)
        critical = sum(1 for r in rows if (r.magnitude or 0) >= 7)
        unassigned = sum(1 for r in rows if not r.is_assigned)
        oldest = max((r.age_seconds or 0 for r in rows), default=None)
        return OverviewOffenses(
            active=active, critical=critical, unassigned=unassigned, oldest_age_seconds=oldest
        )

    async def _alert_overview(self) -> OverviewAlerts:
        open_count = (
            await self.session.scalar(
                select(func.count()).select_from(Alert).where(
                    Alert.status == AlertStatus.OPEN
                )
            )
            or 0
        )
        ack_count = (
            await self.session.scalar(
                select(func.count()).select_from(Alert).where(
                    Alert.status == AlertStatus.ACKNOWLEDGED
                )
            )
            or 0
        )
        sev_rows = await self.session.execute(
            select(Alert.severity, func.count())
            .where(Alert.status != AlertStatus.RESOLVED)
            .group_by(Alert.severity)
        )
        by_sev = {str(sev): cnt for sev, cnt in sev_rows}
        return OverviewAlerts(open=open_count, acknowledged=ack_count, by_severity=by_sev)
