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
from app.anomaly.lifecycle import LifecycleDecision, LifecycleInput, next_state
from app.anomaly.thresholds import ThresholdResolver
from app.core.config import Settings, get_settings
from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    DetectorSignal,
    EvidenceStatus,
)
from app.models.log_source import (
    AnomalyStateTransition,
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
    LogSourceMetric,
)

#: Detector types whose incidents warrant a bounded explanation package. A
#: silence has no volume to attribute, so there is nothing to explain.
EXPLAINABLE_TYPES = frozenset({AnomalyType.VOLUME_SPIKE, AnomalyType.VOLUME_DROP})


@dataclass
class EvaluationReport:
    log_source_id: uuid.UUID
    interval_start: datetime | None
    opened: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    anomalous: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    recovering: list[str] = field(default_factory=list)
    #: Final lifecycle state per detector type after this bucket.
    states: dict[str, str] = field(default_factory=dict)
    skipped_reason: str | None = None


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _is_worse(anomaly: LogSourceAnomaly, observed: float) -> bool:
    """Whether `observed` is a more extreme reading than the one on record.

    Direction matters: for a spike the peak is the worst case, for a drop and a
    silence it is the trough.
    """
    current = anomaly.observed_value
    if current is None:
        return True
    # `==`: anomaly_type is a String column, so this is a plain str on load.
    if anomaly.anomaly_type == AnomalyType.VOLUME_SPIKE:
        return observed > current
    return observed < current


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
            # Coerced, not passed through: String-typed enum columns come back
            # from the database as plain str, and the detectors compare
            # identity. Normalizing here keeps that logic pure and safe.
            completeness=BucketCompleteness(metric.completeness),
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
            preceding_empty_buckets=await self._preceding_empty_buckets(
                log_source.id, metric
            ),
        )

    async def _preceding_empty_buckets(
        self, log_source_id: uuid.UUID, metric: LogSourceMetric, limit: int = 12
    ) -> int:
        """Consecutive empty buckets immediately before this one.

        Only complete buckets count towards the streak. A gap we failed to
        collect is not evidence that the source was silent, and letting it
        extend the streak would make a collector outage graduate into NO_EVENTS.
        """
        rows = list(
            (
                await self.session.scalars(
                    select(LogSourceMetric)
                    .where(
                        LogSourceMetric.log_source_id == log_source_id,
                        LogSourceMetric.bucket_start < metric.bucket_start,
                    )
                    .order_by(LogSourceMetric.bucket_start.desc())
                    .limit(limit)
                )
            ).all()
        )
        streak = 0
        for row in rows:
            if not row.is_complete or row.event_count > 0:
                break
            streak += 1
        return streak

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
                completeness=b.completeness, baseline_version=b.baseline_version,
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
        # UNKNOWN: leave counters untouched. Missing or unreadable data must
        # neither confirm an anomaly nor count towards recovery.

        state.last_interval_start = metric.bucket_start

        decision = next_state(
            LifecycleInput(
                state=AnomalyState(state.state),
                signal=signal,
                consecutive_anomalous=state.consecutive_anomalous,
                consecutive_healthy=state.consecutive_healthy,
                open_after=thresholds.open_after,
                resolve_after=thresholds.resolve_after,
                insufficient_data=evidence.insufficient_data,
                suppressed=(
                    in_maintenance
                    and AnomalyState(state.state) not in ACTIVE_ANOMALY_STATES
                ),
            )
        )
        await self._apply_transition(
            log_source, metric, evidence, state, decision, in_maintenance, report
        )
        await self.session.flush()

    async def _apply_transition(
        self,
        log_source: LogSource,
        metric: LogSourceMetric,
        evidence: AnomalyEvidence,
        state: LogSourceDetectorState,
        decision: LifecycleDecision,
        in_maintenance: bool,
        report: EvaluationReport,
    ) -> None:
        """Persist one lifecycle move, its audit row, and its side effects."""
        previous = AnomalyState(state.state)
        target = decision.state
        report.states[evidence.anomaly_type.value] = target.value

        if target is previous:
            # No move. An already-open incident still gets its evidence
            # refreshed so an operator can see how long it has persisted.
            if previous is AnomalyState.OPEN:
                await self._refresh(log_source, evidence, state, in_maintenance)
                await self._touch_anomaly(state, metric, evidence)
            return

        state.state = target

        if target is AnomalyState.CANDIDATE:
            created = await self._create_incident(
                log_source, metric, evidence, in_maintenance
            )
            state.active_anomaly_id = created.id
            await self._record_transition(
                created, previous, target, metric, decision.reason, evidence
            )
            report.candidates.append(evidence.anomaly_type.value)
            return

        anomaly = await self._active_anomaly(state)

        if target is AnomalyState.OPEN:
            # Confirmation threshold of 1: the incident opens on the same bucket
            # that created it, so there is nothing to promote yet.
            incident = anomaly or await self._create_incident(
                log_source, metric, evidence, in_maintenance
            )
            state.active_anomaly_id = incident.id
            await self._promote_to_open(
                log_source, metric, evidence, state, incident, in_maintenance, report
            )
            await self._record_transition(
                incident, previous, target, metric, decision.reason, evidence
            )
            return

        if anomaly is not None:
            await self._record_transition(
                anomaly, previous, target, metric, decision.reason, evidence
            )

        if target is AnomalyState.RECOVERING:
            if anomaly is not None:
                anomaly.state = AnomalyState.RECOVERING
                # The abnormal telemetry ended with the last abnormal bucket,
                # not with the normal one that revealed the recovery.
                anomaly.anomaly_end = metric.bucket_start
            report.recovering.append(evidence.anomaly_type.value)
            return

        if target is AnomalyState.RESOLVED:
            await self._resolve(log_source, evidence, state, anomaly, report)
            return

        if target is AnomalyState.SUPPRESSED:
            if anomaly is not None:
                anomaly.state = AnomalyState.SUPPRESSED
                anomaly.suppressed = True
                anomaly.suppression_reason = anomaly.suppression_reason or "maintenance window"
            state.is_open = False
            return

        # NORMAL or INSUFFICIENT_DATA: no incident is live any more. A CANDIDATE
        # that never opened is closed out as a non-incident rather than left
        # dangling, so a later abnormal bucket starts a fresh one.
        if anomaly is not None and previous is AnomalyState.CANDIDATE:
            anomaly.state = target
            anomaly.anomaly_end = metric.bucket_start
        state.active_anomaly_id = None
        state.is_open = False

    async def _create_incident(
        self,
        log_source: LogSource,
        metric: LogSourceMetric,
        evidence: AnomalyEvidence,
        in_maintenance: bool,
    ) -> LogSourceAnomaly:
        """Record a new incident in CANDIDATE. Not yet an alert.

        Persisting at candidate time rather than at open time means the first
        abnormal bucket is captured with its evidence even if the incident is
        never confirmed — which is exactly the data needed to tune away a noisy
        detector.
        """
        facts = evidence.extra or {}
        anomaly = LogSourceAnomaly(
            log_source_id=log_source.id,
            anomaly_type=evidence.anomaly_type,
            severity=evidence.severity,
            state=AnomalyState.CANDIDATE,
            detected_at=metric.bucket_start,
            anomaly_start=metric.bucket_start,
            observed_value=evidence.observed_value,
            expected_value=evidence.expected_value,
            deviation_score=evidence.deviation_score,
            confidence=evidence.confidence,
            deviation_ratio=_as_float(facts.get("ratio")),
            robust_z=_as_float(facts.get("robust_z")),
            absolute_delta=_as_float(facts.get("absolute_delta_events")),
            consecutive_buckets=1,
            baseline_version=_as_int(facts.get("baseline_version")),
            policy_version=self.settings.anomaly_policy_version,
            evidence_status=EvidenceStatus.NOT_REQUESTED,
            explanation=evidence.reason,
            details=evidence.to_dict(),
            suppressed=in_maintenance,
            suppression_reason="maintenance window" if in_maintenance else None,
        )
        self.session.add(anomaly)
        await self.session.flush()
        return anomaly

    async def _promote_to_open(
        self,
        log_source: LogSource,
        metric: LogSourceMetric,
        evidence: AnomalyEvidence,
        state: LogSourceDetectorState,
        anomaly: LogSourceAnomaly,
        in_maintenance: bool,
        report: EvaluationReport,
    ) -> None:
        anomaly.state = AnomalyState.OPEN
        anomaly.opened_at = self._clock()
        anomaly.anomaly_end = None
        anomaly.severity = evidence.severity
        await self._touch_anomaly(state, metric, evidence)

        state.is_open = True
        state.open_anomaly_id = anomaly.id
        state.active_anomaly_id = anomaly.id
        report.opened.append(evidence.anomaly_type.value)

        # A confirmed volume anomaly is what the investigation exists for. Mark
        # the package pending here; the collector task fills it asynchronously
        # so detection never blocks on an Ariel round-trip.
        if (
            self.settings.explanation_enabled
            and evidence.anomaly_type in EXPLAINABLE_TYPES
            and not in_maintenance
        ):
            anomaly.evidence_status = EvidenceStatus.PENDING

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

    async def _touch_anomaly(
        self,
        state: LogSourceDetectorState,
        metric: LogSourceMetric,
        evidence: AnomalyEvidence,
    ) -> None:
        """Refresh the live incident's measurements from the current bucket."""
        anomaly = await self._active_anomaly(state)
        if anomaly is None:
            return
        facts = evidence.extra or {}
        anomaly.consecutive_buckets = max(
            anomaly.consecutive_buckets, state.consecutive_anomalous
        )
        # Track the worst observation seen, not merely the latest: an operator
        # asking "how bad did this get?" must not get the tail of a recovery.
        if evidence.is_anomalous:
            anomaly.anomaly_end = metric.bucket_start + timedelta(
                seconds=metric.bucket_seconds
            )
            observed = evidence.observed_value
            if observed is not None and _is_worse(anomaly, observed):
                anomaly.observed_value = observed
                anomaly.deviation_score = evidence.deviation_score
                anomaly.deviation_ratio = _as_float(facts.get("ratio"))
                anomaly.robust_z = _as_float(facts.get("robust_z"))
                anomaly.absolute_delta = _as_float(facts.get("absolute_delta_events"))
                anomaly.explanation = evidence.reason
                anomaly.details = evidence.to_dict()

    async def _active_anomaly(
        self, state: LogSourceDetectorState
    ) -> LogSourceAnomaly | None:
        if state.active_anomaly_id is None:
            return None
        return await self.session.get(LogSourceAnomaly, state.active_anomaly_id)

    async def _record_transition(
        self,
        anomaly: LogSourceAnomaly,
        from_state: AnomalyState,
        to_state: AnomalyState,
        metric: LogSourceMetric,
        reason: str,
        evidence: AnomalyEvidence,
    ) -> None:
        self.session.add(
            AnomalyStateTransition(
                anomaly_id=anomaly.id,
                from_state=from_state,
                to_state=to_state,
                occurred_at=self._clock(),
                bucket_start=metric.bucket_start,
                reason=reason,
                actor="anomaly-engine",
                observed_value=evidence.observed_value,
                expected_value=evidence.expected_value,
            )
        )

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
        anomaly: LogSourceAnomaly | None,
        report: EvaluationReport,
    ) -> None:
        if anomaly is not None and anomaly.resolved_at is None:
            anomaly.state = AnomalyState.RESOLVED
            anomaly.resolved_at = self._clock()
        state.is_open = False
        state.open_anomaly_id = None
        # Clearing the active incident is what makes a later recurrence open a
        # new one rather than reviving this closed incident.
        state.active_anomaly_id = None
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
