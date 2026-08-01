"""Contributor analysis: what changed between the baseline and anomaly windows.

Pure arithmetic, no DB. The properties under test are the ones that keep the
investigation page honest: nothing is invented for a value that was never
measured, a longer comparison window does not manufacture a fleet-wide drop,
and an unpopulated DSM field is never rendered as a measured zero.
"""

from __future__ import annotations

import pytest

from app.anomaly.explanation import compare_all, compare_dimension
from app.models.enums import DimensionAvailability
from app.providers.dto import DimensionAggregate, DimensionValueCount


def _agg(
    dimension: str,
    values: dict[str, int],
    *,
    available: bool = True,
    truncated: bool = False,
    error: str | None = None,
    distinct: int | None = None,
) -> DimensionAggregate:
    items = [DimensionValueCount(value=v, count=c) for v, c in values.items()]
    items.sort(key=lambda i: i.count, reverse=True)
    return DimensionAggregate(
        dimension=dimension,
        available=available,
        values=items,
        distinct_count=distinct if distinct is not None else len(items),
        total_count=sum(values.values()),
        truncated=truncated,
        error=error,
        query=f"MOCK {dimension}",
    )


class TestContributorRanking:
    def test_largest_mover_ranks_first(self) -> None:
        base = _agg("source_ip", {"203.0.113.50": 12, "198.51.100.11": 500})
        anom = _agg("source_ip", {"203.0.113.50": 4820, "198.51.100.11": 520})
        result = compare_dimension(base, anom)

        top = result.contributors[0]
        assert top.value == "203.0.113.50"
        assert top.baseline_count == 12
        assert top.anomaly_count == 4820
        assert top.absolute_delta == 4808

    def test_contribution_share_reflects_the_dominant_talker(self) -> None:
        base = _agg("source_ip", {"a": 12, "b": 500})
        anom = _agg("source_ip", {"a": 4820, "b": 520})
        result = compare_dimension(base, anom)
        top = result.contributors[0]
        # 4808 of 4828 total movement.
        assert top.contribution_share == pytest.approx(0.9959, abs=1e-3)

    def test_shares_are_bounded_to_the_valid_range(self) -> None:
        base = _agg("action", {"ACCEPT": 1000, "DENY": 50})
        anom = _agg("action", {"ACCEPT": 100, "DENY": 4000})
        for c in compare_dimension(base, anom).contributors:
            if c.contribution_share is not None:
                assert -1.0 <= c.contribution_share <= 1.0
            for share in (c.anomaly_share, c.baseline_share):
                if share is not None:
                    assert 0.0 <= share <= 1.0

    def test_ranking_is_stable_for_equal_movement(self) -> None:
        base = _agg("destination_port", {"22": 100, "445": 100})
        anom = _agg("destination_port", {"22": 200, "445": 200})
        first = [c.value for c in compare_dimension(base, anom).contributors]
        second = [c.value for c in compare_dimension(base, anom).contributors]
        assert first == second


class TestNothingIsInvented:
    def test_a_new_value_has_no_percentage_change(self) -> None:
        """A value with no baseline has no percentage, and we must not fake one."""
        base = _agg("destination_ip", {"10.0.0.5": 100})
        anom = _agg("destination_ip", {"10.0.0.5": 100, "10.0.9.9": 4000})
        result = compare_dimension(base, anom)
        new = next(c for c in result.contributors if c.value == "10.0.9.9")
        assert new.is_new is True
        assert new.percent_delta is None
        assert new.baseline_count == 0

    def test_disappeared_values_are_flagged(self) -> None:
        base = _agg("username", {"svc-backup": 400, "alice": 100})
        anom = _agg("username", {"alice": 100})
        result = compare_dimension(base, anom)
        gone = next(c for c in result.contributors if c.value == "svc-backup")
        assert gone.is_disappeared is True
        assert gone.anomaly_count == 0
        assert result.disappeared_value_count == 1

    def test_zero_baseline_cardinality_yields_no_ratio(self) -> None:
        """"8.4x" and "from nothing" are different findings."""
        base = _agg("destination_ip", {}, distinct=0)
        anom = _agg("destination_ip", {"10.0.0.1": 50})
        result = compare_dimension(base, anom)
        assert result.cardinality_ratio is None

    def test_empty_window_yields_no_share(self) -> None:
        base = _agg("source_ip", {})
        anom = _agg("source_ip", {})
        result = compare_dimension(base, anom)
        assert result.anomaly_top_share is None
        assert result.baseline_top_share is None


class TestAvailability:
    def test_unpopulated_field_is_unavailable_not_zero(self) -> None:
        base = _agg("username", {})
        anom = _agg(
            "username", {}, available=False, error="field is not populated"
        )
        result = compare_dimension(base, anom)
        assert result.availability is DimensionAvailability.UNAVAILABLE
        assert result.contributors == []
        # Critically: no fabricated counts.
        assert result.anomaly_distinct_count is None

    def test_query_failure_is_distinct_from_unavailable(self) -> None:
        base = _agg("source_ip", {"a": 1})
        anom = _agg("source_ip", {"a": 1}, error="ProviderUnavailableError: timeout")
        result = compare_dimension(base, anom)
        assert result.availability is DimensionAvailability.FAILED
        assert "timeout" in (result.detail or "")

    def test_truncation_is_surfaced(self) -> None:
        base = _agg("source_ip", {"a": 10})
        anom = _agg("source_ip", {"a": 90}, truncated=True)
        result = compare_dimension(base, anom)
        assert result.availability is DimensionAvailability.TRUNCATED
        assert result.truncated is True


class TestWindowNormalization:
    def test_a_longer_baseline_window_does_not_manufacture_a_drop(self) -> None:
        """The bug this guards: comparing a 10-minute anomaly against a
        30-minute baseline by raw count shows every dimension collapsing."""
        base = _agg("source_ip", {"a": 3000})   # 3000 events over 1800s
        anom = _agg("source_ip", {"a": 1000})   # 1000 events over 600s

        naive = compare_dimension(base, anom, scale=1.0)
        assert naive.contributors[0].absolute_delta == -2000  # spurious "drop"

        scaled = compare_all(
            [base], [anom], baseline_seconds=1800, anomaly_seconds=600
        )[0]
        # Same rate: 1000 vs 1000 -> no change.
        assert scaled.contributors[0].absolute_delta == 0

    def test_rate_normalization_preserves_a_genuine_spike(self) -> None:
        base = _agg("source_ip", {"a": 3000})   # 1.67/s
        anom = _agg("source_ip", {"a": 6000})   # 10/s
        result = compare_all(
            [base], [anom], baseline_seconds=1800, anomaly_seconds=600
        )[0]
        assert result.contributors[0].absolute_delta == 5000


class TestCardinalityAndConcentration:
    def test_cardinality_growth_is_measured(self) -> None:
        base = _agg("destination_ip", {f"10.0.0.{i}": 10 for i in range(5)})
        anom = _agg("destination_ip", {f"10.0.0.{i}": 10 for i in range(42)})
        result = compare_dimension(base, anom, top_n=50)
        assert result.baseline_distinct_count == 5
        assert result.anomaly_distinct_count == 42
        assert result.cardinality_ratio == pytest.approx(8.4)
        assert result.new_value_count == 37

    def test_concentration_shift_is_measured(self) -> None:
        """A single talker taking over an otherwise diverse source."""
        base = _agg("source_ip", {"a": 100, "b": 100, "c": 100})
        anom = _agg("source_ip", {"a": 100, "b": 100, "c": 9800})
        result = compare_dimension(base, anom)
        assert result.baseline_top_share == pytest.approx(1 / 3, abs=1e-3)
        assert result.anomaly_top_share == pytest.approx(0.98, abs=1e-2)


class TestBounds:
    def test_top_n_caps_the_contributor_list(self) -> None:
        base = _agg("source_ip", {f"10.0.0.{i}": 1 for i in range(100)})
        anom = _agg("source_ip", {f"10.0.0.{i}": i * 10 for i in range(100)})
        result = compare_dimension(base, anom, top_n=5)
        assert len(result.contributors) == 5
        assert [c.rank for c in result.contributors] == [1, 2, 3, 4, 5]

    def test_missing_baseline_dimension_is_handled(self) -> None:
        """A dimension present only in the anomaly window still compares."""
        anom = _agg("protocol", {"6": 500})
        result = compare_all([], [anom], baseline_seconds=600, anomaly_seconds=600)
        assert len(result) == 1
        assert result[0].contributors[0].is_new is True
