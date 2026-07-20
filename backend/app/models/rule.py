"""AnalyticsRule, its firing metrics, and MITRE detection coverage."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    CoverageStatus,
    MappingSource,
    RuleDependencyKind,
    RuleHealthStatus,
)


class AnalyticsRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A QRadar analytics rule or building block, plus SOC-owned metadata.

    The QRadar-mirrored columns are overwritten on every synchronization; the
    SOC-owned block below is never touched by a sync. That split is what makes
    it safe to re-sync at any time without losing analyst curation.
    """

    __tablename__ = "analytics_rule"
    __table_args__ = (
        UniqueConstraint("instance_id", "qradar_id", name="uq_analytics_rule_instance_qradar_id"),
        Index("ix_analytics_rule_enabled", "enabled"),
        Index("ix_analytics_rule_building_block", "instance_id", "is_building_block"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE"), nullable=False
    )

    # --- mirrored from QRadar ------------------------------------------------
    qradar_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    rule_type: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    origin: Mapped[str | None] = mapped_column(String(64))
    is_building_block: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    average_capacity: Mapped[float | None] = mapped_column(Float)
    qradar_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    qradar_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Response configuration. `generates_offense` is tracked separately from
    # `enabled` because a rule can fire constantly and still create no offense,
    # which is a different finding from a rule that never fires.
    generates_offense: Mapped[bool | None] = mapped_column(Boolean)
    response_actions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    # --- SOC-owned metadata (preserved across synchronization) --------------
    owner: Mapped[str | None] = mapped_column(String(255))
    # Authoritative MITRE mapping, curated by the SOC. Inferred technique
    # mappings live in TechniqueMapping with a confidence, never here.
    mitre_techniques: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    soc_notes: Mapped[str | None] = mapped_column(Text)
    # Expected firings per day, used as the noisy/inactive reference point when
    # set. NULL means "use the configured global defaults".
    expected_daily_firings: Mapped[float | None] = mapped_column(Float)
    # Excluded from rule-health alerting (e.g. a deliberately dormant rule).
    health_monitoring_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    # --- observed ------------------------------------------------------------
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_contribution_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    offense_contribution_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # --- derived health ------------------------------------------------------
    health_status: Mapped[RuleHealthStatus] = mapped_column(
        String(24), default=RuleHealthStatus.UNKNOWN, nullable=False
    )
    health_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    metrics: Mapped[list[RuleMetric]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )
    dependencies: Mapped[list[RuleDependency]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<AnalyticsRule {self.name!r} enabled={self.enabled}>"


class RuleDependency(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """What a rule needs in order to be able to fire.

    Explicit dependencies come from QRadar or SOC curation. Inferred ones are
    the platform's best guess and carry a confidence and a provenance string;
    they are never presented as fact, and coverage evaluation weights them by
    confidence rather than treating them as established.
    """

    __tablename__ = "rule_dependency"
    __table_args__ = (
        UniqueConstraint(
            "rule_id", "kind", "target_ref", name="uq_rule_dependency_rule_kind_target"
        ),
        Index("ix_rule_dependency_target", "kind", "target_ref"),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("analytics_rule.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[RuleDependencyKind] = mapped_column(String(24), nullable=False)
    # Building-block QRadar id, log-source-type id, or log-source qradar id,
    # stored as text so one column serves all three kinds.
    target_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    target_name: Mapped[str | None] = mapped_column(String(512))

    source: Mapped[MappingSource] = mapped_column(
        String(16), default=MappingSource.INFERRED, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    provenance: Mapped[str | None] = mapped_column(String(255))

    rule: Mapped[AnalyticsRule] = relationship(back_populates="dependencies")


class RuleStateTransition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An observed enabled<->disabled flip, recorded at synchronization time.

    QRadar does not tell us who disabled a rule or when, so the platform records
    the transition it *observed*. `observed_at` is deliberately named to make
    clear this is detection time, not the actual change time.
    """

    __tablename__ = "rule_state_transition"
    __table_args__ = (Index("ix_rule_state_transition_rule_time", "rule_id", "observed_at"),)

    rule_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("analytics_rule.id", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    current_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)


class RuleHealthSnapshot(Base):
    """Point-in-time rule-health verdict with the evidence behind it.

    Stored as a time series so "when did this rule go quiet" is answerable, and
    so a classification can always be re-justified after the fact. `logic_version`
    records which evaluator produced the verdict: changing the rules of
    classification must not silently rewrite the meaning of historical rows.
    """

    __tablename__ = "rule_health_snapshot"
    __table_args__ = (
        Index("ix_rule_health_snapshot_rule_time", "rule_id", "evaluated_at"),
        Index("ix_rule_health_snapshot_status", "status", "evaluated_at"),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("analytics_rule.id", ondelete="CASCADE"),
        primary_key=True,
    )
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    status: Mapped[RuleHealthStatus] = mapped_column(String(24), nullable=False)
    logic_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)

    # --- evidence ------------------------------------------------------------
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trigger_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    offense_contribution_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    expected_daily_firings: Mapped[float | None] = mapped_column(Float)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    building_blocks_healthy: Mapped[bool | None] = mapped_column(Boolean)
    required_log_sources_healthy: Mapped[bool | None] = mapped_column(Boolean)
    missing_dependencies: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class RuleMetric(Base):
    """Time-series of rule firing volume, driving noisy-rule and stale-rule views."""

    __tablename__ = "rule_metric"
    __table_args__ = (Index("ix_rule_metric_rule_time", "rule_id", "bucket_start"),)

    rule_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("analytics_rule.id", ondelete="CASCADE"), primary_key=True
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bucket_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)

    fire_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    offense_contribution_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Fraction of firings a human dispositioned as noise, if fed back from a
    # case system; NULL when unknown.
    false_positive_ratio: Mapped[float | None] = mapped_column(Float)

    rule: Mapped[AnalyticsRule] = relationship(back_populates="metrics")


class TechniqueMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """SOC-owned MITRE technique <-> rule mapping.

    This is the authoritative mapping store. Technique identifiers and any
    descriptive text are supplied by the SOC; the platform never imports MITRE
    content from an outside source, so nothing here can be stale or wrong in a
    way the SOC did not choose.

    `source` distinguishes a curated mapping from one the platform inferred.
    Inferred rows always carry a confidence below 1.0 and a provenance string,
    and the coverage API keeps the two kinds separable end to end.
    """

    __tablename__ = "technique_mapping"
    __table_args__ = (
        UniqueConstraint(
            "instance_id", "technique_id", "rule_id", name="uq_technique_mapping_technique_rule"
        ),
        Index("ix_technique_mapping_technique", "instance_id", "technique_id"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE"), nullable=False
    )
    technique_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # SOC-owned description. Left NULL rather than guessed when unknown.
    technique_name: Mapped[str | None] = mapped_column(String(255))
    tactic: Mapped[str | None] = mapped_column(String(128))

    rule_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("analytics_rule.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[MappingSource] = mapped_column(
        String(16), default=MappingSource.EXPLICIT, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    provenance: Mapped[str | None] = mapped_column(String(255))


class DetectionCoverage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current MITRE ATT&CK technique coverage, rolled up from live detection.

    Coverage is a function of *working* detection, not intent: a technique
    mapped only to a disabled rule, or to a rule whose required telemetry is
    down, is never COVERED. That distinction is the entire point of tracking it
    here rather than in a spreadsheet.
    """

    __tablename__ = "detection_coverage"
    __table_args__ = (
        UniqueConstraint("instance_id", "technique_id", name="uq_detection_coverage_technique"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE"), nullable=False
    )
    technique_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    technique_name: Mapped[str | None] = mapped_column(String(255))
    tactic: Mapped[str | None] = mapped_column(String(128))

    status: Mapped[CoverageStatus] = mapped_column(
        String(16), default=CoverageStatus.NOT_EVALUATED, nullable=False
    )
    mapped_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    firing_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Rules mapped only by inference. Reported separately so an operator can see
    # how much of a technique's coverage rests on a guess.
    inferred_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    degraded_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    coverage_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Lowest confidence among the mappings that produced this verdict.
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    logic_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    # Per-rule evidence chain: rule -> rule health -> building blocks ->
    # required log sources -> telemetry health, as evaluated.
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class DetectionCoverageSnapshot(Base):
    """Coverage history. Answers "when did we lose coverage of T1078?"."""

    __tablename__ = "detection_coverage_snapshot"
    __table_args__ = (
        Index(
            "ix_coverage_snapshot_technique_time",
            "instance_id",
            "technique_id",
            "captured_at",
        ),
        Index("ix_coverage_snapshot_captured", "captured_at"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    technique_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    status: Mapped[CoverageStatus] = mapped_column(String(16), nullable=False)
    coverage_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    mapped_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    firing_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    degraded_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    logic_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
