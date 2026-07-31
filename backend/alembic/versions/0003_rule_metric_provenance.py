"""rule metric provenance and collection completeness

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-31

The QRadar rule inventory has no firing-statistics field.  Metrics derived from
offense contribution are therefore useful positive evidence but an incomplete
view of all rule firings.  Persist that distinction so a missing contribution
can never be presented as a verified zero.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rule_metric",
        sa.Column(
            "provenance", sa.String(64), nullable=False, server_default="unknown"
        ),
    )
    op.add_column(
        "rule_metric",
        sa.Column(
            "completeness", sa.String(32), nullable=False, server_default="incomplete"
        ),
    )
    op.add_column(
        "rule_metric",
        sa.Column("inferred", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "collection_watermark",
        sa.Column(
            "collection_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # Models intentionally use client-side defaults.  Defaults above exist
    # only to backfill upgraded databases and must not create Alembic drift.
    for table, column in (
        ("rule_metric", "provenance"),
        ("rule_metric", "completeness"),
        ("rule_metric", "inferred"),
        ("collection_watermark", "collection_metadata"),
    ):
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" DROP DEFAULT')


def downgrade() -> None:
    op.drop_column("collection_watermark", "collection_metadata")
    op.drop_column("rule_metric", "inferred")
    op.drop_column("rule_metric", "completeness")
    op.drop_column("rule_metric", "provenance")
