"""OffenseSnapshot — periodic point-in-time capture of QRadar offense state.

Snapshots rather than a mutable mirror: offense aging, assignment latency and
"how long did this sit unassigned" are questions about *history*, and a table
that is overwritten in place cannot answer them.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OffenseSnapshot(Base):
    __tablename__ = "offense_snapshot"
    __table_args__ = (
        Index("ix_offense_snapshot_captured", "captured_at"),
        Index("ix_offense_snapshot_offense", "instance_id", "qradar_offense_id", "captured_at"),
        Index("ix_offense_snapshot_open", "status", "captured_at"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    qradar_offense_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(32))
    magnitude: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[int | None] = mapped_column(Integer)
    credibility: Mapped[int | None] = mapped_column(Integer)
    relevance: Mapped[int | None] = mapped_column(Integer)

    assigned_to: Mapped[str | None] = mapped_column(String(255))
    # Denormalized so the "unassigned offenses" dashboard is a single indexed
    # scan instead of a NULL check across a wide table.
    is_assigned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    offense_type: Mapped[int | None] = mapped_column(Integer)
    offense_source: Mapped[str | None] = mapped_column(String(255))
    event_count: Mapped[int | None] = mapped_column(BigInteger)
    flow_count: Mapped[int | None] = mapped_column(BigInteger)
    device_count: Mapped[int | None] = mapped_column(Integer)

    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Computed at capture time so aging buckets do not require a per-row
    # date subtraction at query time.
    age_seconds: Mapped[int | None] = mapped_column(BigInteger)

    closing_reason_id: Mapped[int | None] = mapped_column(Integer)
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    source_addresses: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    local_destination_addresses: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    rule_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, nullable=False)
