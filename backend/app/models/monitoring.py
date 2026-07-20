"""Operational state for background collection: watermarks and job locks."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CollectionWatermark(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """How far collection has progressed for a (instance, collector kind).

    The watermark is the UTC start of the last fully-collected interval. The
    collector resumes from here, bounded backfill fills gaps, and lag is the
    distance between the watermark and now. Idempotency and gap detection both
    key off this row.
    """

    __tablename__ = "collection_watermark"
    __table_args__ = (
        UniqueConstraint("instance_id", "collector", name="uq_collection_watermark_key"),
        Index("ix_collection_watermark_instance", "instance_id"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE"), nullable=False
    )
    # e.g. "log_source_metric", "offense_snapshot"
    collector: Mapped[str] = mapped_column(String(64), nullable=False)

    # UTC start of the last fully-collected interval.
    watermark_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lag_seconds: Mapped[int | None] = mapped_column(BigInteger)
    intervals_collected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(512))
