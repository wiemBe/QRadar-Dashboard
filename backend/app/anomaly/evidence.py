"""Structured anomaly evidence.

Every detector returns this shape (never a bare label) so the UI and the alert
payload can show *why* something fired: observed vs expected, the baseline band,
the deviation score, the threshold it crossed, how many samples backed the
baseline, the affected interval, a confidence, and a human-readable reason.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from app.models.enums import AnomalyType, DetectorSignal, Severity


@dataclass
class AnomalyEvidence:
    anomaly_type: AnomalyType
    signal: DetectorSignal
    severity: Severity
    observed_value: float | None
    expected_value: float | None
    baseline_low: float | None
    baseline_high: float | None
    deviation_score: float | None
    threshold: float | None
    sample_count: int
    interval_start: datetime
    interval_end: datetime
    confidence: float
    reason: str
    extra: dict = field(default_factory=dict)
    # True when the detector could not reach a verdict for want of usable data,
    # as opposed to reaching a "not anomalous" verdict. Both carry the UNKNOWN
    # signal so neither advances hysteresis, but only this one may surface as
    # INSUFFICIENT_DATA — an operator must be able to tell "this source is fine"
    # apart from "we cannot yet say whether this source is fine".
    insufficient_data: bool = False

    @property
    def is_anomalous(self) -> bool:
        return self.signal is DetectorSignal.ANOMALOUS

    def to_dict(self) -> dict:
        d = asdict(self)
        d["anomaly_type"] = self.anomaly_type.value
        d["signal"] = self.signal.value
        d["severity"] = self.severity.value
        d["interval_start"] = self.interval_start.isoformat()
        d["interval_end"] = self.interval_end.isoformat()
        return d


def healthy(
    anomaly_type: AnomalyType,
    *,
    interval_start: datetime,
    interval_end: datetime,
    observed: float | None = None,
    expected: float | None = None,
    reason: str = "within expected range",
) -> AnomalyEvidence:
    return AnomalyEvidence(
        anomaly_type=anomaly_type,
        signal=DetectorSignal.HEALTHY,
        severity=Severity.INFO,
        observed_value=observed,
        expected_value=expected,
        baseline_low=None,
        baseline_high=None,
        deviation_score=None,
        threshold=None,
        sample_count=0,
        interval_start=interval_start,
        interval_end=interval_end,
        confidence=1.0,
        reason=reason,
    )


def unknown(
    anomaly_type: AnomalyType,
    *,
    interval_start: datetime,
    interval_end: datetime,
    reason: str,
) -> AnomalyEvidence:
    return AnomalyEvidence(
        anomaly_type=anomaly_type,
        signal=DetectorSignal.UNKNOWN,
        severity=Severity.INFO,
        observed_value=None,
        expected_value=None,
        baseline_low=None,
        baseline_high=None,
        deviation_score=None,
        threshold=None,
        sample_count=0,
        interval_start=interval_start,
        interval_end=interval_end,
        confidence=0.0,
        reason=reason,
    )


def insufficient(
    anomaly_type: AnomalyType,
    *,
    interval_start: datetime,
    interval_end: datetime,
    reason: str,
    sample_count: int = 0,
) -> AnomalyEvidence:
    """No verdict is possible: the baseline or the bucket is inadequate.

    Deliberately not `healthy()`. Returning "normal" because we could not
    measure anything is the single most misleading thing a monitoring system can
    do — it converts an observability gap into a false assurance.
    """
    ev = unknown(
        anomaly_type,
        interval_start=interval_start,
        interval_end=interval_end,
        reason=reason,
    )
    ev.insufficient_data = True
    ev.sample_count = sample_count
    return ev
