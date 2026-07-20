"""Baseline builder: grouping, MAD=0, min samples, maintenance/business-hours
exclusion, versioning. DB-gated."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.anomaly.baseline import BaselineBuilder
from app.core.config import Settings
from app.models.log_source import LogSourceBaseline
from tests.integration.factories import add_metric, make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings(min_samples: int = 8) -> Settings:
    return Settings(encryption_key="x" * 44, baseline_min_samples=min_samples,
                    baseline_lookback_days=60)


async def _weekly_series(session, ls, *, weeks: int, hour: int, eps_values: list[float]):
    """Insert one metric per week at a fixed weekday+hour (Wednesday)."""
    # 2026-07-01 is a Wednesday.
    base = datetime(2026, 7, 1, hour, 0, tzinfo=UTC)
    for w in range(weeks):
        eps = eps_values[w % len(eps_values)]
        await add_metric(
            session, ls, base + timedelta(weeks=w),
            average_eps=eps, event_count=int(eps * 300),
        )
    return base + timedelta(weeks=weeks)


async def test_reliable_baseline_from_enough_samples(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    now = await _weekly_series(db_session, ls, weeks=12, hour=10,
                               eps_values=[48, 50, 52, 49, 51])

    builder = BaselineBuilder(db_session, settings=_settings())
    cells = await builder.rebuild_for_source(ls, now=now)
    assert cells > 0

    cell = await db_session.scalar(
        select(LogSourceBaseline).where(
            LogSourceBaseline.log_source_id == ls.id,
            LogSourceBaseline.metric_name == "average_eps",
            LogSourceBaseline.weekday == 3, LogSourceBaseline.hour == 10,
        )
    )
    assert cell is not None
    assert cell.is_reliable is True
    # 12 weekly samples are written, but baseline_lookback_days=60 admits only
    # those from 2026-07-25 onwards -- weeks 4..11, i.e. 8. That is exactly
    # baseline_min_samples, so this is also the reliability boundary.
    assert cell.sample_count == 8
    assert 48 <= cell.median <= 52


async def test_mad_zero_when_identical(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    now = await _weekly_series(db_session, ls, weeks=10, hour=9, eps_values=[42.0])

    builder = BaselineBuilder(db_session, settings=_settings())
    await builder.rebuild_for_source(ls, now=now)
    cell = await db_session.scalar(
        select(LogSourceBaseline).where(
            LogSourceBaseline.log_source_id == ls.id,
            LogSourceBaseline.metric_name == "average_eps",
            LogSourceBaseline.hour == 9,
        )
    )
    assert cell.median == 42.0
    assert cell.mad == 0.0  # all identical
    assert cell.is_reliable is True


async def test_insufficient_samples_marked_unreliable(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    now = await _weekly_series(db_session, ls, weeks=3, hour=11, eps_values=[50])

    builder = BaselineBuilder(db_session, settings=_settings(min_samples=8))
    await builder.rebuild_for_source(ls, now=now)
    cell = await db_session.scalar(
        select(LogSourceBaseline).where(
            LogSourceBaseline.log_source_id == ls.id,
            LogSourceBaseline.hour == 11,
            LogSourceBaseline.metric_name == "average_eps",
        )
    )
    assert cell.sample_count == 3
    assert cell.is_reliable is False  # exists but must not drive alerts


async def test_maintenance_intervals_excluded(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(
        db_session, inst, maintenance_mode=True,
        maintenance_until=datetime(2027, 1, 1, tzinfo=UTC),
    )
    now = await _weekly_series(db_session, ls, weeks=12, hour=10, eps_values=[50])
    builder = BaselineBuilder(db_session, settings=_settings())
    cells = await builder.rebuild_for_source(ls, now=now)
    assert cells == 0  # everything in maintenance -> nothing baselined


async def test_business_hours_only_excludes_offhours(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(
        db_session, inst, business_hours_only=True,
        business_hours_start=8, business_hours_end=18, business_days=[1, 2, 3, 4, 5],
    )
    # Series at 03:00 (off-hours) must be excluded entirely.
    now = await _weekly_series(db_session, ls, weeks=12, hour=3, eps_values=[50])
    builder = BaselineBuilder(db_session, settings=_settings())
    cells = await builder.rebuild_for_source(ls, now=now)
    assert cells == 0


async def test_recompute_bumps_version(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    now = await _weekly_series(db_session, ls, weeks=10, hour=10, eps_values=[50])
    builder = BaselineBuilder(db_session, settings=_settings())
    await builder.rebuild_for_source(ls, now=now)
    await builder.rebuild_for_source(ls, now=now)
    cell = await db_session.scalar(
        select(LogSourceBaseline).where(
            LogSourceBaseline.log_source_id == ls.id,
            LogSourceBaseline.hour == 10,
            LogSourceBaseline.metric_name == "average_eps",
        )
    )
    assert cell.baseline_version == 2
    assert cell.observations  # observations retained for reproducibility
