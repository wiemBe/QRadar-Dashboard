"""Health-score arithmetic. The 40/25/20/15 weighting is a spec contract."""

from __future__ import annotations

import pytest

from app.services.health_score import (
    WEIGHT_COLLECTION,
    WEIGHT_FRESHNESS,
    WEIGHT_PARSING,
    WEIGHT_VOLUME,
    HealthInputs,
    compute_health,
)


def _perfect() -> HealthInputs:
    return HealthInputs(
        event_delay_seconds=0.0,
        expected_interval_seconds=300.0,
        observed_eps=100.0,
        baseline_median_eps=100.0,
        baseline_is_reliable=True,
        parsed_username_ratio=1.0,
        parsed_source_ip_ratio=1.0,
        unknown_event_count=0,
        total_event_count=1000,
        collection_error_count=0,
    )


def test_weights_sum_to_one() -> None:
    total = WEIGHT_FRESHNESS + WEIGHT_VOLUME + WEIGHT_PARSING + WEIGHT_COLLECTION
    assert total == pytest.approx(1.0)


def test_perfect_source_scores_100() -> None:
    c = compute_health(_perfect())
    assert c.score == 100.0
    assert (c.freshness, c.volume, c.parsing, c.collection) == (100.0, 100.0, 100.0, 100.0)


def test_silent_source_loses_exactly_the_freshness_weight() -> None:
    inp = _perfect()
    # 5x+ interval late -> freshness 0, everything else perfect.
    dead = HealthInputs(**{**inp.__dict__, "event_delay_seconds": 3000.0})
    c = compute_health(dead)
    assert c.freshness == 0.0
    # Losing only freshness means score == 100 * (1 - 0.40) == 60.
    assert c.score == pytest.approx(60.0)


def test_volume_deviation_is_symmetric() -> None:
    base = _perfect()
    drop = compute_health(HealthInputs(**{**base.__dict__, "observed_eps": 40.0}))
    spike = compute_health(HealthInputs(**{**base.__dict__, "observed_eps": 160.0}))
    # Same relative deviation (0.6) either direction -> same volume component.
    assert drop.volume == spike.volume


def test_unreliable_baseline_does_not_penalise_volume() -> None:
    base = _perfect()
    inp = HealthInputs(
        **{**base.__dict__, "observed_eps": 5.0, "baseline_is_reliable": False}
    )
    assert compute_health(inp).volume == 100.0


def test_parsing_degradation_lowers_parsing_component() -> None:
    base = _perfect()
    inp = HealthInputs(
        **{
            **base.__dict__,
            "parsed_username_ratio": 0.2,
            "parsed_source_ip_ratio": 0.3,
            "unknown_event_count": 400,
            "total_event_count": 1000,
        }
    )
    c = compute_health(inp)
    assert c.parsing < 60.0
    assert 0.0 <= c.score <= 100.0


def test_each_collection_error_costs_20_points() -> None:
    base = _perfect()
    one = compute_health(HealthInputs(**{**base.__dict__, "collection_error_count": 1}))
    five = compute_health(HealthInputs(**{**base.__dict__, "collection_error_count": 5}))
    assert one.collection == 80.0
    assert five.collection == 0.0


def test_score_always_bounded() -> None:
    worst = HealthInputs(
        event_delay_seconds=99999.0,
        expected_interval_seconds=60.0,
        observed_eps=0.0,
        baseline_median_eps=500.0,
        baseline_is_reliable=True,
        parsed_username_ratio=0.0,
        parsed_source_ip_ratio=0.0,
        unknown_event_count=1000,
        total_event_count=1000,
        collection_error_count=99,
    )
    c = compute_health(worst)
    assert c.score == 0.0
