"""phase 3: offense detail, rule inventory, rule health, detection coverage

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-20

Adds the Phase 3 schema:

  * offense_snapshot   — the offense fields Phase 1 did not capture, plus the
                         content hash that makes snapshotting change-driven.
  * analytics_rule     — description, response configuration, SOC-owned
                         curation fields, observed contribution counters and
                         the derived health verdict.
  * rule_dependency          (new) — what a rule needs in order to fire.
  * rule_state_transition    (new) — observed enable/disable flips.
  * rule_health_snapshot     (new) — rule-health history + evidence.
  * technique_mapping        (new) — SOC-owned MITRE mappings.
  * detection_coverage       — evidence, confidence and provenance columns.
  * detection_coverage_snapshot (new) — coverage history.

Every operation here is unconditional and explicit. An earlier draft guarded
each CREATE/ADD with an inspector check, because 0001 then built the schema from
*live* model metadata (`Base.metadata.create_all()`) and so already created the
Phase 3 objects on an empty database. 0001 has since been frozen to an explicit
Phase 2 snapshot, so the starting point is now identical on every path and the
guards -- which made the migration's effect depend on the database it met --
were removed.

Non-destructive: no retention or compression policy is set here, and downgrade
removes only what this revision added.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Time-series tables this revision introduces, with their partitioning column.
_NEW_HYPERTABLES: dict[str, str] = {
    "rule_health_snapshot": "evaluated_at",
    "detection_coverage_snapshot": "captured_at",
}


def _add_columns(table: str, columns: list[sa.Column]) -> None:
    """Add each column, backfilling existing rows where the column is NOT NULL.

    NOT NULL columns carry a server_default purely so the ALTER succeeds against
    rows that already exist; it is dropped immediately afterwards. The models
    declare a client-side `default=` only, so leaving the server default in
    place would make a database upgraded from Phase 2 differ from one created
    fresh -- and `alembic check` would then report drift on the upgraded one.
    """
    for column in columns:
        had_default = column.server_default is not None
        op.add_column(table, column)
        if had_default:
            op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column.name}" DROP DEFAULT')


def _drop_columns(table: str, names: list[str]) -> None:
    for name in names:
        op.drop_column(table, name)


def _timescale_available(bind: sa.engine.Connection) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        ).scalar()
    )


# --------------------------------------------------------------------- columns
_OFFENSE_SNAPSHOT_COLUMNS = [
    sa.Column("offense_type_name", sa.String(255), nullable=True),
    sa.Column("source_network", sa.String(255), nullable=True),
    sa.Column("source_count", sa.Integer(), nullable=True),
    sa.Column("destination_count", sa.Integer(), nullable=True),
    sa.Column("close_time", sa.DateTime(timezone=True), nullable=True),
    sa.Column("closing_reason", sa.String(512), nullable=True),
    sa.Column(
        "usernames",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ),
    sa.Column(
        "log_source_ids",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ),
    sa.Column("content_hash", sa.String(64), nullable=True),
]

_ANALYTICS_RULE_COLUMNS = [
    sa.Column("description", sa.Text(), nullable=True),
    sa.Column("qradar_created_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("generates_offense", sa.Boolean(), nullable=True),
    sa.Column(
        "response_actions",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ),
    sa.Column("soc_notes", sa.Text(), nullable=True),
    sa.Column("expected_daily_firings", sa.Float(), nullable=True),
    sa.Column(
        "health_monitoring_enabled",
        sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    ),
    sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "event_contribution_count",
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("0"),
    ),
    sa.Column(
        "offense_contribution_count",
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("0"),
    ),
    sa.Column(
        "health_status",
        sa.String(24),
        nullable=False,
        server_default=sa.text("'UNKNOWN'"),
    ),
    sa.Column("health_evaluated_at", sa.DateTime(timezone=True), nullable=True),
]

_DETECTION_COVERAGE_COLUMNS = [
    sa.Column(
        "inferred_rule_count", sa.Integer(), nullable=False, server_default=sa.text("0")
    ),
    sa.Column(
        "degraded_rule_count", sa.Integer(), nullable=False, server_default=sa.text("0")
    ),
    sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")),
    sa.Column("logic_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    sa.Column("reason", sa.Text(), nullable=True),
    sa.Column(
        "evidence",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    ),
]


def upgrade() -> None:
    bind = op.get_bind()

    _add_columns("offense_snapshot", _OFFENSE_SNAPSHOT_COLUMNS)
    _add_columns("analytics_rule", _ANALYTICS_RULE_COLUMNS)
    _add_columns("detection_coverage", _DETECTION_COVERAGE_COLUMNS)

    op.create_index(
        "ix_analytics_rule_building_block",
        "analytics_rule",
        ["instance_id", "is_building_block"],
    )

    # ------------------------------------------------------------ new tables
    op.create_table(
        "rule_dependency",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("target_ref", sa.String(128), nullable=False),
        sa.Column("target_name", sa.String(512), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("provenance", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["analytics_rule.id"],
            name="fk_rule_dependency_rule_id_analytics_rule",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rule_dependency"),
        sa.UniqueConstraint(
            "rule_id", "kind", "target_ref", name="uq_rule_dependency_rule_kind_target"
        ),
    )
    op.create_index("ix_rule_dependency_target", "rule_dependency", ["kind", "target_ref"])

    op.create_table(
        "rule_state_transition",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_enabled", sa.Boolean(), nullable=False),
        sa.Column("current_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["analytics_rule.id"],
            name="fk_rule_state_transition_rule_id_analytics_rule",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rule_state_transition"),
    )
    op.create_index(
        "ix_rule_state_transition_rule_time",
        "rule_state_transition",
        ["rule_id", "observed_at"],
    )

    op.create_table(
        "rule_health_snapshot",
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("logic_version", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger_count", sa.BigInteger(), nullable=False),
        sa.Column("offense_contribution_count", sa.BigInteger(), nullable=False),
        sa.Column("expected_daily_firings", sa.Float(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("building_blocks_healthy", sa.Boolean(), nullable=True),
        sa.Column("required_log_sources_healthy", sa.Boolean(), nullable=True),
        sa.Column(
            "missing_dependencies", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["analytics_rule.id"],
            name="fk_rule_health_snapshot_rule_id_analytics_rule",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("rule_id", "evaluated_at", name="pk_rule_health_snapshot"),
    )
    op.create_index(
        "ix_rule_health_snapshot_rule_time",
        "rule_health_snapshot",
        ["rule_id", "evaluated_at"],
    )
    op.create_index(
        "ix_rule_health_snapshot_status",
        "rule_health_snapshot",
        ["status", "evaluated_at"],
    )

    op.create_table(
        "technique_mapping",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("technique_id", sa.String(32), nullable=False),
        sa.Column("technique_name", sa.String(255), nullable=True),
        sa.Column("tactic", sa.String(128), nullable=True),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("provenance", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name="fk_technique_mapping_instance_id_qradar_instance",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["analytics_rule.id"],
            name="fk_technique_mapping_rule_id_analytics_rule",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_technique_mapping"),
        sa.UniqueConstraint(
            "instance_id",
            "technique_id",
            "rule_id",
            name="uq_technique_mapping_technique_rule",
        ),
    )
    op.create_index(
        "ix_technique_mapping_technique",
        "technique_mapping",
        ["instance_id", "technique_id"],
    )

    op.create_table(
        "detection_coverage_snapshot",
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("technique_id", sa.String(32), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("coverage_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("mapped_rule_count", sa.Integer(), nullable=False),
        sa.Column("enabled_rule_count", sa.Integer(), nullable=False),
        sa.Column("firing_rule_count", sa.Integer(), nullable=False),
        sa.Column("degraded_rule_count", sa.Integer(), nullable=False),
        sa.Column("logic_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name="fk_detection_coverage_snapshot_instance_id_qradar_instance",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instance_id",
            "technique_id",
            "captured_at",
            name="pk_detection_coverage_snapshot",
        ),
    )
    op.create_index(
        "ix_coverage_snapshot_technique_time",
        "detection_coverage_snapshot",
        ["instance_id", "technique_id", "captured_at"],
    )
    op.create_index(
        "ix_coverage_snapshot_captured", "detection_coverage_snapshot", ["captured_at"]
    )

    # --------------------------------------------------------- hypertables
    # create_hypertable is itself idempotent via if_not_exists.
    if _timescale_available(bind):
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        for table, time_col in _NEW_HYPERTABLES.items():
            op.execute(
                sa.text(
                    "SELECT create_hypertable(:t, :c, "
                    "if_not_exists => TRUE, migrate_data => TRUE)"
                ).bindparams(t=table, c=time_col)
            )


def downgrade() -> None:
    for table in ("detection_coverage_snapshot", "technique_mapping",
                  "rule_health_snapshot", "rule_state_transition", "rule_dependency"):
        op.drop_table(table)

    op.drop_index("ix_analytics_rule_building_block", table_name="analytics_rule")

    _drop_columns("detection_coverage", [c.name for c in _DETECTION_COVERAGE_COLUMNS])
    _drop_columns("analytics_rule", [c.name for c in _ANALYTICS_RULE_COLUMNS])
    _drop_columns("offense_snapshot", [c.name for c in _OFFENSE_SNAPSHOT_COLUMNS])
