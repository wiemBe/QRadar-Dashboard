"""Alert lifecycle and notification dispatch records.

Lifecycle: OPEN -> ACKNOWLEDGED -> RESOLVED.

Deduplication is structural, not procedural: `dedup_key` is unique among
non-resolved alerts via a partial index, so a second detection of the same
condition while an alert is still open cannot create a duplicate row, and
therefore cannot fire a duplicate notification.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AlertStatus,
    AlertTransition,
    NotificationChannel,
    NotificationStatus,
    Severity,
)


class Alert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alert"
    __table_args__ = (
        # Enforces "at most one open alert per condition". The WHERE clause makes
        # it a partial unique index so resolved alerts don't block a genuine new
        # occurrence later.
        Index(
            "uq_alert_active_dedup",
            "dedup_key",
            unique=True,
            postgresql_where=text("status <> 'RESOLVED'"),
        ),
        Index("ix_alert_status_severity", "status", "severity"),
        Index("ix_alert_opened", "opened_at"),
    )

    # Stable fingerprint of the underlying condition, computed at the
    # application layer (app.alerts.fingerprint). `dedup_key` and `fingerprint`
    # hold the same value: dedup_key is the indexed dedup column, fingerprint is
    # the explicit domain field the API exposes.
    dedup_key: Mapped[str] = mapped_column(String(255), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[Severity] = mapped_column(String(16), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(
        String(16), default=AlertStatus.OPEN, nullable=False
    )

    # Loose typed link back to the originating object; kept as type+id rather
    # than a hard FK because an alert can originate from several source tables.
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    context: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(255))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(255))
    resolution_reason: Mapped[str | None] = mapped_column(Text)

    # Number of times the condition re-fired while this alert stayed open.
    # Incremented instead of creating a new alert — the count is useful signal,
    # a duplicate notification is not.
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Snapshot of the evidence at the most recent detection, so the alert is
    # self-contained for the UI and for after-the-fact review even if the
    # underlying anomaly rows are pruned.
    evidence_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # IDs of the LogSourceAnomaly rows that contributed to this alert.
    source_anomaly_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    notifications: Mapped[list[AlertNotification]] = relationship(
        back_populates="alert", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Alert {self.dedup_key} status={self.status}>"


class AlertNotification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One delivery attempt-set for one (alert, transition, channel).

    The uniqueness constraint enforces "no duplicate notification for the same
    alert transition on the same channel" at the database level — the
    application also checks before enqueueing, but this is the safety net.
    """

    __tablename__ = "alert_notification"
    __table_args__ = (
        Index("ix_alert_notification_alert", "alert_id", "channel"),
        Index("ix_alert_notification_dispatch", "status", "next_attempt_at"),
        UniqueConstraint(
            "alert_id", "transition", "channel", "target",
            name="uq_alert_notification_transition",
        ),
    )

    alert_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("alert.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[NotificationChannel] = mapped_column(String(16), nullable=False)
    target: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # Which lifecycle transition this notification represents.
    transition: Mapped[AlertTransition] = mapped_column(String(16), nullable=False)

    status: Mapped[NotificationStatus] = mapped_column(
        String(16), default=NotificationStatus.PENDING, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Rendered payload retained for the delivery audit trail (secrets redacted).
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    alert: Mapped[Alert] = relationship(back_populates="notifications")
