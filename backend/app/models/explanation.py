"""Bounded explanation evidence for a log-source volume anomaly.

Three tables rather than one JSON blob:

  * ``AnomalyExplanation``            — the package: windows, strategy, status.
  * ``AnomalyExplanationDimension``   — one row per dimension analyzed, holding
    the per-dimension aggregates (cardinality change, concentration, new and
    disappeared value counts) and that dimension's availability.
  * ``AnomalyExplanationContributor`` — one row per (dimension, value), holding
    the baseline/anomaly counts and the contribution arithmetic.

Contributors are typed and queryable on purpose. "Which source IPs contributed
to more than one anomaly this week?" is the question the product exists to
answer, and it is unanswerable against an opaque JSONB document. Only genuinely
shapeless, bounded payloads (raw AQL provenance, sanitized errors) stay JSON.

Nothing here stores raw events, payloads, credentials, tokens or headers.
"""

from __future__ import annotations

import uuid
from datetime import datetime
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
from app.models.enums import DimensionAvailability, EvidenceStatus

if TYPE_CHECKING:
    # Runtime import would be circular: log_source.py maps back to
    # AnomalyExplanation. SQLAlchemy resolves the relationship by class name
    # from the registry, so the annotation alone is enough.
    from app.models.log_source import LogSourceAnomaly

#: Bumped when the stored shape changes, so an older package stays readable.
EXPLANATION_SCHEMA_VERSION = 1


class AnomalyExplanation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One bounded evidence package answering "what changed?" for an anomaly."""

    __tablename__ = "anomaly_explanation"
    __table_args__ = (
        UniqueConstraint("anomaly_id", name="uq_anomaly_explanation_anomaly"),
        Index("ix_anomaly_explanation_status", "status"),
    )

    anomaly_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source_anomaly.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[EvidenceStatus] = mapped_column(
        String(16), default=EvidenceStatus.PENDING, nullable=False
    )
    # Sanitized failure reason. Never a provider response body, never headers.
    error: Mapped[str | None] = mapped_column(Text)

    # --- windows ------------------------------------------------------------
    # The abnormal interval, and the recent-normal interval it is compared
    # against. Both half-open UTC [start, end).
    anomaly_window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    anomaly_window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    baseline_window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    baseline_window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # e.g. "recent_normal_window" — how the comparison window was chosen.
    comparison_strategy: Mapped[str] = mapped_column(String(48), nullable=False)

    # --- totals -------------------------------------------------------------
    anomaly_total_events: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    baseline_total_events: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # --- provenance ---------------------------------------------------------
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collection_duration_ms: Mapped[int | None] = mapped_column(Integer)
    # Per-dimension AQL text and row counts. Bounded, non-secret.
    query_provenance: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    schema_version: Mapped[int] = mapped_column(
        Integer, default=EXPLANATION_SCHEMA_VERSION, nullable=False
    )

    anomaly: Mapped[LogSourceAnomaly] = relationship(
        back_populates="explanation_package"
    )
    dimensions: Mapped[list[AnomalyExplanationDimension]] = relationship(
        back_populates="explanation", cascade="all, delete-orphan"
    )
    contributors: Mapped[list[AnomalyExplanationContributor]] = relationship(
        back_populates="explanation", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<AnomalyExplanation anomaly={self.anomaly_id} status={self.status}>"


class AnomalyExplanationDimension(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-dimension aggregate result within an explanation package.

    A dimension row always exists for every dimension the policy requested,
    even when the field is absent from the source's DSM output — that row then
    carries ``UNAVAILABLE``. Omitting it would make "we did not look" and "we
    looked and found nothing" indistinguishable.
    """

    __tablename__ = "anomaly_explanation_dimension"
    __table_args__ = (
        UniqueConstraint(
            "explanation_id", "dimension", name="uq_explanation_dimension"
        ),
    )

    explanation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("anomaly_explanation.id", ondelete="CASCADE"),
        nullable=False,
    )
    # e.g. "source_ip", "destination_port", "qid", "action".
    dimension: Mapped[str] = mapped_column(String(48), nullable=False)
    availability: Mapped[DimensionAvailability] = mapped_column(
        String(16), default=DimensionAvailability.AVAILABLE, nullable=False
    )
    # Sanitized reason when availability is UNAVAILABLE or FAILED.
    detail: Mapped[str | None] = mapped_column(Text)

    # --- cardinality --------------------------------------------------------
    baseline_distinct_count: Mapped[int | None] = mapped_column(Integer)
    anomaly_distinct_count: Mapped[int | None] = mapped_column(Integer)
    # anomaly_distinct / baseline_distinct; NULL when the baseline count is 0,
    # because "8.4x" and "from nothing" are different findings.
    cardinality_ratio: Mapped[float | None] = mapped_column(Float)

    new_value_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disappeared_value_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- concentration ------------------------------------------------------
    # Share of window volume held by the top value, in [0, 1]. A jump here is
    # the signature of a single talker dominating an otherwise diverse source.
    baseline_top_share: Mapped[float | None] = mapped_column(Float)
    anomaly_top_share: Mapped[float | None] = mapped_column(Float)

    # True when the per-dimension result hit the configured value cap, so the
    # contributor list below is a prefix rather than the whole picture.
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    explanation: Mapped[AnomalyExplanation] = relationship(back_populates="dimensions")


class AnomalyExplanationContributor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One (dimension, value) pair and its contribution to the volume change."""

    __tablename__ = "anomaly_explanation_contributor"
    __table_args__ = (
        UniqueConstraint(
            "explanation_id", "dimension", "value", name="uq_explanation_contributor"
        ),
        Index("ix_explanation_contributor_rank", "explanation_id", "dimension", "rank"),
        # Contribution share is a fraction of a single window's change; values
        # outside [-1, 1] mean the arithmetic is wrong, not that the anomaly is
        # extreme, so reject them at the boundary.
        CheckConstraint(
            "contribution_share IS NULL OR contribution_share BETWEEN -1.0 AND 1.0",
            name="contribution_share_range",
        ),
    )

    explanation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("anomaly_explanation.id", ondelete="CASCADE"),
        nullable=False,
    )
    dimension: Mapped[str] = mapped_column(String(48), nullable=False)
    # The observed field value, as returned by Ariel and stringified. Bounded
    # in length; an over-long value is truncated rather than rejected.
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    # Human-friendly form where one exists (QID -> event name). Never invented.
    label: Mapped[str | None] = mapped_column(String(255))

    baseline_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    anomaly_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    absolute_delta: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # (anomaly - baseline) / baseline. NULL when baseline_count is 0: a new
    # value has no percentage change, and reporting one would be an invention.
    percent_delta: Mapped[float | None] = mapped_column(Float)
    # This value's share of the anomaly window's total volume, in [0, 1].
    anomaly_share: Mapped[float | None] = mapped_column(Float)
    baseline_share: Mapped[float | None] = mapped_column(Float)
    # This value's share of the window-over-window change. Signed: negative for
    # a value that shrank inside an overall increase.
    contribution_share: Mapped[float | None] = mapped_column(Float)

    baseline_rank: Mapped[int | None] = mapped_column(Integer)
    anomaly_rank: Mapped[int | None] = mapped_column(Integer)
    # Ordering within the dimension, by descending |contribution|.
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    is_new: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_disappeared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    explanation: Mapped[AnomalyExplanation] = relationship(back_populates="contributors")

    def __repr__(self) -> str:
        return f"<Contributor {self.dimension}={self.value} delta={self.absolute_delta}>"
