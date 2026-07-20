"""Configuration drift tracking.

A ConfigurationSnapshot is a hash + payload of a config object at a point in
time; a ConfigurationChange is the diff between two consecutive snapshots. This
answers "who changed the rule set / reference data / log-source config, and
when" without QRadar audit access.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ChangeType


class ConfigurationSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "configuration_snapshot"
    __table_args__ = (
        Index("ix_configuration_snapshot_object", "instance_id", "object_type", "object_id",
              "captured_at"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE"), nullable=False
    )
    # e.g. "analytics_rule", "reference_set", "log_source"
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    object_name: Mapped[str | None] = mapped_column(String(512))

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    changes: Mapped[list[ConfigurationChange]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class ConfigurationChange(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "configuration_change"
    __table_args__ = (
        Index("ix_configuration_change_detected", "detected_at"),
        Index("ix_configuration_change_object", "object_type", "object_id"),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("configuration_snapshot.id", ondelete="CASCADE"),
        nullable=False,
    )
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    object_name: Mapped[str | None] = mapped_column(String(512))

    change_type: Mapped[ChangeType] = mapped_column(String(16), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(255))

    previous_hash: Mapped[str | None] = mapped_column(String(64))
    current_hash: Mapped[str | None] = mapped_column(String(64))
    # Field-level diff: {field: {"before": ..., "after": ...}}
    diff: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    snapshot: Mapped[ConfigurationSnapshot] = relationship(back_populates="changes")
