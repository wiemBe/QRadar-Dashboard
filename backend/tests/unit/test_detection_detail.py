"""Projection of a stored detector evidence dict onto the API detection block.

Pure arithmetic-free mapping, but the properties under test are what keep the
investigation page from overstating its evidence: an absent fact stays absent
rather than becoming 0, a detector's internal keys do not leak into a public
response, and an unrecognized robust-score status is dropped rather than
guessed as OK.
"""

from __future__ import annotations

from app.models.enums import RobustScoreStatus
from app.schemas.anomaly import detection_detail

FULL = {
    "reason": "average EPS 6.00 is above the baseline median 2.00",
    "baseline_low": 1.7,
    "baseline_high": 2.3,
    "threshold": 3.5,
    "sample_count": 30,
    "signal": "ANOMALOUS",
    "severity": "HIGH",
    "extra": {
        "observed_eps": 6.0,
        "expected_eps": 2.0,
        "absolute_delta_events": 1200.0,
        "bucket_seconds": 300.0,
        "baseline_completeness": 0.9,
        "baseline_version": 2,
        "ratio": 3.0,
        "robust_score_status": "OK",
        "robust_z": 13.5,
    },
}


class TestProjection:
    def test_maps_both_levels_of_the_stored_payload(self) -> None:
        d = detection_detail(FULL)
        assert d is not None
        assert d.expected_low == 1.7
        assert d.threshold == 3.5
        assert d.baseline_sample_count == 30
        assert d.observed_eps == 6.0
        assert d.robust_score_status is RobustScoreStatus.OK

    def test_an_anomaly_with_no_stored_evidence_has_no_detection_block(self) -> None:
        assert detection_detail(None) is None
        assert detection_detail({}) is None

    def test_unlisted_keys_are_dropped(self) -> None:
        d = detection_detail(FULL)
        assert d is not None
        assert "signal" not in d.model_dump()
        assert "severity" not in d.model_dump()


class TestAbsenceIsNotZero:
    def test_a_missing_fact_stays_none(self) -> None:
        """0.0 is a measurement; None is the absence of one. A detector that
        never computed a fallback bound has not placed the observation at 0."""
        d = detection_detail({"reason": "silence", "extra": {}})
        assert d is not None
        assert d.fallback_bound is None
        assert d.ratio is None
        assert d.baseline_completeness is None

    def test_a_measured_zero_survives(self) -> None:
        d = detection_detail({"extra": {"expected_eps": 0.0, "ratio": 0.0}})
        assert d is not None
        assert d.expected_eps == 0.0
        assert d.ratio == 0.0

    def test_a_flag_is_not_coerced_into_a_number(self) -> None:
        """bool subclasses int in Python. Letting True become 1.0 in a numeric
        field would turn a detector bug into a plausible-looking measurement."""
        d = detection_detail({"extra": {"ratio": True}})
        assert d is not None
        assert d.ratio is None

    def test_a_non_numeric_value_is_dropped_rather_than_parsed(self) -> None:
        d = detection_detail({"threshold": "3.5", "extra": {"robust_z": None}})
        assert d is not None
        assert d.threshold is None
        assert d.robust_z is None


class TestDegenerateScore:
    def test_degenerate_status_and_fallback_bound_are_both_reported(self) -> None:
        """A DEGENERATE score means the verdict rests on the deterministic
        bound, which is weaker evidence; the page can only say so if it is
        told both facts."""
        d = detection_detail(
            {"extra": {"robust_score_status": "DEGENERATE", "fallback_bound": 2.3}}
        )
        assert d is not None
        assert d.robust_score_status is RobustScoreStatus.DEGENERATE
        assert d.fallback_bound == 2.3

    def test_an_unrecognized_status_is_not_guessed(self) -> None:
        d = detection_detail({"extra": {"robust_score_status": "SOMETHING_NEW"}})
        assert d is not None
        assert d.robust_score_status is None

    def test_the_ratio_basis_survives_when_expected_was_zero(self) -> None:
        """A spike from a zero baseline has no ratio at all; the basis string
        is what stops the empty ratio reading as 'unchanged'."""
        d = detection_detail({"extra": {"ratio": None, "ratio_basis": "expected_zero"}})
        assert d is not None
        assert d.ratio is None
        assert d.ratio_basis == "expected_zero"
