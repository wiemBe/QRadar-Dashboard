"""Baseline computation.

Groups a log source's historical observations of a metric by (weekday, hour) and
computes a robust (median, MAD) baseline per cell. Design points required by the
spec:

  * Group by weekday and hour (same weekday+hour across weeks are comparable).
  * Median + MAD; MAD = 0 handled by app.anomaly.statistics.effective_scale.
  * Configurable minimum sample count; below it the cell is stored but marked
    not-reliable and must not drive alerts.
  * Exclude maintenance windows and intervals overlapping an active/known
    anomaly, so an ongoing incident does not poison the baseline it will later
    be judged against.
  * Store baseline version and the observations used.
  * Runs asynchronously (invoked from a Celery task), never inline with
    detection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly import statistics as rstats
from app.core.config import Settings, get_settings
from app.models.log_source import (
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceMetric,
)

# Which metric columns get baselines. These are the ones anomaly detection
# compares against a historical norm.
BASELINE_METRICS = ("average_eps", "event_count")


@dataclass
class Observation:
    bucket_start: datetime
    values: dict[str, float]


class BaselineBuilder:
    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    async def rebuild_for_source(
        self, log_source: LogSource, *, now: datetime | None = None
    ) -> int:
        """Recompute all baseline cells for one source. Returns cells written."""
        now = now or datetime.now(UTC)
        lookback_start = now - timedelta(days=self.settings.baseline_lookback_days)

        observations = await self._load_clean_observations(log_source, lookback_start, now)

        # Group by (metric, weekday, hour).
        cells: dict[tuple[str, int, int], list[float]] = {}
        for obs in observations:
            weekday = obs.bucket_start.isoweekday()
            hour = obs.bucket_start.hour
            for metric, value in obs.values.items():
                cells.setdefault((metric, weekday, hour), []).append(value)

        written = 0
        for (metric, weekday, hour), values in cells.items():
            await self._upsert_cell(
                log_source.id, metric, weekday, hour, values, lookback_start, now
            )
            written += 1
        await self.session.flush()
        return written

    async def _load_clean_observations(
        self, log_source: LogSource, start: datetime, end: datetime
    ) -> list[Observation]:
        rows = list(
            (
                await self.session.scalars(
                    select(LogSourceMetric)
                    .where(
                        LogSourceMetric.log_source_id == log_source.id,
                        LogSourceMetric.bucket_start >= start,
                        LogSourceMetric.bucket_start < end,
                    )
                    .order_by(LogSourceMetric.bucket_start)
                )
            ).all()
        )

        excluded = await self._excluded_intervals(log_source, start, end)

        observations: list[Observation] = []
        for m in rows:
            if self._is_excluded(m.bucket_start, log_source, excluded):
                continue
            observations.append(
                Observation(
                    bucket_start=m.bucket_start,
                    values={
                        "average_eps": float(m.average_eps),
                        "event_count": float(m.event_count),
                    },
                )
            )
        return observations

    async def _excluded_intervals(
        self, log_source: LogSource, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Intervals to exclude: spans of any anomaly (resolved or not) for this
        source. An active anomaly must not poison the baseline used to judge it."""
        anomalies = list(
            (
                await self.session.scalars(
                    select(LogSourceAnomaly).where(
                        LogSourceAnomaly.log_source_id == log_source.id,
                        LogSourceAnomaly.detected_at < end,
                    )
                )
            ).all()
        )
        spans: list[tuple[datetime, datetime]] = []
        for a in anomalies:
            span_end = a.resolved_at or end
            spans.append((a.detected_at, span_end))
        return spans

    def _is_excluded(
        self,
        when: datetime,
        log_source: LogSource,
        excluded: list[tuple[datetime, datetime]],
    ) -> bool:
        # Maintenance window.
        if log_source.maintenance_mode and (
            log_source.maintenance_until is None or when <= log_source.maintenance_until
        ):
            return True
        # Business-hours-only sources: off-hours intervals are expected-empty and
        # must not be baselined as if they were real observations.
        if log_source.business_hours_only and not self._in_business_hours(when, log_source):
            return True
        # Known anomaly spans.
        return any(lo <= when <= hi for lo, hi in excluded)

    def _in_business_hours(self, when: datetime, log_source: LogSource) -> bool:
        weekday = when.isoweekday()
        if weekday not in (log_source.business_days or [1, 2, 3, 4, 5]):
            return False
        return log_source.business_hours_start <= when.hour < log_source.business_hours_end

    async def _upsert_cell(
        self,
        log_source_id: uuid.UUID,
        metric: str,
        weekday: int,
        hour: int,
        values: list[float],
        window_start: datetime,
        window_end: datetime,
    ) -> None:
        med = rstats.median(values)
        mad_value = rstats.mad(values, med)
        p05 = rstats.percentile(values, 5)
        p95 = rstats.percentile(values, 95)
        reliable = len(values) >= self.settings.baseline_min_samples
        # Bound the stored observation sample so a hot cell doesn't bloat the row.
        stored_obs = [round(v, 4) for v in values[-200:]]

        existing = await self.session.scalar(
            select(LogSourceBaseline).where(
                LogSourceBaseline.log_source_id == log_source_id,
                LogSourceBaseline.metric_name == metric,
                LogSourceBaseline.weekday == weekday,
                LogSourceBaseline.hour == hour,
            )
        )
        if existing is None:
            self.session.add(
                LogSourceBaseline(
                    log_source_id=log_source_id,
                    metric_name=metric,
                    weekday=weekday,
                    hour=hour,
                    median=med,
                    mad=mad_value,
                    p05=p05,
                    p95=p95,
                    sample_count=len(values),
                    is_reliable=reliable,
                    window_start=window_start,
                    window_end=window_end,
                    computed_at=window_end,
                    baseline_version=1,
                    observations=stored_obs,
                )
            )
        else:
            existing.median = med
            existing.mad = mad_value
            existing.p05 = p05
            existing.p95 = p95
            existing.sample_count = len(values)
            existing.is_reliable = reliable
            existing.window_start = window_start
            existing.window_end = window_end
            existing.computed_at = window_end
            existing.baseline_version += 1
            existing.observations = stored_obs
