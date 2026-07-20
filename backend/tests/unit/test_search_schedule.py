"""Cron expansion for scheduled searches.

Pure-function coverage of `next_fire_time`: the seeding case, the strictly-after
case that drives `next_run_at`, timezone handling, and the rejection of a cron
expression that would otherwise take a whole scheduler cycle down. Dispatch
behaviour itself is DB-gated and lives in tests/integration/test_search_scheduler.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.search_scheduler import InvalidCronExpression, next_fire_time


def at(hour: int, minute: int, day: int = 20) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


def test_seeding_returns_first_fire_at_or_after_now() -> None:
    assert next_fire_time("*/5 * * * *", "UTC", after=None, now=at(10, 3)) == at(10, 5)


def test_seeding_on_an_exact_boundary_fires_now() -> None:
    # A search seeded exactly on a tick should not wait a whole extra period.
    assert next_fire_time("*/5 * * * *", "UTC", after=None, now=at(10, 5)) == at(10, 5)


def test_next_is_strictly_after_the_previous_tick() -> None:
    # This is what stops a dispatched tick from being selected again forever.
    assert next_fire_time("*/5 * * * *", "UTC", after=at(10, 5), now=at(10, 5)) == at(10, 10)


def test_backlog_reschedules_from_now_not_from_the_missed_tick() -> None:
    # Worker was down 10:05 -> 10:47; the next fire is the one after *now*.
    assert next_fire_time("*/5 * * * *", "UTC", after=at(10, 47), now=at(10, 47)) == at(10, 50)


def test_hourly_and_daily_expressions() -> None:
    assert next_fire_time("0 * * * *", "UTC", after=None, now=at(10, 3)) == at(11, 0)
    assert next_fire_time("0 2 * * *", "UTC", after=None, now=at(10, 3)) == at(2, 0, day=21)


def test_non_utc_timezone_is_normalised_to_utc() -> None:
    # 02:00 Europe/Istanbul (UTC+3) is 23:00 UTC the previous day.
    fire = next_fire_time("0 2 * * *", "Europe/Istanbul", after=None, now=at(10, 3))
    assert fire.tzinfo is not None
    assert fire == datetime(2026, 7, 20, 23, 0, tzinfo=UTC)


def test_result_is_always_timezone_aware_utc() -> None:
    fire = next_fire_time("*/5 * * * *", "UTC", after=None, now=at(10, 3))
    assert fire.utcoffset() == UTC.utcoffset(None)


@pytest.mark.parametrize(
    "cron",
    ["not a cron", "*/5 * * *", "99 * * * *", ""],
)
def test_malformed_cron_raises_invalid_cron_expression(cron: str) -> None:
    # The scheduler catches exactly this type to skip one search rather than
    # abandoning the cycle, so it must not leak a bare ValueError/KeyError.
    with pytest.raises(InvalidCronExpression):
        next_fire_time(cron, "UTC", after=None, now=at(10, 3))


def test_unknown_timezone_raises_invalid_cron_expression() -> None:
    with pytest.raises(InvalidCronExpression):
        next_fire_time("*/5 * * * *", "Mars/Olympus_Mons", after=None, now=at(10, 3))
