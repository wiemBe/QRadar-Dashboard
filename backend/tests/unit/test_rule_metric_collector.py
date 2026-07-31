"""Pure guardrails for offense-derived rule metrics."""

from datetime import UTC, datetime

from app.collectors.rule_metric_collector import (
    COMPLETENESS,
    PROVENANCE,
    floor_to_day,
)


def test_offense_metric_source_is_explicitly_incomplete() -> None:
    assert PROVENANCE == "offense_contribution"
    assert COMPLETENESS == "incomplete"


def test_metric_buckets_are_utc_days() -> None:
    value = datetime(2026, 7, 31, 23, 59, 1, tzinfo=UTC)
    assert floor_to_day(value) == datetime(2026, 7, 31, tzinfo=UTC)


def test_naive_qradar_times_are_treated_as_utc() -> None:
    value = datetime(2026, 7, 31, 12, 30)
    assert floor_to_day(value) == datetime(2026, 7, 31, tzinfo=UTC)
