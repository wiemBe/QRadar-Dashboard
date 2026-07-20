"""Apply TimescaleDB retention and compression policies from configuration.

Kept out of migrations on purpose: retention *drops data*, and a destructive
action must be an explicit, configurable operation — never an implicit side
effect of running `alembic upgrade`. All policies default to disabled.

Idempotent: `add_retention_policy(..., if_not_exists => TRUE)` and removing a
policy that is absent are both safe to repeat.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

logger = logging.getLogger("app.timescale")


async def _timescale_available(session: AsyncSession) -> bool:
    result = await session.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    )
    return result.scalar() is not None


async def apply_policies(session: AsyncSession, settings: Settings | None = None) -> dict[str, str]:
    """Reconcile retention/compression policies with configuration.

    Returns a per-table description of the action taken, for logging/auditing.
    """
    settings = settings or get_settings()
    outcome: dict[str, str] = {}

    if not await _timescale_available(session):
        logger.info("timescaledb not present; skipping policy application")
        return {"_status": "timescaledb-absent"}

    retention = settings.retention_days()

    for table in ("log_source_metric", "search_result_metric", "rule_metric", "offense_snapshot"):
        days = retention.get(table)
        if days is None:
            # Retention disabled or unset for this table: ensure no policy exists
            # so a previously-configured destructive policy can be turned off.
            await session.execute(
                text("SELECT remove_retention_policy(:t, if_exists => TRUE)").bindparams(t=table)
            )
            outcome[table] = "retention-disabled"
            continue
        if days < 1:
            raise ValueError(f"retention for {table} must be >= 1 day, got {days}")
        await session.execute(
            text(
                "SELECT add_retention_policy(:t, drop_after => (:ival)::interval, "
                "if_not_exists => TRUE)"
            ).bindparams(t=table, ival=f"{days} days")
        )
        outcome[table] = f"retention={days}d"

    if settings.compression_after_days is not None:
        cdays = settings.compression_after_days
        for table in ("log_source_metric", "search_result_metric", "rule_metric",
                      "offense_snapshot"):
            await session.execute(
                text("ALTER TABLE " + table + " SET (timescaledb.compress = true)")
            )
            await session.execute(
                text(
                    "SELECT add_compression_policy(:t, compress_after => (:ival)::interval, "
                    "if_not_exists => TRUE)"
                ).bindparams(t=table, ival=f"{cdays} days")
            )
        outcome["_compression"] = f"after={settings.compression_after_days}d"

    await session.commit()
    logger.info("timescale policies applied", extra={"outcome": outcome})
    return outcome
