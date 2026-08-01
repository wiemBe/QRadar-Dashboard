"""phase A source-volume anomaly: completeness, lifecycle and explanation evidence

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-02

Phase A turns the log-source anomaly engine from a verdict producer into an
investigation platform.  Three groups of change:

  * Bucket and baseline *completeness*, so an incompletely collected interval
    can never enter a baseline or be read as a real observation.  Without this
    a collection outage looks exactly like a fleet-wide volume drop.

  * An explicit anomaly *lifecycle* (CANDIDATE / OPEN / RECOVERING / RESOLVED)
    with an auditable transition table, replacing the implicit open/resolved
    boolean.  Existing rows are migrated to the equivalent terminal state.

  * Bounded *explanation evidence*: typed contributor and dimension tables that
    record what changed during the anomalous interval.  Typed rather than JSON
    because "which source IPs contributed to more than one anomaly this week?"
    is the question the product exists to answer.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Columns added with a server_default purely to backfill existing rows. The
# models use client-side defaults, so every one of these must have its default
# dropped again or `alembic check` reports drift.
_BACKFILL_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("log_source_metric", "completeness"),
    ("log_source_metric", "query_provenance"),
    ("log_source_baseline", "completeness"),
    ("log_source_baseline", "excluded_sample_count"),
    ("log_source_baseline", "exclusion_counts"),
    ("log_source_detector_state", "state"),
    ("log_source_anomaly", "state"),
    ("log_source_anomaly", "consecutive_buckets"),
    ("log_source_anomaly", "policy_version"),
    ("log_source_anomaly", "evidence_status"),
)


def upgrade() -> None:
    # ---------------------------------------------------- metric completeness
    op.add_column(
        "log_source_metric",
        sa.Column(
            "completeness", sa.String(16), nullable=False, server_default="COMPLETE"
        ),
    )
    op.add_column(
        "log_source_metric",
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "log_source_metric",
        sa.Column("collection_source", sa.String(32), nullable=True),
    )
    op.add_column(
        "log_source_metric",
        sa.Column(
            "query_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "log_source_metric",
        sa.Column("collection_duration_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "log_source_metric",
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "log_source_metric",
        sa.Column("watermark_at", sa.DateTime(timezone=True), nullable=True),
    )

    # -------------------------------------------------- baseline completeness
    op.add_column(
        "log_source_baseline",
        sa.Column(
            "completeness", sa.Float(), nullable=False, server_default=sa.text("0.0")
        ),
    )
    op.add_column(
        "log_source_baseline",
        sa.Column(
            "excluded_sample_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "log_source_baseline",
        sa.Column(
            "exclusion_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # ------------------------------------------------------ detector lifecycle
    op.add_column(
        "log_source_detector_state",
        sa.Column("state", sa.String(24), nullable=False, server_default="NORMAL"),
    )
    op.add_column(
        "log_source_detector_state",
        sa.Column(
            "active_anomaly_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    # An already-open detector is mid-incident; carry it into the new state
    # machine rather than silently resetting it to NORMAL, which would let the
    # engine open a duplicate incident for telemetry that never recovered.
    op.execute(
        """
        UPDATE log_source_detector_state
           SET state = 'OPEN', active_anomaly_id = open_anomaly_id
         WHERE is_open IS TRUE
        """
    )

    # ------------------------------------------------------- anomaly lifecycle
    op.add_column(
        "log_source_anomaly",
        sa.Column("state", sa.String(24), nullable=False, server_default="OPEN"),
    )
    op.add_column(
        "log_source_anomaly",
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "log_source_anomaly",
        sa.Column("anomaly_start", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "log_source_anomaly",
        sa.Column("anomaly_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("log_source_anomaly", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column(
        "log_source_anomaly", sa.Column("deviation_ratio", sa.Float(), nullable=True)
    )
    op.add_column("log_source_anomaly", sa.Column("robust_z", sa.Float(), nullable=True))
    op.add_column(
        "log_source_anomaly", sa.Column("absolute_delta", sa.Float(), nullable=True)
    )
    op.add_column(
        "log_source_anomaly",
        sa.Column(
            "consecutive_buckets", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "log_source_anomaly", sa.Column("baseline_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "log_source_anomaly",
        sa.Column(
            "policy_version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
    )
    op.add_column(
        "log_source_anomaly",
        sa.Column(
            "evidence_status",
            sa.String(16),
            nullable=False,
            server_default="NOT_REQUESTED",
        ),
    )

    # Historical rows predate the lifecycle. Derive the equivalent terminal
    # state from the columns that did exist, so history stays readable.
    # Suppression is checked first: a suppressed anomaly is suppressed whether
    # or not it later resolved, and that is the fact an auditor asks about.
    op.execute(
        """
        UPDATE log_source_anomaly
           SET state = CASE
                         WHEN suppressed IS TRUE   THEN 'SUPPRESSED'
                         WHEN resolved_at IS NOT NULL THEN 'RESOLVED'
                         ELSE 'OPEN'
                       END,
               opened_at     = detected_at,
               anomaly_start = detected_at,
               anomaly_end   = resolved_at
        """
    )

    # ------------------------------------------------------ transition history
    op.create_table(
        "anomaly_state_transition",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("anomaly_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_state", sa.String(24), nullable=True),
        sa.Column("to_state", sa.String(24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["anomaly_id"],
            ["log_source_anomaly.id"],
            name=op.f("fk_anomaly_state_transition_anomaly_id_log_source_anomaly"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_state_transition")),
    )
    op.create_index(
        "ix_anomaly_transition_anomaly",
        "anomaly_state_transition",
        ["anomaly_id", "occurred_at"],
    )

    # ------------------------------------------------------ explanation package
    op.create_table(
        "anomaly_explanation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("anomaly_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("anomaly_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("anomaly_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("comparison_strategy", sa.String(48), nullable=False),
        sa.Column("anomaly_total_events", sa.BigInteger(), nullable=False),
        sa.Column("baseline_total_events", sa.BigInteger(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collection_duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "query_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["anomaly_id"],
            ["log_source_anomaly.id"],
            name=op.f("fk_anomaly_explanation_anomaly_id_log_source_anomaly"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_explanation")),
        sa.UniqueConstraint("anomaly_id", name="uq_anomaly_explanation_anomaly"),
    )
    op.create_index("ix_anomaly_explanation_status", "anomaly_explanation", ["status"])

    op.create_table(
        "anomaly_explanation_dimension",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("explanation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dimension", sa.String(48), nullable=False),
        sa.Column("availability", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("baseline_distinct_count", sa.Integer(), nullable=True),
        sa.Column("anomaly_distinct_count", sa.Integer(), nullable=True),
        sa.Column("cardinality_ratio", sa.Float(), nullable=True),
        sa.Column("new_value_count", sa.Integer(), nullable=False),
        sa.Column("disappeared_value_count", sa.Integer(), nullable=False),
        sa.Column("baseline_top_share", sa.Float(), nullable=True),
        sa.Column("anomaly_top_share", sa.Float(), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["explanation_id"],
            ["anomaly_explanation.id"],
            name=op.f(
                "fk_anomaly_explanation_dimension_explanation_id_anomaly_explanation"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_explanation_dimension")),
        sa.UniqueConstraint(
            "explanation_id", "dimension", name="uq_explanation_dimension"
        ),
    )

    op.create_table(
        "anomaly_explanation_contributor",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("explanation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dimension", sa.String(48), nullable=False),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("baseline_count", sa.BigInteger(), nullable=False),
        sa.Column("anomaly_count", sa.BigInteger(), nullable=False),
        sa.Column("absolute_delta", sa.BigInteger(), nullable=False),
        sa.Column("percent_delta", sa.Float(), nullable=True),
        sa.Column("anomaly_share", sa.Float(), nullable=True),
        sa.Column("baseline_share", sa.Float(), nullable=True),
        sa.Column("contribution_share", sa.Float(), nullable=True),
        sa.Column("baseline_rank", sa.Integer(), nullable=True),
        sa.Column("anomaly_rank", sa.Integer(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("is_new", sa.Boolean(), nullable=False),
        sa.Column("is_disappeared", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "contribution_share IS NULL OR contribution_share BETWEEN -1.0 AND 1.0",
            name=op.f("ck_anomaly_explanation_contributor_contribution_share_range"),
        ),
        sa.ForeignKeyConstraint(
            ["explanation_id"],
            ["anomaly_explanation.id"],
            name=op.f(
                "fk_anomaly_explanation_contributor_explanation_id_anomaly_explanation"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_explanation_contributor")),
        sa.UniqueConstraint(
            "explanation_id", "dimension", "value", name="uq_explanation_contributor"
        ),
    )
    op.create_index(
        "ix_explanation_contributor_rank",
        "anomaly_explanation_contributor",
        ["explanation_id", "dimension", "rank"],
    )

    # Models use client-side defaults; these existed only to backfill.
    for table, column in _BACKFILL_DEFAULTS:
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" DROP DEFAULT')


def downgrade() -> None:
    op.drop_index(
        "ix_explanation_contributor_rank", table_name="anomaly_explanation_contributor"
    )
    op.drop_table("anomaly_explanation_contributor")
    op.drop_table("anomaly_explanation_dimension")
    op.drop_index("ix_anomaly_explanation_status", table_name="anomaly_explanation")
    op.drop_table("anomaly_explanation")
    op.drop_index("ix_anomaly_transition_anomaly", table_name="anomaly_state_transition")
    op.drop_table("anomaly_state_transition")

    for column in (
        "evidence_status",
        "policy_version",
        "baseline_version",
        "consecutive_buckets",
        "absolute_delta",
        "robust_z",
        "deviation_ratio",
        "confidence",
        "anomaly_end",
        "anomaly_start",
        "opened_at",
        "state",
    ):
        op.drop_column("log_source_anomaly", column)

    op.drop_column("log_source_detector_state", "active_anomaly_id")
    op.drop_column("log_source_detector_state", "state")

    for column in ("exclusion_counts", "excluded_sample_count", "completeness"):
        op.drop_column("log_source_baseline", column)

    for column in (
        "watermark_at",
        "collected_at",
        "collection_duration_ms",
        "query_provenance",
        "collection_source",
        "first_event_at",
        "completeness",
    ):
        op.drop_column("log_source_metric", column)
