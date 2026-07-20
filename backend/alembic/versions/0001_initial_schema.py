"""initial schema + timescaledb hypertables

Revision ID: 0001
Revises:
Create Date: 2026-07-19

Baseline migration. The relational schema is materialised directly from the
SQLAlchemy model metadata so the migration can never drift from the ORM
definitions, then the four time-series tables are converted to TimescaleDB
hypertables.

This migration is deliberately NON-DESTRUCTIVE: it creates hypertables but sets
no retention or compression policy. Retention drops data, so it must never be a
hardcoded migration side effect. Retention/compression are applied separately
and only when explicitly configured — see app/services/timescale.py.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models import HYPERTABLES, Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timescale_available(bind: sa.engine.Connection) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Relational schema, straight from model metadata.
    Base.metadata.create_all(bind=bind)

    # 2. Hypertables. Skipped gracefully on a vanilla Postgres (e.g. a CI box
    #    without the Timescale extension) so the migration still applies there.
    if not _timescale_available(bind):
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    for table, time_col in HYPERTABLES.items():
        op.execute(
            sa.text(
                "SELECT create_hypertable(:t, :c, "
                "if_not_exists => TRUE, migrate_data => TRUE)"
            ).bindparams(t=table, c=time_col)
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Dropping the tables removes their hypertable structure and any policy that
    # was later attached to them.
    Base.metadata.drop_all(bind=bind)
