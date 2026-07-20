"""Alembic environment.

Runs migrations with the synchronous psycopg driver (Alembic does not need
async), sourcing the URL from application Settings so there is exactly one place
that knows the DSN.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.models import HYPERTABLES, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# alembic.ini ships this inert placeholder; it never denotes a real target.
_PLACEHOLDER_URL = "driver://user:pass@localhost/dbname"


def _resolve_url() -> str:
    """The DSN to migrate. Application Settings is the single source of truth,
    except when a caller drives Alembic programmatically and has already set a
    URL on the Config -- the migration integration tests point at the ephemeral
    test database that way, and must not be redirected to the deployment DSN."""
    configured = config.get_main_option("sqlalchemy.url")
    if configured and configured != _PLACEHOLDER_URL:
        return configured
    return get_settings().sync_database_url


config.set_main_option("sqlalchemy.url", _resolve_url())

target_metadata = Base.metadata

# TimescaleDB creates internal objects (chunks, catalog tables, continuous-
# aggregate views) in schemas we do not manage. Excluding them keeps
# `alembic check` from reporting them as drift.
_TIMESCALE_SCHEMAS = {
    "_timescaledb_internal",
    "_timescaledb_catalog",
    "_timescaledb_config",
    "_timescaledb_cache",
    "timescaledb_information",
    "timescaledb_experimental",
}


# create_hypertable() implicitly builds a descending time index named
# "<table>_<time column>_idx". It is owned by Timescale, not by model metadata,
# so autogenerate must not offer to drop it as drift.
_TIMESCALE_INDEXES = {f"{table}_{col}_idx" for table, col in HYPERTABLES.items()}


def include_object(obj: object, name: str, type_: str, reflected: bool, compare_to: object) -> bool:
    schema = getattr(obj, "schema", None)
    if schema in _TIMESCALE_SCHEMAS:
        return False
    if type_ == "index" and reflected and name in _TIMESCALE_INDEXES:
        return False
    return True


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    if type_ == "schema":
        return name not in _TIMESCALE_SCHEMAS
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
