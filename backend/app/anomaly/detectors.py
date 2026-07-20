"""The nine anomaly detectors. Pure functions over a DetectionContext.

Each returns an AnomalyEvidence with a HEALTHY / ANOMALOUS / UNKNOWN signal and
full structured evidence. No I/O, no ORM — the engine assembles the context and
persists the outcome. This keeps every detector exhaustively unit-testable
against exact inputs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.anomaly import statistics as rstats
from app.anomaly.evidence import AnomalyEvidence, healthy, unknown
from app.anomaly.thresholds import Thresholds
from app.core.config import get_settings
from app.models.enums import AnomalyType, DetectorSignal, Severity


@dataclass
class MetricPoint:
    interval_start: datetime
    interval_end: datetime
    event_count: int
    average_eps: float
    peak_eps: float
    last_event_at: datetime | None
    event_delay_seconds: float | None
    unknown_event_count: int
    stored_event_count: int
    parsed_username_ratio: float | None
    parsed_source_ip_ratio: float | None
    distinct_qid_count: int
    distinct_username_count: int
    distinct_source_ip_count: int
    collection_error_count: int
    payload_signature: str | None


@dataclass
class BaselineCell:
    median: float
    mad: float
    p05: float | None
    p95: float | None
    sample_count: int
    is_reliable: bool


@dataclass
class DetectionContext:
    point: MetricPoint
    baselines: dict[str, BaselineCell]
    thresholds: Thresholds
    expected_interval_seconds: float
    # payload_signature of preceding intervals, oldest→newest, excluding current
    recent_signatures: list[str] = field(default_factory=list)
    # whether the source is expected to be actively reporting in this interval
    # (business hours / schedule) — off-hours silence is not NO_EVENTS
    is_expected_active: bool = True

    @property
    def mad_floor_ratio(self) -> float:
        return get_settings().baseline_mad_floor_ratio


def _confidence(cell: BaselineCell | None, magnitude: float) -> float:
    """Confidence blends baseline reliability with deviation magnitude."""
    if cell is None:
        return 0.5
    reliability = min(1.0, cell.sample_count / 30.0)
    strength = min(1.0, magnitude / 6.0)
    return round(0.4 * reliability + 0.6 * strength, 3)


# --------------------------------------------------------------------------- 1
def detect_no_events(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.NO_EVENTS
    if not ctx.is_expected_active:
        return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason="source not expected to be active this interval")
    cell = ctx.baselines.get("event_count")
    expected = cell.median if cell else None
    if p.event_count == 0 and (expected is None or expected > 0):
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.HIGH,
            observed_value=0.0, expected_value=expected,
            baseline_low=cell.p05 if cell else None, baseline_high=cell.p95 if cell else None,
            deviation_score=None, threshold=0.0, sample_count=cell.sample_count if cell else 0,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=0.9 if (cell and cell.is_reliable) else 0.6,
            reason="no events received in an interval where events are expected",
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=float(p.event_count), expected=expected)


# --------------------------------------------------------------------------- 2
def detect_volume_drop(ctx: DetectionContext) -> AnomalyEvidence:
    return _volume(ctx, AnomalyType.VOLUME_DROP, direction=-1)


# --------------------------------------------------------------------------- 3
def detect_volume_spike(ctx: DetectionContext) -> AnomalyEvidence:
    return _volume(ctx, AnomalyType.VOLUME_SPIKE, direction=+1)


def _volume(ctx: DetectionContext, at: AnomalyType, *, direction: int) -> AnomalyEvidence:
    p = ctx.point
    cell = ctx.baselines.get("average_eps")
    if cell is None or not cell.is_reliable:
        return unknown(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason="no reliable volume baseline yet")
    z = rstats.robust_z(p.average_eps, cell.median, cell.mad, mad_floor_ratio=ctx.mad_floor_ratio)
    thr = ctx.thresholds.deviation_z
    triggered = (z <= -thr) if direction < 0 else (z >= thr)
    if triggered:
        sev = Severity.HIGH if abs(z) >= thr * 1.5 else Severity.MEDIUM
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=sev,
            observed_value=round(p.average_eps, 3), expected_value=round(cell.median, 3),
            baseline_low=cell.p05, baseline_high=cell.p95,
            deviation_score=round(z, 3), threshold=thr, sample_count=cell.sample_count,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=_confidence(cell, abs(z)),
            reason=(
                f"average EPS {p.average_eps:.2f} is "
                f"{'below' if direction < 0 else 'above'} baseline median "
                f"{cell.median:.2f} (robust z={z:.2f}, threshold ±{thr})"
            ),
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=round(p.average_eps, 3), expected=round(cell.median, 3))


# --------------------------------------------------------------------------- 4
def detect_parsing_degradation(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.PARSING_DEGRADATION
    ratios = [r for r in (p.parsed_username_ratio, p.parsed_source_ip_ratio) if r is not None]
    if not ratios:
        return unknown(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason="no parse-ratio data")
    worst = min(ratios)
    floor = ctx.thresholds.min_parsed_ratio
    if worst < floor:
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.MEDIUM,
            observed_value=round(worst, 3), expected_value=floor,
            baseline_low=floor, baseline_high=1.0, deviation_score=round(floor - worst, 3),
            threshold=floor, sample_count=p.event_count,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=round(min(1.0, (floor - worst) / max(floor, 0.01)), 3),
            reason=f"field-extraction ratio {worst:.2f} fell below floor {floor:.2f}",
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=round(worst, 3), expected=floor)


# --------------------------------------------------------------------------- 5
def detect_unknown_event_spike(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.UNKNOWN_EVENT_SPIKE
    if p.event_count <= 0:
        return unknown(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason="no events to classify")
    ratio = p.unknown_event_count / p.event_count
    ceiling = ctx.thresholds.max_unknown_ratio
    if ratio > ceiling:
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.MEDIUM,
            observed_value=round(ratio, 3), expected_value=ceiling,
            baseline_low=0.0, baseline_high=ceiling, deviation_score=round(ratio - ceiling, 3),
            threshold=ceiling, sample_count=p.event_count,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=round(min(1.0, (ratio - ceiling) / max(ceiling, 0.01)), 3),
            reason=f"unknown-event ratio {ratio:.2f} exceeds ceiling {ceiling:.2f}",
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=round(ratio, 3), expected=ceiling)


# --------------------------------------------------------------------------- 6
def detect_timestamp_delay(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.TIMESTAMP_DELAY
    if p.event_delay_seconds is None:
        return unknown(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason="no delay measurement")
    limit = ctx.thresholds.delay_interval_multiple * ctx.expected_interval_seconds
    if p.event_delay_seconds > limit:
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.MEDIUM,
            observed_value=round(p.event_delay_seconds, 1), expected_value=round(limit, 1),
            baseline_low=0.0, baseline_high=limit,
            deviation_score=round(p.event_delay_seconds / max(limit, 1), 2),
            threshold=round(limit, 1), sample_count=1,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=0.8,
            reason=f"event delay {p.event_delay_seconds:.0f}s exceeds {limit:.0f}s",
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=round(p.event_delay_seconds, 1), expected=round(limit, 1))


# --------------------------------------------------------------------------- 7
def detect_cardinality_drop(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.CARDINALITY_DROP
    cell = ctx.baselines.get("distinct_source_ip_count")
    if cell is None or not cell.is_reliable or cell.median <= 0:
        return unknown(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason="no reliable cardinality baseline")
    frac = ctx.thresholds.cardinality_drop_fraction
    expected_floor = cell.median * frac
    if p.distinct_source_ip_count < expected_floor:
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.MEDIUM,
            observed_value=float(p.distinct_source_ip_count), expected_value=round(cell.median, 1),
            baseline_low=round(expected_floor, 1), baseline_high=cell.p95,
            deviation_score=round(1 - p.distinct_source_ip_count / max(cell.median, 1), 3),
            threshold=round(expected_floor, 1), sample_count=cell.sample_count,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=_confidence(cell, 4.0),
            reason=(
                f"distinct source IPs {p.distinct_source_ip_count} dropped below "
                f"{frac:.0%} of baseline median {cell.median:.0f}"
            ),
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=float(p.distinct_source_ip_count), expected=round(cell.median, 1))


# --------------------------------------------------------------------------- 8
def detect_collection_error(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.COLLECTION_ERROR
    if p.collection_error_count > ctx.thresholds.collection_error_threshold:
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.HIGH,
            observed_value=float(p.collection_error_count),
            expected_value=float(ctx.thresholds.collection_error_threshold),
            baseline_low=0.0, baseline_high=float(ctx.thresholds.collection_error_threshold),
            deviation_score=float(p.collection_error_count), threshold=float(
                ctx.thresholds.collection_error_threshold),
            sample_count=1, interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=0.95,
            reason=f"{p.collection_error_count} collection error(s) in the interval",
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=float(p.collection_error_count), expected=0.0)


# --------------------------------------------------------------------------- 9
def detect_repeated_payload(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.REPEATED_PAYLOAD
    if p.payload_signature is None:
        return unknown(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason="no payload signature")
    need = ctx.thresholds.repeated_payload_intervals
    # Count trailing consecutive intervals (including current) with same signature.
    streak = 1
    for sig in reversed(ctx.recent_signatures):
        if sig == p.payload_signature:
            streak += 1
        else:
            break
    if streak >= need:
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.MEDIUM,
            observed_value=float(streak), expected_value=float(need - 1),
            baseline_low=0.0, baseline_high=float(need - 1), deviation_score=float(streak),
            threshold=float(need), sample_count=streak,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=round(min(1.0, streak / (need * 2)), 3),
            reason=f"identical payload signature repeated for {streak} consecutive intervals",
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=float(streak), expected=float(need))


# Canonical order. NO_EVENTS first: when it fires the engine suppresses the
# volume/parsing/cardinality detectors, which a zero-event interval would
# trivially and redundantly trip.
DETECTORS: list[Callable[[DetectionContext], AnomalyEvidence]] = [
    detect_no_events,
    detect_collection_error,
    detect_volume_drop,
    detect_volume_spike,
    detect_parsing_degradation,
    detect_unknown_event_spike,
    detect_timestamp_delay,
    detect_cardinality_drop,
    detect_repeated_payload,
]

# When NO_EVENTS fires, these are meaningless for the same interval.
SUPPRESSED_BY_NO_EVENTS = {
    AnomalyType.VOLUME_DROP,
    AnomalyType.PARSING_DEGRADATION,
    AnomalyType.UNKNOWN_EVENT_SPIKE,
    AnomalyType.CARDINALITY_DROP,
    AnomalyType.REPEATED_PAYLOAD,
}
