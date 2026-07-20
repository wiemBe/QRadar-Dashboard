"""Declarative base, shared mixins, and the encrypted-at-rest column type."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# Explicit naming convention so Alembic autogenerate produces stable, and
# therefore reversible, constraint names.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(UTC)


class EncryptedString(TypeDecorator[str]):
    """Versioned-Fernet-encrypted text column.

    Used for QRadar tokens and notification webhook URLs. Keys come from the
    configured keyring and are never persisted alongside the ciphertext; the
    key *version* travels inside the token so rotation needs no schema change.
    See app.security.crypto for the rotation design.

    Reading a value that fails to decrypt raises rather than returning None —
    silently degrading to "no credential" would turn a key-rotation mistake into
    a confusing auth failure much further downstream.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: object) -> str | None:
        if value is None:
            return None
        from app.security.crypto import get_encryptor

        return get_encryptor().encrypt(value)

    def process_result_value(self, value: str | None, dialect: object) -> str | None:
        if value is None:
            return None
        from app.security.crypto import KeyRotationError, get_encryptor

        try:
            return get_encryptor().decrypt(value)
        except KeyRotationError as exc:
            raise RuntimeError(
                "Failed to decrypt a stored secret. The encryption key that wrote it is not in "
                "the current keyring; restore it to ENCRYPTION_KEYS_JSON or re-enter the "
                "affected credentials."
            ) from exc


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=utcnow, nullable=False
    )
