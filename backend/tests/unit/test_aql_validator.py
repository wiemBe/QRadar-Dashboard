"""Safe AQL validation — the security-critical gate before any execution."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.aql_validator import AQLValidationError, validate_aql

SETTINGS = Settings(
    encryption_key="x" * 44,
    ariel_max_time_range_hours=168,
    ariel_max_result_rows=10_000,
)


def _validate(aql: str):
    return validate_aql(aql, settings=SETTINGS)


# --- valid queries ---------------------------------------------------------
def test_accepts_bounded_aggregate_query() -> None:
    v = _validate("SELECT sourceip, COUNT(*) FROM events GROUP BY sourceip LAST 24 HOURS")
    assert v.datasets == ["EVENTS"]
    assert v.has_time_window


def test_accepts_flows_dataset() -> None:
    assert _validate("SELECT * FROM flows LAST 1 DAYS").datasets == ["FLOWS"]


def test_accepts_start_stop_window() -> None:
    aql = "SELECT * FROM events START '2026-01-01 00:00' STOP '2026-01-01 06:00'"
    assert _validate(aql).has_time_window


# --- statement injection ---------------------------------------------------
def test_rejects_multiple_statements() -> None:
    with pytest.raises(AQLValidationError, match="multiple statements"):
        _validate("SELECT * FROM events LAST 1 HOURS; DROP TABLE x")


def test_semicolon_inside_string_is_inert() -> None:
    # The ; is data, not a statement separator.
    v = _validate("SELECT * FROM events WHERE name = '; DROP TABLE' LAST 1 HOURS")
    assert v.has_time_window


def test_line_comment_cannot_hide_a_statement() -> None:
    # Everything after -- is blanked; the query is a single valid SELECT.
    v = _validate("SELECT * FROM events LAST 1 HOURS -- ; DELETE FROM events")
    assert v.datasets == ["EVENTS"]


def test_block_comment_cannot_hide_a_keyword() -> None:
    v = _validate("SELECT * FROM events LAST 1 HOURS /* ; DROP TABLE */")
    assert v.has_time_window


# --- mutation / DDL --------------------------------------------------------
@pytest.mark.parametrize(
    "aql",
    [
        "DELETE FROM events LAST 1 HOURS",
        "UPDATE events SET x=1 LAST 1 HOURS",
        "DROP TABLE events",
        "SELECT * FROM events LAST 1 HOURS INTO outfile",
    ],
)
def test_rejects_mutating_queries(aql: str) -> None:
    with pytest.raises(AQLValidationError):
        _validate(aql)


# --- bounds ----------------------------------------------------------------
def test_rejects_unbounded_query() -> None:
    with pytest.raises(AQLValidationError, match="time window"):
        _validate("SELECT * FROM events")


def test_rejects_excessive_time_range() -> None:
    with pytest.raises(AQLValidationError, match="exceeds the maximum"):
        _validate("SELECT * FROM events LAST 9999 DAYS")


def test_rejects_excessive_limit() -> None:
    with pytest.raises(AQLValidationError, match="exceeds the maximum"):
        _validate("SELECT * FROM events LAST 1 HOURS LIMIT 5000000")


def test_rejects_disallowed_dataset() -> None:
    with pytest.raises(AQLValidationError, match="not permitted"):
        _validate("SELECT * FROM simarc LAST 1 HOURS")


def test_rejects_empty_query() -> None:
    with pytest.raises(AQLValidationError, match="empty"):
        _validate("   ")


def test_rejects_non_select_leading_keyword() -> None:
    with pytest.raises(AQLValidationError, match="single SELECT"):
        _validate("WITH x AS (SELECT 1) SELECT * FROM events LAST 1 HOURS")


def test_all_reasons_are_collected() -> None:
    # Unbounded AND bad dataset AND mutation -> several reasons at once.
    with pytest.raises(AQLValidationError) as exc:
        _validate("DELETE FROM assets")
    assert len(exc.value.reasons) >= 2
