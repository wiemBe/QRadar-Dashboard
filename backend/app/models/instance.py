"""QRadarInstance — a monitored QRadar deployment."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, EncryptedString, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import InstanceStatus


class QRadarInstance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "qradar_instance"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    console_host: Mapped[str] = mapped_column(String(255), nullable=False)
    api_version: Mapped[str] = mapped_column(String(16), default="20.0", nullable=False)

    # Encrypted at rest, never serialized to any API response. The read-only
    # authorized service token for this console.
    sec_token: Mapped[str | None] = mapped_column(EncryptedString(512))

    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ca_bundle_path: Mapped[str | None] = mapped_column(String(512))

    # Which provider backs this instance: mock | rest | mcp
    provider_kind: Mapped[str] = mapped_column(String(16), default="rest", nullable=False)
    mcp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mcp_base_url: Mapped[str | None] = mapped_column(String(255))

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    status: Mapped[InstanceStatus] = mapped_column(
        String(16), default=InstanceStatus.UNKNOWN, nullable=False
    )
    qradar_version: Mapped[str | None] = mapped_column(String(64))
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    log_sources: Mapped[list[LogSource]] = relationship(  # noqa: F821
        back_populates="instance", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<QRadarInstance {self.name} status={self.status}>"
