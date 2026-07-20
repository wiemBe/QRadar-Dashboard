"""User, Role, and the append-only AuditLog.

Authentication is abstracted (local or OIDC). For OIDC-backed users we store no
password; `hashed_password` stays NULL and the subject claim links the identity.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

user_role = Table(
    "user_role_link",
    Base.metadata,
    Column("user_id", PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"),
           primary_key=True),
    Column("role_id", PgUUID(as_uuid=True), ForeignKey("role.id", ondelete="CASCADE"),
           primary_key=True),
)


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "role"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Coarse capability strings, e.g. ["search:execute", "alert:ack", "admin:*"].
    # RBAC is checked against this list; see app.security.rbac.
    permissions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    users: Mapped[list[User]] = relationship(
        secondary=user_role, back_populates="roles"
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "app_user"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))

    # local auth only; NULL for OIDC identities
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    # OIDC subject claim; NULL for local users
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list[Role]] = relationship(
        secondary=user_role, back_populates="users", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only record of administrative and search actions.

    No updated_at, no ORM-level delete path is ever exposed. Records who did
    what to which object, with a redacted parameter payload — never raw secrets
    or raw event data.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_actor_time", "actor", "created_at"),
        Index("ix_audit_log_action", "action", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    actor: Mapped[str | None] = mapped_column(String(255))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    # e.g. "search.execute", "search.update", "alert.acknowledge",
    # "instance.create", "auth.login"
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str | None] = mapped_column(String(64))
    object_id: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(16), default="SUCCESS", nullable=False)

    source_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    # Redacted before persistence by app.security.redaction.
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
