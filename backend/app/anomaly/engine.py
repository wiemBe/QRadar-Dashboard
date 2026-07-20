"""Anomaly evaluation engine.

For one log source and one collection interval it:
  1. Builds a DetectionContext from the interval's metric, the matching
     (weekday, hour) baselines, and recent payload signatures.
  2. Runs every detector, suppressing the ones NO_EVENTS makes redundant.
  3. Applies hysteresis per (source, anomaly type) via LogSourceDetectorState:
     open only after N consecutive anomalous intervals, resolve only after M
     consecutive healthy ones. N and M come from the threshold resolver
     (per type and per criticality), so alerts do not flap.
  4. Persists LogSourceAnomaly rows and drives the alert lifecycle.

Evaluation is idempotent per interval: re-running the same interval is a no-op
because the detector state records the last interval processed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.fingerprint import anomaly_fingerprint
from app.alerts.service import AlertInput, AlertService
from app.anomaly.detectors import (
    DETECTORS,
    SUPPRESSED_BY_NO_EVENTS,
    BaselineCell,
    DetectionContext,
    MetricPoint,
)
from app.anomaly.evidence import AnomalyEvidence
from app.anomaly.thresholds import ThresholdResolver
from app.core.config import Settings, get_settings
from app.models.enums import AnomalyType, DetectorSignal
from app.models.log_source import (
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
    LogSourceMetric,
)


@dataclass
class EvaluationReport:
    log_source_id: uuid.UUID
    interval_start: datetime | None
    opened: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    anomalous: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


class AnomalyEngine:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock=None,
        enqueuer=None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._resolver = ThresholdResolver(self.settings)
        self.alerts = AlertService(session, clock=self._clock)
        # Optional object with `async enqueue(alert, transition)`. When set,
        # alert transitions produce notification rows. None in unit tests.
        self._enqueuer = enqueuer

    async def evaluate_latest(self, log_source: LogSource) -> EvaluationReport:
        metric = await self.session.scalar(
            select(LogSourceMetric)
            .where(LogSourceMetric.log_source_id == log_source.id)
            .order_by(LogSourceMetric.bucket_start.desc())
            .limit(1)
        )
        if metric is None:
            return EvaluationReport(log_source.id, None, skipped_reason="no metrics")
        return await self.evaluate_interval(log_source, metric)

    async def evaluate_interval(
        self, log_source: LogSource, metric: LogSourceMetric
    ) -> EvaluationReport:
        report = EvaluationReport(log_source.id, metric.bucket_start)

        if not log_source.monitoring_enabled:
            report.skipped_reason = "monitoring disabled"
            return report

        in_maintenance = self._in_maintenance(log_source, metric.bucket_start)

        ctx = await self._build_context(log_source, metric)
        evidences = self._run_detectors(ctx)

        for evidence in evidences:
            if evidence.is_anomalous:
                report.anomalous.append(evidence.anomaly_type.value)
            await self._apply_hysteresis(log_source, metric, evidence, in_maintenance, report)

        await self.session.flush()
        return report

    # ------------------------------------------------------------- context
    async def _build_context(
        self, log_source: LogSource, metric: LogSourceMetric
    ) -> DetectionContext:
        weekday = metric.bucket_start.isoweekday()
        hour = metric.bucket_start.hour
        cells = await self._baseline_cells(log_source.id, weekday, hour)

        recent = await self._recent_signatures(log_source.id, metric.bucket_start)
        thresholds = self._resolver.resolve(
            criticality=log_source.criticality, custom=log_source.custom_thresholds
        )
        expected_interval = float(
            log_source.expected_interval_seconds or metric.bucket_seconds or 300
        )

        point = MetricPoint(
            interval_start=metric.bucket_start,
            interval_end=metric.bucket_start + timedelta(seconds=metric.bucket_seconds),
            event_count=metric.event_count,
            average_eps=metric.average_eps,
            peak_eps=metric.peak_eps,
            last_event_at=metric.last_event_at,
            event_delay_seconds=metric.event_delay_seconds,
            unknown_event_count=metric.unknown_event_count,
            stored_event_count=metric.stored_event_count,
            parsed_username_ratio=metric.parsed_username_ratio,
            parsed_source_ip_ratio=metric.parsed_source_ip_ratio,
            distinct_qid_count=metric.distinct_qid_count,
            distinct_username_count=metric.distinct_username_count,
            distinct_source_ip_count=metric.distinct_source_ip_count,
            collection_error_count=metric.collection_error_count,
            payload_signature=metric.payload_signature,
        )
        return DetectionContext(
            point=point,
            baselines=cells,
            thresholds=thresholds,
            expected_interval_seconds=expected_interval,
            recent_signatures=recent,
            is_expected_active=self._is_expected_active(log_source, metric.bucket_start),
        )

    async def _baseline_cells(
        self, log_source_id: uuid.UUID, weekday: int, hour: int
    ) -> dict[str, BaselineCell]:
        rows = await self.session.scalars(
            select(LogSourceBaseline).where(
                LogSourceBaseline.log_source_id == log_source_id,
                LogSourceBaseline.weekday == weekday,
                LogSourceBaseline.hour == hour,
            )
        )
        cells: dict[str, BaselineCell] = {}
        for b in rows:
            cells[b.metric_name] = BaselineCell(
                median=b.median, mad=b.mad, p05=b.p05, p95=b.p95,
                sample_count=b.sample_count, is_reliable=b.is_reliable,
            )
        return cells

    async def _recent_signatures(
        self, log_source_id: uuid.UUID, before: datetime, limit: int = 6
    ) -> list[str]:
        rows = list(
            (
                await self.session.scalars(
                    select(LogSourceMetric.payload_signature)
                    .where(
                        LogSourceMetric.log_source_id == log_source_id,
                        LogSourceMetric.bucket_start < before,
                        LogSourceMetric.payload_signature.is_not(None),
                    )
                    .order_by(LogSourceMetric.bucket_start.desc())
                    .limit(limit)
                )
            ).all()
        )
        return list(reversed(rows))  # oldest -> newest

    def _run_detectors(self, ctx: DetectionContext) -> list[AnomalyEvidence]:
        results = [d(ctx) for d in DETECTORS]
        no_events = next(
            (e for e in results if e.anomaly_type == AnomalyType.NO_EVENTS), None
        )
        if no_events is not None and no_events.is_anomalous:
            # Demote the redundant detectors to UNKNOWN so their hysteresis
            # counters do not advance on a trivially-satisfied condition.
            for e in results:
                if e.anomaly_type in SUPPRESSED_BY_NO_EVENTS:
                    e.signal = DetectorSignal.UNKNOWN
                    e.reason = "suppressed: no events this interval"
        return results

    # ------------------------------------------------------------ hysteresis
    async def _apply_hysteresis(
        self,
        log_source: LogSource,
        metric: LogSourceMetric,
        evidence: AnomalyEvidence,
        in_maintenance: bool,
        report: EvaluationReport,
    ) -> None:
        state = await self._get_state(log_source.id, evidence.anomaly_type)

        # Idempotency: this interval already folded into the counters.
        if state.last_interval_start is not None and \
                state.last_interval_start >= metric.bucket_start:
            return

        thresholds = self._resolver.resolve(
            criticality=log_source.criticality,
            custom=log_source.custom_thresholds,
            anomaly_type=evidence.anomaly_type,
        )

        signal = evidence.signal
        # In maintenance, do not advance the anomalous counter (never open), but
        # allow recovery to proceed so a pre-existing anomaly can clear.
        if in_maintenance and signal is DetectorSignal.ANOMALOUS:
            signal = DetectorSignal.UNKNOWN

        if signal is DetectorSignal.ANOMALOUS:
            state.consecutive_anomalous += 1
            state.consecutive_healthy = 0
        elif signal is DetectorSignal.HEALTHY:
            state.consecutive_healthy += 1
            state.consecutive_anomalous = 0
        # UNKNOWN: leave counters untouched.

        state.last_interval_start = metric.bucket_start

        if (
            not state.is_open
            and signal is DetectorSignal.ANOMALOUS
            and state.consecutive_anomalous >= thresholds.open_after
        ):
            await self._open(log_source, metric, evidence, state, in_maintenance, report)
        elif state.is_open and signal is DetectorSignal.ANOMALOUS:
            await self._refresh(log_source, evidence, state, in_maintenance)
        elif (
            state.is_open
            and signal is DetectorSignal.HEALTHY
            and state.consecutive_healthy >= thresholds.resolve_after
        ):
            await self._resolve(log_source, evidence, state, report)

        await self.session.flush()

    async def _open(
        self,
        log_source: LogSource,
        metric: LogSourceMetric,
        evidence: AnomalyEvidence,
        state: LogSourceDetectorState,
        in_maintenance: bool,
        report: EvaluationReport,
    ) -> None:
        anomaly = LogSourceAnomaly(
            log_source_id=log_source.id,
            anomaly_type=evidence.anomaly_type,
            severity=evidence.severity,
            detected_at=metric.bucket_start,
            observed_value=evidence.observed_value,
            expected_value=evidence.expected_value,
            deviation_score=evidence.deviation_score,
            explanation=evidence.reason,
            details=evidence.to_dict(),
            suppressed=in_maintenance,
            suppression_reason="maintenance window" if in_maintenance else None,
        )
        self.session.add(anomaly)
        await self.session.flush()
        state.is_open = True
        state.open_anomaly_id = anomaly.id
        report.opened.append(evidence.anomaly_type.value)

        # Maintenance anomalies are recorded but never alerted.
        if in_maintenance:
            return
        result = await self.alerts.open_or_update(
            self._alert_input(log_source, evidence, anomaly.id)
        )
        # Enqueue a notification only on a genuine OPEN transition (dedup: an
        # update to an already-open alert has transition=None).
        if self._enqueuer is not None and result.transition is not None:
            await self._enqueuer.enqueue(result.alert, result.transition)

    def _alert_input(
        self, log_source: LogSource, evidence: AnomalyEvidence, anomaly_id: uuid.UUID | None
    ) -> AlertInput:
        return AlertInput(
            fingerprint=anomaly_fingerprint(log_source.id, evidence.anomaly_type.value),
            title=f"{evidence.anomaly_type.value} on {log_source.name}",
            severity=evidence.severity,
            source_type="log_source",
            source_id=log_source.id,
            description=evidence.reason,
            evidence=evidence.to_dict(),
            source_anomaly_ids=[str(anomaly_id)] if anomaly_id is not None else [],
            context={
                "log_source": log_source.name,
                "owner": log_source.owner,
                "criticality": str(log_source.criticality),
            },
        )

    async def _refresh(
        self,
        log_source: LogSource,
        evidence: AnomalyEvidence,
        state: LogSourceDetectorState,
        in_maintenance: bool,
    ) -> None:
        """A still-anomalous interval on an already-open condition.

        Re-asserting the alert bumps occurrence_count and refreshes the evidence,
        so an operator can see how many intervals the condition has persisted for
        rather than a count frozen at 1. open_or_update returns transition=None
        for an already-open alert, so this deliberately never notifies — that is
        the anti-noise guarantee, and it is why no enqueue happens here.
        """
        if in_maintenance:
            return
        await self.alerts.open_or_update(
            self._alert_input(log_source, evidence, state.open_anomaly_id)
        )

    async def _resolve(
        self,
        log_source: LogSource,
        evidence: AnomalyEvidence,
        state: LogSourceDetectorState,
        report: EvaluationReport,
    ) -> None:
        if state.open_anomaly_id is not None:
            anomaly = await self.session.get(LogSourceAnomaly, state.open_anomaly_id)
            if anomaly is not None and anomaly.resolved_at is None:
                anomaly.resolved_at = self._clock()
        state.is_open = False
        state.open_anomaly_id = None
        report.resolved.append(evidence.anomaly_type.value)
        result = await self.alerts.resolve_by_fingerprint(
            anomaly_fingerprint(log_source.id, evidence.anomaly_type.value),
            actor="anomaly-engine",
            reason="condition recovered",
        )
        if self._enqueuer is not None and result is not None and result.transition is not None:
            await self._enqueuer.enqueue(result.alert, result.transition)

    async def _get_state(
        self, log_source_id: uuid.UUID, anomaly_type: AnomalyType
    ) -> LogSourceDetectorState:
        state = await self.session.scalar(
            select(LogSourceDetectorState).where(
                LogSourceDetectorState.log_source_id == log_source_id,
                LogSourceDetectorState.anomaly_type == anomaly_type,
            )
        )
        if state is None:
            state = LogSourceDetectorState(
                log_source_id=log_source_id, anomaly_type=anomaly_type
            )
            self.session.add(state)
            await self.session.flush()
        return state

    # ---------------------------------------------------------------- helpers
    def _in_maintenance(self, log_source: LogSource, when: datetime) -> bool:
        if not log_source.maintenance_mode:
            return False
        if log_source.maintenance_until is None:
            return True
        return when <= log_source.maintenance_until

    def _is_expected_active(self, log_source: LogSource, when: datetime) -> bool:
        if not log_source.business_hours_only:
            return True
        weekday = when.isoweekday()
        if weekday not in (log_source.business_days or [1, 2, 3, 4, 5]):
            return False
        return log_source.business_hours_start <= when.hour < log_source.business_hours_end
