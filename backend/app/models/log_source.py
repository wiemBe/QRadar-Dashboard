"""Log-source inventory, time-series metrics, baselines, and anomalies."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    Criticality,
    EvidenceStatus,
    Severity,
)

if TYPE_CHECKING:
    # Runtime import would be circular: explanation.py maps back to
    # LogSourceAnomaly. SQLAlchemy resolves the relationship by class name from
    # the registry, so the annotation alone is enough.
    from app.models.explanation import AnomalyExplanation


class LogSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A QRadar log source plus the SOC-owned metadata QRadar does not track."""

    __tablename__ = "log_source"
    __table_args__ = (
        UniqueConstraint("instance_id", "qradar_id", name="uq_log_source_instance_qradar_id"),
        Index("ix_log_source_monitoring", "monitoring_enabled", "criticality"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE"), nullable=False
    )

    # --- mirrored from QRadar ----------------------------------------------
    qradar_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    type_id: Mapped[int | None] = mapped_column(Integer)
    type_name: Mapped[str | None] = mapped_column(String(255))
    protocol_type_id: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    qradar_status: Mapped[str | None] = mapped_column(String(64))
    credibility: Mapped[int | None] = mapped_column(Integer)
    target_event_collector_id: Mapped[int | None] = mapped_column(Integer)
    last_event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- SOC-owned metadata -------------------------------------------------
    criticality: Mapped[Criticality] = mapped_column(
        String(16), default=Criticality.MEDIUM, nullable=False
    )
    owner: Mapped[str | None] = mapped_column(String(255))
    owner_email: Mapped[str | None] = mapped_column(String(255))

    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    maintenance_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    maintenance_reason: Mapped[str | None] = mapped_column(Text)

    # Expected reporting cadence. A source that only reports during business
    # hours must not raise NO_EVENTS overnight.
    expected_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    business_hours_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    business_hours_start: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    business_hours_end: Mapped[int] = mapped_column(Integer, default=18, nullable=False)
    # ISO weekdays, 1=Monday .. 7=Sunday
    business_days: Mapped[list[int]] = mapped_column(
        JSONB, default=lambda: [1, 2, 3, 4, 5], nullable=False
    )
    timezone_name: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)

    # Per-source overrides for anomaly thresholds; merged over the global
    # defaults at evaluation time. Shape validated by the ThresholdConfig schema.
    custom_thresholds: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # --- derived ------------------------------------------------------------
    health_score: Mapped[float | None] = mapped_column(Float)
    health_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    instance: Mapped[QRadarInstance] = relationship(back_populates="log_sources")  # noqa: F821
    metrics: Mapped[list[LogSourceMetric]] = relationship(
        back_populates="log_source", cascade="all, delete-orphan"
    )
    baselines: Mapped[list[LogSourceBaseline]] = relationship(
        back_populates="log_source", cascade="all, delete-orphan"
    )
    anomalies: Mapped[list[LogSourceAnomaly]] = relationship(
        back_populates="log_source", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<LogSource {self.name} qradar_id={self.qradar_id}>"


class LogSourceMetric(Base):
    """Time-series observation for one log source over one collection window.

    TimescaleDB hypertable partitioned on `bucket_start`. The composite primary
    key includes the partitioning column because Timescale requires it.
    """

    __tablename__ = "log_source_metric"
    __table_args__ = (
        Index("ix_log_source_metric_source_time", "log_source_id", "bucket_start"),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bucket_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)

    # --- volume -------------------------------------------------------------
    event_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    average_eps: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    peak_eps: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_delay_seconds: Mapped[float | None] = mapped_column(Float)

    # --- parsing quality ----------------------------------------------------
    unknown_event_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    stored_event_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    parsed_username_ratio: Mapped[float | None] = mapped_column(Float)
    parsed_source_ip_ratio: Mapped[float | None] = mapped_column(Float)

    # --- cardinality --------------------------------------------------------
    distinct_qid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    distinct_username_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    distinct_source_ip_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- collection health --------------------------------------------------
    collection_error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Hash of a representative payload for this interval, for REPEATED_PAYLOAD
    # detection across consecutive intervals.
    payload_signature: Mapped[str | None] = mapped_column(String(64))

    # --- Phase A: provenance and completeness -------------------------------
    # Whether the collector observed the whole interval. Only COMPLETE buckets
    # enter a baseline or produce a verdict; a PARTIAL bucket displayed as a
    # real observation would turn a collection outage into a false VOLUME_DROP
    # across every source at once.
    completeness: Mapped[BucketCompleteness] = mapped_column(
        String(16), default=BucketCompleteness.COMPLETE, nullable=False
    )
    first_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Which subsystem produced this row, e.g. "ariel_aggregate" or "mock".
    collection_source: Mapped[str | None] = mapped_column(String(32))
    # Non-secret evidence of how the row was produced: AQL text, window bounds,
    # row counts, truncation flags. Never credentials, tokens or headers.
    query_provenance: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    collection_duration_ms: Mapped[int | None] = mapped_column(Integer)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Watermark in force when this bucket was written, so a later reader can
    # tell whether the row predates a backfill correction.
    watermark_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    log_source: Mapped[LogSource] = relationship(back_populates="metrics")

    @property
    def bucket_end(self) -> datetime:
        """Exclusive upper bound of the half-open bucket [start, end)."""
        return self.bucket_start + timedelta(seconds=self.bucket_seconds)

    @property
    def is_complete(self) -> bool:
        # `==`, not `is`: these enum columns are String-typed, so a value loaded
        # from the database is a plain str rather than the StrEnum member.
        return self.completeness == BucketCompleteness.COMPLETE


class LogSourceBaseline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Robust baseline for one (log source, metric, weekday, hour) cell.

    Median and MAD rather than mean and standard deviation: SIEM volume data is
    heavy-tailed and a single incident spike would inflate a standard deviation
    enough to blind the detector for weeks afterwards. No ML in v1 — this is
    explainable to an analyst and reproducible from the stored samples.
    """

    __tablename__ = "log_source_baseline"
    __table_args__ = (
        UniqueConstraint(
            "log_source_id", "metric_name", "weekday", "hour", name="uq_log_source_baseline_cell"
        ),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="weekday_range"),
        CheckConstraint("hour BETWEEN 0 AND 23", name="hour_range"),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE"), nullable=False
    )
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    hour: Mapped[int] = mapped_column(Integer, nullable=False)

    median: Mapped[float] = mapped_column(Float, nullable=False)
    mad: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    p05: Mapped[float | None] = mapped_column(Float)
    p95: Mapped[float | None] = mapped_column(Float)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Below the minimum sample count the baseline exists but must not be used
    # for alerting. Surfacing "still learning" beats emitting false positives.
    is_reliable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Monotonic version bumped on each recomputation, so an execution/anomaly can
    # record which baseline it was judged against and a later reader can tell the
    # baseline moved. The actual observations used are retained for auditability
    # and reproducibility (bounded sample; not the full history).
    baseline_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    observations: Mapped[list[float]] = mapped_column(JSONB, default=list, nullable=False)

    # --- Phase A ------------------------------------------------------------
    # Fraction of candidate buckets in the lookback that survived exclusion and
    # actually backed this cell, in [0, 1]. A cell built from 4 of 28 available
    # buckets is far weaker than one built from 26, even when both clear the
    # minimum sample count, and an operator needs to see the difference.
    completeness: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # How many candidate buckets were dropped, and why, so an unexpectedly thin
    # baseline is diagnosable without re-running the build.
    excluded_sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exclusion_counts: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    log_source: Mapped[LogSource] = relationship(back_populates="baselines")

    @property
    def seasonality_key(self) -> str:
        """Phase A seasonality: ISO weekday x hour of day (168 cells)."""
        return f"w{self.weekday}h{self.hour:02d}"


class LogSourceDetectorState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Hysteresis state per (log source, anomaly type).

    Anomaly alerts flap without hysteresis: a metric bouncing across a threshold
    would open and resolve repeatedly. We require N consecutive anomalous
    intervals to open and M consecutive healthy intervals to resolve. This row
    holds the running consecutive counters and the last interval processed
    (which also makes evaluation idempotent — re-processing the same interval is
    a no-op).
    """

    __tablename__ = "log_source_detector_state"
    __table_args__ = (
        UniqueConstraint(
            "log_source_id", "anomaly_type", name="uq_detector_state_source_type"
        ),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE"), nullable=False
    )
    anomaly_type: Mapped[AnomalyType] = mapped_column(String(32), nullable=False)

    consecutive_anomalous: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_healthy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_interval_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    open_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    # Phase A explicit lifecycle. `is_open` is retained as the Phase 3 spelling
    # of "an incident exists" and stays consistent with `state`, so existing
    # alerting keeps working while the finer states drive the investigation UI.
    state: Mapped[AnomalyState] = mapped_column(
        String(24), default=AnomalyState.NORMAL, nullable=False
    )
    # Incident currently being tracked through CANDIDATE -> RESOLVED. Distinct
    # from open_anomaly_id, which only exists once the incident is OPEN.
    active_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))


class LogSourceAnomaly(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A detected deviation. Distinct from an Alert: an anomaly is an
    observation, an alert is the notified lifecycle object built from it."""

    __tablename__ = "log_source_anomaly"
    __table_args__ = (
        Index("ix_log_source_anomaly_open", "log_source_id", "anomaly_type", "resolved_at"),
        Index("ix_log_source_anomaly_detected", "detected_at"),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE"), nullable=False
    )
    anomaly_type: Mapped[AnomalyType] = mapped_column(String(32), nullable=False)
    severity: Mapped[Severity] = mapped_column(String(16), nullable=False)

    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    observed_value: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    deviation_score: Mapped[float | None] = mapped_column(Float)
    # Human-readable reason, rendered server-side so the UI never has to
    # reconstruct detection logic.
    explanation: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Set when the anomaly fired while the source was in maintenance: recorded
    # for history but never notified.
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(255))

    # --- Phase A lifecycle --------------------------------------------------
    state: Mapped[AnomalyState] = mapped_column(
        String(24), default=AnomalyState.CANDIDATE, nullable=False
    )
    # detected_at is when the engine first saw the deviation; opened_at is when
    # it was promoted to an incident. They differ by the confirmation delay,
    # and reporting mean-time-to-detect needs both.
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bounds of the abnormal telemetry itself, which is what the explanation
    # job queries. Distinct from detected_at/resolved_at, which are engine
    # clock times.
    anomaly_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anomaly_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    confidence: Mapped[float | None] = mapped_column(Float)
    # observed / expected. Stored alongside the robust score because the ratio
    # is what an operator reasons about and the z-score is what fired.
    deviation_ratio: Mapped[float | None] = mapped_column(Float)
    robust_z: Mapped[float | None] = mapped_column(Float)
    # Signed observed - expected, in metric units.
    absolute_delta: Mapped[float | None] = mapped_column(Float)
    consecutive_buckets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Which baseline and which detection policy produced this verdict, so a
    # later reader can tell whether it would still fire under current rules.
    baseline_version: Mapped[int | None] = mapped_column(Integer)
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    evidence_status: Mapped[EvidenceStatus] = mapped_column(
        String(16), default=EvidenceStatus.NOT_REQUESTED, nullable=False
    )

    log_source: Mapped[LogSource] = relationship(back_populates="anomalies")
    transitions: Mapped[list[AnomalyStateTransition]] = relationship(
        back_populates="anomaly",
        cascade="all, delete-orphan",
        order_by="AnomalyStateTransition.occurred_at",
    )
    # Named `explanation_package`, not `explanation`: the Text column above is
    # the Phase 3 one-line detector reason and keeps that name.
    explanation_package: Mapped[AnomalyExplanation | None] = relationship(
        back_populates="anomaly", cascade="all, delete-orphan", uselist=False
    )

    def __repr__(self) -> str:
        return f"<LogSourceAnomaly {self.anomaly_type} state={self.state}>"


class AnomalyStateTransition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Auditable record of one anomaly lifecycle transition.

    Every state change is persisted rather than only the current state, because
    "this incident flapped CANDIDATE/NORMAL six times before opening" is an
    operator-visible fact about detector tuning that a single mutable column
    destroys.
    """

    __tablename__ = "anomaly_state_transition"
    __table_args__ = (
        Index("ix_anomaly_transition_anomaly", "anomaly_id", "occurred_at"),
    )

    anomaly_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source_anomaly.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_state: Mapped[AnomalyState | None] = mapped_column(String(24))
    to_state: Mapped[AnomalyState] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The metric bucket that drove the transition, when one did. Manual
    # suppression has no triggering bucket.
    bucket_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(64), default="anomaly-engine", nullable=False)
    observed_value: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)

    anomaly: Mapped[LogSourceAnomaly] = relationship(back_populates="transitions")
