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


@dataclass
class ExclusionTally:
    """Why candidate buckets were dropped, for baseline completeness.

    An unexpectedly thin baseline is a support question ("why is this source
    still INSUFFICIENT_DATA after three weeks?"). Recording the reason at build
    time answers it without re-running the build against history that may have
    since aged out.
    """

    maintenance: int = 0
    off_hours: int = 0
    known_anomaly: int = 0
    incomplete: int = 0
    unfinished: int = 0

    @property
    def total(self) -> int:
        return (
            self.maintenance
            + self.off_hours
            + self.known_anomaly
            + self.incomplete
            + self.unfinished
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "maintenance": self.maintenance,
            "off_hours": self.off_hours,
            "known_anomaly": self.known_anomaly,
            "incomplete": self.incomplete,
            "unfinished": self.unfinished,
        }


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

        observations, tally, candidates = await self._load_clean_observations(
            log_source, lookback_start, now
        )

        # Group by (metric, weekday, hour). Phase A seasonality is ISO weekday
        # crossed with hour of day: 168 cells, capturing the working day and the
        # weekend without needing months of history to populate.
        cells: dict[tuple[str, int, int], list[float]] = {}
        for obs in observations:
            weekday = obs.bucket_start.isoweekday()
            hour = obs.bucket_start.hour
            for metric, value in obs.values.items():
                cells.setdefault((metric, weekday, hour), []).append(value)

        # Completeness is a property of the whole rebuild: what fraction of the
        # candidate buckets in the lookback actually survived exclusion. A cell
        # built from 4 of 28 buckets is far weaker than one built from 26 even
        # when both clear the minimum sample count.
        completeness = (len(observations) / candidates) if candidates else 0.0

        written = 0
        for (metric, weekday, hour), values in cells.items():
            await self._upsert_cell(
                log_source.id,
                metric,
                weekday,
                hour,
                values,
                lookback_start,
                now,
                completeness=completeness,
                tally=tally,
            )
            written += 1
        await self.session.flush()
        return written

    async def _load_clean_observations(
        self, log_source: LogSource, start: datetime, end: datetime
    ) -> tuple[list[Observation], ExclusionTally, int]:
        """Return usable observations, why the rest were dropped, and how many
        candidate buckets were considered."""
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
        tally = ExclusionTally()

        # The bucket containing `end` is still accumulating. Its partial count
        # would drag every cell it lands in downwards, so it never enters a
        # baseline regardless of what completeness it claims.
        observations: list[Observation] = []
        for m in rows:
            if m.bucket_end > end:
                tally.unfinished += 1
                continue
            if not m.is_complete:
                tally.incomplete += 1
                continue
            reason = self._exclusion_reason(m.bucket_start, log_source, excluded)
            if reason is not None:
                setattr(tally, reason, getattr(tally, reason) + 1)
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
        return observations, tally, len(rows)

    async def _excluded_intervals(
        self, log_source: LogSource, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Intervals to exclude: spans of any anomaly (resolved or not) for this
        source. An active anomaly must not poison the baseline used to judge it.

        A span ends when the anomaly stopped, which is not the same as when it
        was resolved. Bounding by `resolved_at` alone extends the span to the
        end of the lookback window for anything unresolved, which breaks two
        ways:

          * Deadlock. An OPEN incident then excludes the very buckets a new
            seasonal cell needs, so the detector has no baseline, returns
            INSUFFICIENT_DATA instead of a healthy verdict, and the incident can
            never recover -- it needs the baseline that its own existence
            prevents. Observed live on 2026-08-02: source 262 sat OPEN through
            seven consecutive normal buckets with `consecutive_healthy` at zero.
          * Permanent poisoning. A CANDIDATE that returns to normal before
            opening is never resolved, so `resolved_at` stays NULL forever and
            that source's buckets are excluded indefinitely.

        Buckets after `anomaly_end` are the recovery buckets -- normal by
        definition, and exactly what recovery must be judged on. An incident
        with no `anomaly_end` yet is still genuinely in progress, so it keeps
        excluding to the end of the window.
        """
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
            span_end = a.resolved_at or a.anomaly_end or end
            spans.append((a.detected_at, span_end))
        return spans

    def _exclusion_reason(
        self,
        when: datetime,
        log_source: LogSource,
        excluded: list[tuple[datetime, datetime]],
    ) -> str | None:
        """The ExclusionTally field name to charge this bucket to, or None."""
        # Maintenance window.
        if log_source.maintenance_mode and (
            log_source.maintenance_until is None or when <= log_source.maintenance_until
        ):
            return "maintenance"
        # Business-hours-only sources: off-hours intervals are expected-empty and
        # must not be baselined as if they were real observations.
        if log_source.business_hours_only and not self._in_business_hours(when, log_source):
            return "off_hours"
        # Known anomaly spans. An ongoing incident must not poison the baseline
        # it will later be judged against.
        if any(lo <= when <= hi for lo, hi in excluded):
            return "known_anomaly"
        return None

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
        *,
        completeness: float,
        tally: ExclusionTally,
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
                    completeness=completeness,
                    excluded_sample_count=tally.total,
                    exclusion_counts=tally.as_dict(),
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
            existing.completeness = completeness
            existing.excluded_sample_count = tally.total
            existing.exclusion_counts = tally.as_dict()
