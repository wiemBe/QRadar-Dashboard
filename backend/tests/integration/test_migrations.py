"""Migration + TimescaleDB integration tests against a real Postgres.

Gated on TEST_DATABASE_URL. Run with:

    docker compose -f docker-compose.test.yml up -d test-db
    export TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_test
    (cd backend && pytest -m integration tests/integration/test_migrations.py)

Alembic uses the sync (psycopg) driver, so these tests derive a sync URL from
TEST_DATABASE_URL and drive Alembic's command API directly.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")


def _sync_url() -> str:
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    return TEST_DB_URL.replace("+asyncpg", "+psycopg")


def _alembic_config(sync_url: str):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


@pytest.fixture
def clean_db() -> str:
    """Drop everything (including timescale-managed objects) before each test."""
    sync_url = _sync_url()
    engine = create_engine(sync_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    return sync_url


def _table_exists(engine, name: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text("SELECT to_regclass(:n)").bindparams(n=f"public.{name}")
            ).scalar()
        )


def test_upgrade_from_empty(clean_db: str) -> None:
    from alembic import command

    command.upgrade(_alembic_config(clean_db), "head")

    engine = create_engine(clean_db, future=True)
    try:
        # A representative sample of the 23 tables must exist.
        for tbl in ("log_source", "scheduled_search", "alert", "log_source_metric",
                    "collection_watermark", "log_source_detector_state"):
            assert _table_exists(engine, tbl), f"{tbl} missing after upgrade"
    finally:
        engine.dispose()


def test_downgrade_then_reupgrade(clean_db: str) -> None:
    from alembic import command

    cfg = _alembic_config(clean_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(clean_db, future=True)
    try:
        assert not _table_exists(engine, "log_source"), "downgrade left tables behind"
    finally:
        engine.dispose()

    # Re-upgrade must succeed on the now-empty database.
    command.upgrade(cfg, "head")
    engine = create_engine(clean_db, future=True)
    try:
        assert _table_exists(engine, "log_source")
    finally:
        engine.dispose()


def test_timescale_extension_and_hypertables(clean_db: str) -> None:
    from alembic import command

    command.upgrade(_alembic_config(clean_db), "head")

    engine = create_engine(clean_db, future=True)
    try:
        with engine.connect() as conn:
            ext = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
            ).scalar()
            if ext is None:
                pytest.skip("timescaledb extension not installed in this image")

            hypertables = {
                row[0]
                for row in conn.execute(
                    text("SELECT hypertable_name FROM timescaledb_information.hypertables")
                )
            }
        assert {"log_source_metric", "search_result_metric", "rule_metric",
                "offense_snapshot"} <= hypertables
    finally:
        engine.dispose()


def test_retention_policy_is_not_applied_by_migration(clean_db: str) -> None:
    """Migrations must be non-destructive: no retention job after a bare upgrade."""
    from alembic import command

    command.upgrade(_alembic_config(clean_db), "head")

    engine = create_engine(clean_db, future=True)
    try:
        with engine.connect() as conn:
            if conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
            ).scalar() is None:
                pytest.skip("timescaledb not installed")
            retention_jobs = conn.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.jobs "
                    "WHERE proc_name = 'policy_retention'"
                )
            ).scalar()
        assert retention_jobs == 0, "migration must not create destructive retention policies"
    finally:
        engine.dispose()


INSTANCE_ID = "11111111-1111-1111-1111-111111111111"
RULE_ID = "22222222-2222-2222-2222-222222222222"

PHASE3_TABLES = (
    "rule_dependency",
    "rule_state_transition",
    "rule_health_snapshot",
    "technique_mapping",
    "detection_coverage_snapshot",
)


def test_baseline_stops_at_phase2_schema(clean_db: str) -> None:
    """0001 must create the Phase 2 schema and nothing beyond it.

    While 0001 used `Base.metadata.create_all()` it built whatever the models
    currently declared, so on an empty database it silently created the Phase 3
    tables too and 0002 then had to no-op around them. Frozen, it stops here.
    """
    from alembic import command

    command.upgrade(_alembic_config(clean_db), "0001")

    engine = create_engine(clean_db, future=True)
    try:
        for tbl in ("log_source", "analytics_rule", "offense_snapshot", "detection_coverage"):
            assert _table_exists(engine, tbl), f"Phase 2 table {tbl} missing at 0001"
        for tbl in PHASE3_TABLES:
            assert not _table_exists(engine, tbl), (
                f"0001 created {tbl}, a Phase 3 table: the baseline is reading live model "
                "metadata again instead of its frozen DDL"
            )
        with engine.connect() as conn:
            cols = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'offense_snapshot'"
                    )
                )
            }
        assert "content_hash" not in cols, "0001 added a Phase 3 column to offense_snapshot"
    finally:
        engine.dispose()


def test_upgrade_from_phase2_preserves_data_and_adds_phase3(clean_db: str) -> None:
    """The real upgrade path: a populated Phase 2 database moved to head.

    Phase 2 rows must survive untouched, and the NOT NULL Phase 3 columns must
    backfill rather than fail the ALTER.
    """
    from alembic import command

    cfg = _alembic_config(clean_db)
    command.upgrade(cfg, "0001")

    engine = create_engine(clean_db, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO qradar_instance "
                    "(id, name, console_host, api_version, verify_ssl, provider_kind, "
                    " mcp_enabled, enabled, status, consecutive_failures) "
                    "VALUES (CAST(:i AS uuid), 'lab', 'qradar.lab.local', '20.0', true, 'REST', "
                    "        false, true, 'HEALTHY', 0)"
                ).bindparams(i=INSTANCE_ID)
            )
            conn.execute(
                text(
                    "INSERT INTO analytics_rule "
                    "(id, instance_id, qradar_id, name, rule_type, enabled, "
                    " is_building_block, origin, mitre_techniques, categories) "
                    "VALUES (CAST(:r AS uuid), CAST(:i AS uuid), 4821, "
                    "        'Excessive Failed Logins', 'EVENT', true, false, 'USER', "
                    "        '[\"T1110\"]', '[\"Authentication\"]')"
                ).bindparams(r=RULE_ID, i=INSTANCE_ID)
            )
            conn.execute(
                text(
                    "INSERT INTO offense_snapshot "
                    "(instance_id, qradar_offense_id, captured_at, status, magnitude, "
                    " event_count, is_assigned, categories, source_addresses, "
                    " local_destination_addresses, rule_ids) "
                    "VALUES (CAST(:i AS uuid), 9001, '2026-07-01T00:00:00Z', 'OPEN', 8, "
                    "        120, false, '[]', '[\"10.0.0.5\"]', '[]', '[4821]')"
                ).bindparams(i=INSTANCE_ID)
            )

        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            # Phase 2 data survived.
            assert conn.execute(text("SELECT count(*) FROM analytics_rule")).scalar() == 1
            assert (
                conn.execute(text("SELECT name FROM analytics_rule")).scalar()
                == "Excessive Failed Logins"
            )
            assert conn.execute(text("SELECT magnitude FROM offense_snapshot")).scalar() == 8
            assert (
                conn.execute(text("SELECT event_count FROM offense_snapshot")).scalar() == 120
            )

            # Phase 3 NOT NULL columns backfilled on the pre-existing rows.
            assert (
                conn.execute(text("SELECT health_status FROM analytics_rule")).scalar()
                == "UNKNOWN"
            )
            assert (
                conn.execute(text("SELECT usernames::text FROM offense_snapshot")).scalar()
                == "[]"
            )

            # The backfill defaults were transient: a fresh install has no server
            # default on these columns, so an upgraded database must not either.
            leftovers = [
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT table_name || '.' || column_name "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND column_default IS NOT NULL "
                        "  AND column_name NOT IN ('created_at', 'updated_at')"
                    )
                )
            ]
            assert not leftovers, f"backfill server defaults were left behind: {leftovers}"

        for tbl in PHASE3_TABLES:
            assert _table_exists(engine, tbl), f"Phase 3 table {tbl} missing after upgrade"
    finally:
        engine.dispose()


def test_alembic_check_reports_no_drift(clean_db: str) -> None:
    """`alembic check` must pass: the metadata matches the migrated schema."""
    from alembic.util.exc import AutogenerateDiffsDetected

    from alembic import command

    cfg = _alembic_config(clean_db)
    command.upgrade(cfg, "head")
    try:
        command.check(cfg)
    except AutogenerateDiffsDetected as exc:  # pragma: no cover - failure path
        pytest.fail(f"schema drift detected between models and migrations:\n{exc}")
