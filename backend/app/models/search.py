"""Scheduled AQL searches, their executions, and aggregated result metrics."""

from __future__ import annotations

import uuid
from datetime import datetime

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
from app.models.enums import ExecutionStatus, Severity, VisualizationType


class ScheduledSearch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An operator-defined AQL search executed on a schedule.

    The AQL is *never* supplied by an anonymous frontend request at execution
    time. A search is authored, validated, versioned and stored; the scheduler
    then executes the stored, already-validated query by id.
    """

    __tablename__ = "scheduled_search"
    __table_args__ = (
        CheckConstraint("timeout_seconds > 0", name="timeout_positive"),
        CheckConstraint("max_time_range_hours > 0", name="time_range_positive"),
        CheckConstraint("max_result_rows > 0", name="result_rows_positive"),
        Index("ix_scheduled_search_enabled", "enabled", "next_run_at"),
    )

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(128), index=True)

    # Comma-free list of ATT&CK technique ids, e.g. ["T1078", "T1110.001"]
    mitre_techniques: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    aql_query: Mapped[str] = mapped_column(Text, nullable=False)
    query_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Cron expression driving the schedule (Celery beat / APScheduler).
    schedule_cron: Mapped[str] = mapped_column(String(128), nullable=False)
    schedule_timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)

    # Alerting threshold applied to the aggregated result.
    threshold_value: Mapped[float | None] = mapped_column(Float)
    threshold_operator: Mapped[str] = mapped_column(String(4), default="GT", nullable=False)
    severity: Mapped[Severity] = mapped_column(String(16), default=Severity.MEDIUM, nullable=False)

    # --- execution guardrails (all enforced server-side before dispatch) ----
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    max_time_range_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    max_result_rows: Mapped[int] = mapped_column(Integer, default=10_000, nullable=False)

    visualization_type: Mapped[VisualizationType] = mapped_column(
        String(16), default=VisualizationType.TABLE, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    versions: Mapped[list[SearchQueryVersion]] = relationship(
        back_populates="search", cascade="all, delete-orphan", order_by="SearchQueryVersion.version"
    )
    executions: Mapped[list[SearchExecution]] = relationship(
        back_populates="search", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ScheduledSearch {self.name} v{self.query_version}>"


class SearchQueryVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable history of a search's AQL.

    Kept so a result trend can be read honestly: if the query changed, the
    trend before and after is not comparable, and the UI must say so.
    """

    __tablename__ = "search_query_version"
    __table_args__ = (
        UniqueConstraint("search_id", "version", name="uq_search_query_version_search_version"),
    )

    search_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("scheduled_search.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    aql_query: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(255))
    change_note: Mapped[str | None] = mapped_column(Text)

    search: Mapped[ScheduledSearch] = relationship(back_populates="versions")


class SearchExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One run of a scheduled search, successful or not."""

    __tablename__ = "search_execution"
    __table_args__ = (
        Index("ix_search_execution_search_started", "search_id", "started_at"),
        Index("ix_search_execution_status", "status"),
        # One execution row per logical run. Retries reuse it; this is the final
        # guard against duplicate SearchExecution records under concurrency.
        UniqueConstraint("search_id", "run_key", name="uq_search_execution_run_key"),
    )

    search_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("scheduled_search.id", ondelete="CASCADE"), nullable=False
    )
    query_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Idempotency key for one logical run (a scheduled tick or a manual trigger).
    # Retries reuse the same execution row rather than creating a new one, so the
    # unique constraint below makes duplicate SearchExecutions structurally
    # impossible even under concurrent dispatch.
    run_key: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), default="SCHEDULED", nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(255))

    status: Mapped[ExecutionStatus] = mapped_column(
        String(16), default=ExecutionStatus.PENDING, nullable=False
    )
    # QRadar's Ariel search handle, retained for correlation in QRadar's own logs.
    ariel_search_id: Mapped[str | None] = mapped_column(String(128))
    ariel_status: Mapped[str | None] = mapped_column(String(32))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    result_count: Mapped[int | None] = mapped_column(BigInteger)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Holds an ExecutionErrorType value (kept as a string column so adding a new
    # failure mode does not require a DB enum migration).
    error_type: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    threshold_breached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    search: Mapped[ScheduledSearch] = relationship(back_populates="executions")
    result_metrics: Mapped[list[SearchResultMetric]] = relationship(
        back_populates="execution", cascade="all, delete-orphan"
    )


class SearchResultMetric(Base):
    """Aggregated search output.

    Raw events are deliberately not persisted: they duplicate the SIEM's own
    storage, multiply the blast radius of a breach of this platform, and drag
    regulated data into a system with a different retention policy. We store
    counts and grouped aggregates. `dimensions` holds the GROUP BY key.
    """

    __tablename__ = "search_result_metric"
    __table_args__ = (
        Index("ix_search_result_metric_search_time", "search_id", "bucket_start"),
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("search_execution.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    metric_key: Mapped[str] = mapped_column(String(255), primary_key=True, default="total")

    search_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("scheduled_search.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    execution: Mapped[SearchExecution] = relationship(back_populates="result_metrics")
