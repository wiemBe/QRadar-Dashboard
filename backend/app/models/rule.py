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
from app.models.enums import CoverageStatus


class AnalyticsRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analytics_rule"
    __table_args__ = (
        UniqueConstraint("instance_id", "qradar_id", name="uq_analytics_rule_instance_qradar_id"),
        Index("ix_analytics_rule_enabled", "enabled"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE"), nullable=False
    )
    qradar_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    rule_type: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    origin: Mapped[str | None] = mapped_column(String(64))
    is_building_block: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    owner: Mapped[str | None] = mapped_column(String(255))
    mitre_techniques: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    average_capacity: Mapped[float | None] = mapped_column(Float)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    qradar_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    metrics: Mapped[list[RuleMetric]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<AnalyticsRule {self.name!r} enabled={self.enabled}>"


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


class DetectionCoverage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """MITRE ATT&CK technique coverage rolled up from enabled, healthy rules.

    Coverage is a function of *live* detection, not intent: a technique mapped
    only to a disabled or never-firing rule is DEGRADED, not COVERED. That
    distinction is the entire point of tracking it here rather than in a
    spreadsheet.
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
        String(16), default=CoverageStatus.NOT_COVERED, nullable=False
    )
    mapped_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    firing_rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    coverage_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
