"""Result-trend endpoint helpers and bounds. No database needed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.api.routes.searches import (
    RESULTS_DEFAULT_LIMIT,
    RESULTS_MAX_LIMIT,
    _as_utc,
)


def test_none_passes_through() -> None:
    assert _as_utc(None) is None


def test_naive_timestamp_is_read_as_utc() -> None:
    # Not rejected: the platform stores and reports UTC, so that is the only
    # defensible reading of an unqualified bound.
    got = _as_utc(datetime(2026, 7, 20, 10, 30))
    assert got == datetime(2026, 7, 20, 10, 30, tzinfo=UTC)
    assert got.tzinfo is UTC


def test_offset_timestamp_is_converted_not_relabelled() -> None:
    # +02:00 10:30 is 08:30Z. Relabelling instead of converting would silently
    # shift every point on the chart by the offset.
    aware = datetime(2026, 7, 20, 10, 30, tzinfo=timezone(timedelta(hours=2)))
    assert _as_utc(aware) == datetime(2026, 7, 20, 8, 30, tzinfo=UTC)


def test_utc_timestamp_is_unchanged() -> None:
    aware = datetime(2026, 7, 20, 10, 30, tzinfo=UTC)
    assert _as_utc(aware) == aware


def test_default_limit_is_within_the_hard_cap() -> None:
    assert 0 < RESULTS_DEFAULT_LIMIT <= RESULTS_MAX_LIMIT
