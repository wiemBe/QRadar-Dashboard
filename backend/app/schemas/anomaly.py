"""Phase A behavioral-analytics API schemas.

Presentation shapes, deliberately distinct from both the ORM models and the
provider DTOs. Two rules run through all of them:

  * A value that was not measured is `None`, never 0 and never a placeholder.
    `percent_delta=None` on a newly observed contributor means "this value has
    no baseline to be a percentage of", which is a different fact from "it did
    not change".
  * Availability and completeness travel with the data, so a client can render
    "we could not measure this" instead of silently drawing an empty chart.

All timestamps are UTC. Timezone conversion is a presentation concern and
happens in the browser, against the user's selected timezone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    DimensionAvailability,
    EvidenceStatus,
    RobustScoreStatus,
    Severity,
)


class MetricBucketOut(BaseModel):
    """One time bucket of observed volume."""

    model_config = ConfigDict(from_attributes=True)

    bucket_start: datetime
    bucket_seconds: int
    event_count: int
    average_eps: float
    peak_eps: float
    completeness: BucketCompleteness
    last_event_at: datetime | None = None


class BaselineCellOut(BaseModel):
    """The expected band for one (weekday, hour) seasonal cell."""

    model_config = ConfigDict(from_attributes=True)

    metric_name: str
    weekday: int
    hour: int
    median: float
    mad: float
    p05: float | None = None
    p95: float | None = None
    sample_count: int
    #: False means the cell exists but has too few samples to drive a verdict.
    #: The UI must render this as "still learning", never as a healthy band.
    is_reliable: bool
    completeness: float
    baseline_version: int
    computed_at: datetime | None = None


class SourceBehaviorOut(BaseModel):
    """Current observed-vs-expected behavior for one log source."""

    log_source_id: uuid.UUID
    name: str
    criticality: str
    #: None when no bucket has been collected yet.
    observed_eps: float | None = None
    #: None when the seasonal cell has no adequate baseline.
    expected_eps: float | None = None
    expected_low: float | None = None
    expected_high: float | None = None
    #: observed / expected. None when expected is zero — a ratio against nothing
    #: does not exist and must not be rendered as a large number.
    deviation_ratio: float | None = None
    state: AnomalyState
    baseline_sample_count: int = 0
    baseline_completeness: float = 0.0
    last_bucket_at: datetime | None = None
    last_event_at: datetime | None = None
    open_anomaly_count: int = 0


class AnomalyTransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_state: AnomalyState | None = None
    to_state: AnomalyState
    occurred_at: datetime
    bucket_start: datetime | None = None
    reason: str | None = None
    actor: str
    observed_value: float | None = None
    expected_value: float | None = None


class AnomalyListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    log_source_id: uuid.UUID
    #: Resolved by the route from the owning source. None only when the source
    #: row is gone, which the UI renders as the id rather than as a blank cell.
    log_source_name: str | None = None
    anomaly_type: AnomalyType
    state: AnomalyState
    severity: Severity
    observed_value: float | None = None
    expected_value: float | None = None
    deviation_ratio: float | None = None
    robust_z: float | None = None
    absolute_delta: float | None = None
    consecutive_buckets: int = 0
    confidence: float | None = None
    detected_at: datetime
    opened_at: datetime | None = None
    anomaly_start: datetime | None = None
    anomaly_end: datetime | None = None
    resolved_at: datetime | None = None
    evidence_status: EvidenceStatus
    suppressed: bool = False
    #: Server-rendered detector reasoning, so the UI never reconstructs
    #: detection logic to explain a verdict.
    explanation: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_seconds(self) -> float | None:
        """How long the abnormal telemetry lasted, in seconds.

        A computed field rather than a plain property so it is actually
        serialized: every client would otherwise recompute this arithmetic, and
        the "still running" case (no end yet) is exactly where an independent
        reimplementation tends to substitute `now` and invent a duration.
        """
        if self.anomaly_start is None:
            return None
        end = self.anomaly_end or self.resolved_at
        return (end - self.anomaly_start).total_seconds() if end else None


class ContributorOut(BaseModel):
    """One (dimension, value) pair and its contribution to the change."""

    model_config = ConfigDict(from_attributes=True)

    dimension: str
    value: str
    label: str | None = None
    baseline_count: int
    anomaly_count: int
    absolute_delta: int
    #: None for a newly observed value: it has no baseline to be a percent of.
    percent_delta: float | None = None
    anomaly_share: float | None = None
    baseline_share: float | None = None
    contribution_share: float | None = None
    baseline_rank: int | None = None
    anomaly_rank: int | None = None
    rank: int
    is_new: bool
    is_disappeared: bool


class ExplanationDimensionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dimension: str
    availability: DimensionAvailability
    #: Why a dimension is UNAVAILABLE or FAILED. Sanitized.
    detail: str | None = None
    baseline_distinct_count: int | None = None
    anomaly_distinct_count: int | None = None
    cardinality_ratio: float | None = None
    new_value_count: int = 0
    disappeared_value_count: int = 0
    baseline_top_share: float | None = None
    anomaly_top_share: float | None = None
    truncated: bool = False
    contributors: list[ContributorOut] = Field(default_factory=list)


class ExplanationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: EvidenceStatus
    error: str | None = None
    anomaly_window_start: datetime
    anomaly_window_end: datetime
    baseline_window_start: datetime
    baseline_window_end: datetime
    comparison_strategy: str
    anomaly_total_events: int
    baseline_total_events: int
    requested_at: datetime | None = None
    completed_at: datetime | None = None
    collection_duration_ms: int | None = None
    #: The AQL behind each aggregate, plus row counts and truncation flags.
    #: Non-secret by construction: no token, no headers, no response bodies.
    query_provenance: dict = Field(default_factory=dict)
    schema_version: int
    dimensions: list[ExplanationDimensionOut] = Field(default_factory=list)


class DetectionDetailOut(BaseModel):
    """The detector's own working, as it stood when the verdict was reached.

    Assembled from the stored evidence dict by an explicit whitelist rather
    than serialized wholesale: that column is a detector-owned payload whose
    keys vary per detector, and forwarding it verbatim would make any future
    field a silent, unreviewed addition to a public API response.

    Every field is optional. An older anomaly, or one from a detector that does
    not compute a given quantity, simply has no value for it — which the UI
    must render as "not recorded", never as 0.
    """

    #: The detector's own sentence explaining the verdict.
    reason: str | None = None
    #: The expected band the observation was judged against.
    expected_low: float | None = None
    expected_high: float | None = None
    #: The robust z-score threshold that had to be cleared.
    threshold: float | None = None
    baseline_sample_count: int | None = None
    baseline_completeness: float | None = None
    baseline_version: int | None = None

    observed_eps: float | None = None
    expected_eps: float | None = None
    observed_events: float | None = None
    expected_events: float | None = None
    absolute_delta_events: float | None = None
    bucket_seconds: float | None = None

    ratio: float | None = None
    #: "expected_zero" when the baseline expected no traffic, so the ratio does
    #: not exist and the absolute-delta guard carried the verdict alone.
    ratio_basis: str | None = None

    #: DEGENERATE means MAD was zero and the z-score below is an artefact of the
    #: floored scale. The verdict then rests on `fallback_bound`, which is
    #: sound but weaker evidence — the UI must say so next to the confidence.
    robust_score_status: RobustScoreStatus | None = None
    robust_z: float | None = None
    fallback_bound: float | None = None


class AnomalyDetailOut(AnomalyListItem):
    """Everything the investigation page needs for one anomaly."""

    baseline_version: int | None = None
    policy_version: int = 1
    transitions: list[AnomalyTransitionOut] = Field(default_factory=list)
    #: None when evidence was never requested or has not yet been collected.
    explanation_package: ExplanationOut | None = None
    #: None when the anomaly recorded no structured detector evidence.
    detection: DetectionDetailOut | None = None


class BehaviorSummaryOut(BaseModel):
    """Fleet-level behavioral posture for the overview page."""

    open_anomalies: int = 0
    spikes: int = 0
    drops: int = 0
    silent_sources: int = 0
    candidates: int = 0
    recovering: int = 0
    #: Sources whose seasonal cell has too little history to judge. Reported
    #: separately from healthy sources: an unbaselined source is an
    #: observability gap, not a clean bill of health.
    insufficient_data_sources: int = 0
    monitored_sources: int = 0
    #: Explanation jobs still queued, and jobs that ran and could not complete.
    #: Reported separately because a backlog is a capacity signal while a
    #: failure is a defect, and an investigation page with no evidence looks
    #: identical in both cases.
    evidence_pending: int = 0
    evidence_failed: int = 0
    recently_resolved: list[AnomalyListItem] = Field(default_factory=list)
    highest_deviation: list[AnomalyListItem] = Field(default_factory=list)


def detection_detail(details: dict[str, Any] | None) -> DetectionDetailOut | None:
    """Project a stored detector evidence dict onto the typed detection block.

    Reads named keys only, and reads nothing from anywhere but the two known
    levels (`details` and `details["extra"]`). Unrecognized keys are dropped
    rather than passed through, so a detector gaining a new internal fact does
    not thereby publish it.
    """
    if not details:
        return None
    extra = details.get("extra")
    facts: dict[str, Any] = extra if isinstance(extra, dict) else {}

    def num(source: dict[str, Any], key: str) -> float | None:
        value = source.get(key)
        # bool is an int subclass; a flag reaching a numeric field is a bug in
        # the detector, and coercing it to 1.0 would hide that.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def text(source: dict[str, Any], key: str) -> str | None:
        value = source.get(key)
        return value if isinstance(value, str) else None

    sample_count = num(details, "sample_count")
    baseline_version = num(facts, "baseline_version")
    status = text(facts, "robust_score_status")

    return DetectionDetailOut(
        reason=text(details, "reason"),
        expected_low=num(details, "baseline_low"),
        expected_high=num(details, "baseline_high"),
        threshold=num(details, "threshold"),
        baseline_sample_count=int(sample_count) if sample_count is not None else None,
        baseline_completeness=num(facts, "baseline_completeness"),
        baseline_version=int(baseline_version) if baseline_version is not None else None,
        observed_eps=num(facts, "observed_eps"),
        expected_eps=num(facts, "expected_eps"),
        observed_events=num(facts, "observed_events"),
        expected_events=num(facts, "expected_events"),
        absolute_delta_events=num(facts, "absolute_delta_events"),
        bucket_seconds=num(facts, "bucket_seconds"),
        ratio=num(facts, "ratio"),
        ratio_basis=text(facts, "ratio_basis"),
        # An unrecognized status is dropped rather than guessed: claiming the
        # score was OK when we cannot tell would overstate the evidence.
        robust_score_status=(
            RobustScoreStatus(status)
            if status in set(RobustScoreStatus)
            else None
        ),
        robust_z=num(facts, "robust_z"),
        fallback_bound=num(facts, "fallback_bound"),
    )


class PagedAnomalies(BaseModel):
    items: list[AnomalyListItem]
    total: int
    limit: int
    offset: int
