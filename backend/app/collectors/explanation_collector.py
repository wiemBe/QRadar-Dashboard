"""Bounded explanation evidence collection for an OPEN volume anomaly.

For one anomaly it:
  1. Derives the anomaly window and a recent-normal comparison window.
  2. Runs one bounded aggregate Ariel query per dimension, per window.
  3. Compares them and persists a typed contributor/dimension package.

Guarantees:
  * Bounded — both windows are clamped, and every dimension result is capped.
  * Read-only — aggregate counts only; no raw event or payload is retrieved.
  * Never blocking detection — this runs from its own Celery task, so an
    unresponsive appliance delays evidence rather than delaying alerts.
  * Honest — a dimension the DSM does not populate is recorded UNAVAILABLE, a
    failure is recorded FAILED, and a package that got some of both is PARTIAL.
    Nothing is inferred and no count is invented.
  * Idempotent — the package is keyed one-to-one on the anomaly, so a retry
    replaces the previous attempt rather than accumulating duplicates.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.explanation import (
    ContributorDelta,
    DimensionComparison,
    compare_all,
)
from app.core.config import Settings, get_settings
from app.models.enums import DimensionAvailability, EvidenceStatus
from app.models.explanation import (
    EXPLANATION_SCHEMA_VERSION,
    AnomalyExplanation,
    AnomalyExplanationContributor,
    AnomalyExplanationDimension,
)
from app.models.log_source import LogSource, LogSourceAnomaly
from app.providers.base import ProviderError, QRadarProvider
from app.providers.dto import DimensionAggregate

logger = logging.getLogger("app.collectors.explanation")

COMPARISON_STRATEGY = "recent_normal_window"

#: Advisory-lock name, so two workers cannot run investigation queries against
#: the same instance concurrently.
EXPLANATION_COLLECTOR = "anomaly_explanation"


@dataclass
class ExplanationReport:
    anomaly_id: object
    status: EvidenceStatus
    dimensions_analyzed: int = 0
    contributors_written: int = 0
    error: str | None = None


@dataclass
class _Windows:
    anomaly_start: datetime
    anomaly_end: datetime
    baseline_start: datetime
    baseline_end: datetime

    @property
    def anomaly_seconds(self) -> float:
        return (self.anomaly_end - self.anomaly_start).total_seconds()

    @property
    def baseline_seconds(self) -> float:
        return (self.baseline_end - self.baseline_start).total_seconds()


class ExplanationCollector:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def collect(self, anomaly: LogSourceAnomaly) -> ExplanationReport:
        started = self._clock()
        log_source = await self.session.get(LogSource, anomaly.log_source_id)
        if log_source is None:
            return await self._fail(anomaly, "log source no longer exists", started)

        windows = self._windows(anomaly)
        dimensions = list(self.settings.explanation_dimensions)
        top_n = self.settings.explanation_top_values

        try:
            anomaly_aggs = await self.provider.get_dimension_aggregates(
                qradar_log_source_id=log_source.qradar_id,
                window_start=windows.anomaly_start,
                window_end=windows.anomaly_end,
                dimensions=dimensions,
                top_n=top_n,
            )
            baseline_aggs = await self.provider.get_dimension_aggregates(
                qradar_log_source_id=log_source.qradar_id,
                window_start=windows.baseline_start,
                window_end=windows.baseline_end,
                dimensions=dimensions,
                top_n=top_n,
            )
        except (ProviderError, ValueError, NotImplementedError) as exc:
            # Sanitized: our own message and the exception class, never an
            # upstream response body.
            return await self._fail(
                anomaly, f"{type(exc).__name__}: {exc}"[:400], started
            )

        comparisons = compare_all(
            baseline_aggs,
            anomaly_aggs,
            baseline_seconds=windows.baseline_seconds,
            anomaly_seconds=windows.anomaly_seconds,
            top_n=top_n,
        )

        package = await self._replace_package(anomaly, windows)
        package.anomaly_total_events = sum(a.total_count for a in anomaly_aggs)
        package.baseline_total_events = sum(a.total_count for a in baseline_aggs)
        package.query_provenance = _provenance(anomaly_aggs, baseline_aggs, windows)

        contributors = 0
        for comparison in comparisons:
            self.session.add(_dimension_row(package, comparison))
            for contributor in comparison.contributors:
                self.session.add(_contributor_row(package, contributor))
                contributors += 1

        status = _package_status(comparisons)
        completed = self._clock()
        package.status = status
        package.completed_at = completed
        package.collection_duration_ms = int(
            (completed - started).total_seconds() * 1000
        )
        anomaly.evidence_status = status
        await self.session.flush()

        logger.info(
            "explanation collected",
            extra={
                "anomaly": str(anomaly.id),
                "evidence_status": status.value,
                "dimensions": len(comparisons),
                "contributors": contributors,
            },
        )
        return ExplanationReport(
            anomaly_id=anomaly.id,
            status=status,
            dimensions_analyzed=len(comparisons),
            contributors_written=contributors,
        )

    # ------------------------------------------------------------- windows
    def _windows(self, anomaly: LogSourceAnomaly) -> _Windows:
        """Anomaly window, plus the recent-normal window preceding it.

        The comparison window ends where the anomaly begins, so it contains only
        pre-anomaly traffic. Overlapping the two would dilute the very change
        the package exists to isolate.
        """
        max_seconds = float(self.settings.explanation_max_window_seconds)

        start = anomaly.anomaly_start or anomaly.detected_at
        end = anomaly.anomaly_end or self._clock()
        if end <= start:
            # A single-bucket anomaly that has not yet been extended. Give it a
            # nonzero width so the query is well-formed.
            end = start + timedelta(minutes=5)
        # Clamp from the end: the most recent traffic is the most relevant.
        if (end - start).total_seconds() > max_seconds:
            start = end - timedelta(seconds=max_seconds)

        anomaly_seconds = (end - start).total_seconds()
        baseline_seconds = min(
            anomaly_seconds * self.settings.explanation_baseline_window_multiple,
            max_seconds,
        )
        return _Windows(
            anomaly_start=start,
            anomaly_end=end,
            baseline_start=start - timedelta(seconds=baseline_seconds),
            baseline_end=start,
        )

    # ---------------------------------------------------------- persistence
    async def _replace_package(
        self, anomaly: LogSourceAnomaly, windows: _Windows
    ) -> AnomalyExplanation:
        """Fresh package for this anomaly, discarding any previous attempt.

        A retry after a partial failure must not merge with the rows the failed
        attempt left behind, or the package would mix two collection times and
        two window definitions.
        """
        existing = await self.session.scalar(
            select(AnomalyExplanation).where(
                AnomalyExplanation.anomaly_id == anomaly.id
            )
        )
        if existing is not None:
            await self.session.execute(
                delete(AnomalyExplanationContributor).where(
                    AnomalyExplanationContributor.explanation_id == existing.id
                )
            )
            await self.session.execute(
                delete(AnomalyExplanationDimension).where(
                    AnomalyExplanationDimension.explanation_id == existing.id
                )
            )
            await self.session.delete(existing)
            await self.session.flush()

        package = AnomalyExplanation(
            anomaly_id=anomaly.id,
            status=EvidenceStatus.PENDING,
            anomaly_window_start=windows.anomaly_start,
            anomaly_window_end=windows.anomaly_end,
            baseline_window_start=windows.baseline_start,
            baseline_window_end=windows.baseline_end,
            comparison_strategy=COMPARISON_STRATEGY,
            requested_at=self._clock(),
            schema_version=EXPLANATION_SCHEMA_VERSION,
        )
        self.session.add(package)
        await self.session.flush()
        return package

    async def _fail(
        self, anomaly: LogSourceAnomaly, reason: str, started: datetime
    ) -> ExplanationReport:
        windows = self._windows(anomaly)
        package = await self._replace_package(anomaly, windows)
        package.status = EvidenceStatus.FAILED
        package.error = reason
        package.completed_at = self._clock()
        package.collection_duration_ms = int(
            (package.completed_at - started).total_seconds() * 1000
        )
        anomaly.evidence_status = EvidenceStatus.FAILED
        await self.session.flush()
        logger.warning(
            "explanation failed",
            extra={"anomaly": str(anomaly.id), "reason": reason},
        )
        return ExplanationReport(
            anomaly_id=anomaly.id, status=EvidenceStatus.FAILED, error=reason
        )


def _package_status(comparisons: list[DimensionComparison]) -> EvidenceStatus:
    """Roll per-dimension outcomes up into one package status.

    UNAVAILABLE only when *every* dimension was unavailable: a source whose DSM
    populates two of ten requested fields still produced usable evidence, and
    calling that "unavailable" would discard it.
    """
    if not comparisons:
        return EvidenceStatus.UNAVAILABLE
    usable = [
        c for c in comparisons
        if c.availability in (DimensionAvailability.AVAILABLE, DimensionAvailability.TRUNCATED)
    ]
    if not usable:
        if any(c.availability is DimensionAvailability.FAILED for c in comparisons):
            return EvidenceStatus.FAILED
        return EvidenceStatus.UNAVAILABLE
    if len(usable) < len(comparisons):
        return EvidenceStatus.PARTIAL
    if any(c.truncated for c in comparisons):
        return EvidenceStatus.PARTIAL
    return EvidenceStatus.COMPLETE


def _provenance(
    anomaly_aggs: list[DimensionAggregate],
    baseline_aggs: list[DimensionAggregate],
    windows: _Windows,
) -> dict:
    """Non-secret record of the queries behind the package."""
    return {
        "comparison_strategy": COMPARISON_STRATEGY,
        "anomaly_window": {
            "start": windows.anomaly_start.isoformat(),
            "end": windows.anomaly_end.isoformat(),
            "seconds": windows.anomaly_seconds,
        },
        "baseline_window": {
            "start": windows.baseline_start.isoformat(),
            "end": windows.baseline_end.isoformat(),
            "seconds": windows.baseline_seconds,
        },
        "queries": [
            {
                "dimension": agg.dimension,
                "window": window_name,
                "aql": agg.query,
                "rows": len(agg.values),
                "truncated": agg.truncated,
                "error": agg.error,
            }
            for window_name, aggs in (
                ("anomaly", anomaly_aggs),
                ("baseline", baseline_aggs),
            )
            for agg in aggs
        ],
    }


def _dimension_row(
    package: AnomalyExplanation, comparison: DimensionComparison
) -> AnomalyExplanationDimension:
    return AnomalyExplanationDimension(
        explanation_id=package.id,
        dimension=comparison.dimension,
        availability=comparison.availability,
        detail=comparison.detail,
        baseline_distinct_count=comparison.baseline_distinct_count,
        anomaly_distinct_count=comparison.anomaly_distinct_count,
        cardinality_ratio=comparison.cardinality_ratio,
        new_value_count=comparison.new_value_count,
        disappeared_value_count=comparison.disappeared_value_count,
        baseline_top_share=comparison.baseline_top_share,
        anomaly_top_share=comparison.anomaly_top_share,
        truncated=comparison.truncated,
    )


def _contributor_row(
    package: AnomalyExplanation, c: ContributorDelta
) -> AnomalyExplanationContributor:
    return AnomalyExplanationContributor(
        explanation_id=package.id,
        dimension=c.dimension,
        value=c.value[:255],
        label=c.label[:255] if c.label else None,
        baseline_count=c.baseline_count,
        anomaly_count=c.anomaly_count,
        absolute_delta=c.absolute_delta,
        percent_delta=c.percent_delta,
        anomaly_share=c.anomaly_share,
        baseline_share=c.baseline_share,
        # Clamped to the range the CHECK constraint enforces. Float summation
        # over many values can land a hair outside [-1, 1].
        contribution_share=(
            max(-1.0, min(1.0, c.contribution_share))
            if c.contribution_share is not None
            else None
        ),
        baseline_rank=c.baseline_rank,
        anomaly_rank=c.anomaly_rank,
        rank=c.rank,
        is_new=c.is_new,
        is_disappeared=c.is_disappeared,
    )
