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

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    DimensionAvailability,
    EvidenceStatus,
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

    @property
    def duration_seconds(self) -> float | None:
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


class AnomalyDetailOut(AnomalyListItem):
    """Everything the investigation page needs for one anomaly."""

    log_source_name: str | None = None
    baseline_version: int | None = None
    policy_version: int = 1
    transitions: list[AnomalyTransitionOut] = Field(default_factory=list)
    #: None when evidence was never requested or has not yet been collected.
    explanation_package: ExplanationOut | None = None


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
    recently_resolved: list[AnomalyListItem] = Field(default_factory=list)
    highest_deviation: list[AnomalyListItem] = Field(default_factory=list)


class PagedAnomalies(BaseModel):
    items: list[AnomalyListItem]
    total: int
    limit: int
    offset: int
