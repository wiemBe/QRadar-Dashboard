"""Anomaly lifecycle state machine.

Pure transition rules, no DB. The invariants under test are the ones that stop
the platform lying to an operator: unknown data never counts as recovery, a
candidate that never opened was never an incident, and suppression preserves
rather than discards.
"""

from __future__ import annotations

import pytest

from app.anomaly.lifecycle import LifecycleInput, next_state
from app.models.enums import AnomalyState, DetectorSignal


def _inp(
    state: AnomalyState,
    signal: DetectorSignal,
    *,
    anomalous: int = 0,
    healthy: int = 0,
    open_after: int = 2,
    resolve_after: int = 3,
    insufficient: bool = False,
    suppressed: bool = False,
) -> LifecycleInput:
    return LifecycleInput(
        state=state,
        signal=signal,
        consecutive_anomalous=anomalous,
        consecutive_healthy=healthy,
        open_after=open_after,
        resolve_after=resolve_after,
        insufficient_data=insufficient,
        suppressed=suppressed,
    )


class TestConfirmation:
    def test_first_abnormal_bucket_creates_candidate_not_incident(self) -> None:
        d = next_state(_inp(AnomalyState.NORMAL, DetectorSignal.ANOMALOUS, anomalous=1))
        assert d.state is AnomalyState.CANDIDATE

    def test_candidate_promotes_at_the_confirmation_threshold(self) -> None:
        d = next_state(
            _inp(AnomalyState.CANDIDATE, DetectorSignal.ANOMALOUS, anomalous=2)
        )
        assert d.state is AnomalyState.OPEN

    def test_candidate_holds_below_the_threshold(self) -> None:
        d = next_state(
            _inp(
                AnomalyState.CANDIDATE,
                DetectorSignal.ANOMALOUS,
                anomalous=2,
                open_after=3,
            )
        )
        assert d.state is AnomalyState.CANDIDATE

    def test_open_after_one_opens_immediately(self) -> None:
        """LAB_MODE uses a confirmation count of 1."""
        d = next_state(
            _inp(
                AnomalyState.NORMAL,
                DetectorSignal.ANOMALOUS,
                anomalous=1,
                open_after=1,
            )
        )
        assert d.state is AnomalyState.OPEN

    def test_candidate_returning_to_normal_was_never_an_incident(self) -> None:
        d = next_state(_inp(AnomalyState.CANDIDATE, DetectorSignal.HEALTHY, healthy=1))
        assert d.state is AnomalyState.NORMAL


class TestRecovery:
    def test_open_moves_to_recovering_on_one_normal_bucket(self) -> None:
        d = next_state(_inp(AnomalyState.OPEN, DetectorSignal.HEALTHY, healthy=1))
        assert d.state is AnomalyState.RECOVERING

    def test_recovering_resolves_at_the_recovery_threshold(self) -> None:
        d = next_state(_inp(AnomalyState.RECOVERING, DetectorSignal.HEALTHY, healthy=3))
        assert d.state is AnomalyState.RESOLVED

    def test_recovering_holds_below_the_threshold(self) -> None:
        d = next_state(_inp(AnomalyState.RECOVERING, DetectorSignal.HEALTHY, healthy=2))
        assert d.state is AnomalyState.RECOVERING

    def test_relapse_reopens_the_same_incident(self) -> None:
        d = next_state(
            _inp(AnomalyState.RECOVERING, DetectorSignal.ANOMALOUS, anomalous=1)
        )
        assert d.state is AnomalyState.OPEN

    def test_resolve_after_one_skips_recovering(self) -> None:
        d = next_state(
            _inp(AnomalyState.OPEN, DetectorSignal.HEALTHY, healthy=1, resolve_after=1)
        )
        assert d.state is AnomalyState.RESOLVED


class TestMissingDataIsNotRecovery:
    """The invariant that keeps a collection outage from closing incidents."""

    @pytest.mark.parametrize(
        "state",
        [AnomalyState.OPEN, AnomalyState.RECOVERING, AnomalyState.CANDIDATE],
    )
    def test_unknown_signal_holds_an_active_state(self, state: AnomalyState) -> None:
        d = next_state(_inp(state, DetectorSignal.UNKNOWN, healthy=99))
        assert d.state is state

    @pytest.mark.parametrize(
        "state",
        [AnomalyState.OPEN, AnomalyState.RECOVERING, AnomalyState.CANDIDATE],
    )
    def test_insufficient_data_holds_an_active_state(self, state: AnomalyState) -> None:
        # Losing visibility mid-incident must not silently close it.
        d = next_state(
            _inp(state, DetectorSignal.UNKNOWN, insufficient=True, healthy=99)
        )
        assert d.state is state

    def test_a_high_healthy_count_cannot_resolve_via_unknown(self) -> None:
        """An UNKNOWN bucket must not resolve even with the counter satisfied."""
        d = next_state(
            _inp(AnomalyState.RECOVERING, DetectorSignal.UNKNOWN, healthy=99)
        )
        assert d.state is AnomalyState.RECOVERING


class TestInsufficientData:
    def test_normal_source_without_baseline_reports_insufficient(self) -> None:
        d = next_state(
            _inp(AnomalyState.NORMAL, DetectorSignal.UNKNOWN, insufficient=True)
        )
        assert d.state is AnomalyState.INSUFFICIENT_DATA

    def test_insufficient_becomes_normal_once_the_baseline_is_adequate(self) -> None:
        d = next_state(
            _inp(AnomalyState.INSUFFICIENT_DATA, DetectorSignal.HEALTHY, healthy=1)
        )
        assert d.state is AnomalyState.NORMAL

    def test_insufficient_can_still_open_on_an_abnormal_bucket(self) -> None:
        d = next_state(
            _inp(
                AnomalyState.INSUFFICIENT_DATA,
                DetectorSignal.ANOMALOUS,
                anomalous=1,
            )
        )
        assert d.state is AnomalyState.CANDIDATE


class TestSuppression:
    def test_suppression_wins_over_every_signal(self) -> None:
        d = next_state(
            _inp(
                AnomalyState.OPEN,
                DetectorSignal.ANOMALOUS,
                anomalous=9,
                suppressed=True,
            )
        )
        assert d.state is AnomalyState.SUPPRESSED

    def test_suppression_is_idempotent(self) -> None:
        d = next_state(
            _inp(AnomalyState.SUPPRESSED, DetectorSignal.HEALTHY, suppressed=True)
        )
        assert d.state is AnomalyState.SUPPRESSED

    def test_lifting_suppression_returns_to_normal(self) -> None:
        d = next_state(_inp(AnomalyState.SUPPRESSED, DetectorSignal.HEALTHY))
        assert d.state is AnomalyState.NORMAL


class TestRecurrence:
    def test_a_resolved_incident_does_not_revive(self) -> None:
        """A new abnormal bucket after RESOLVED starts a fresh incident.

        The engine clears active_anomaly_id on resolution, so reaching
        CANDIDATE here creates a new row rather than reopening the old one --
        which is what keeps incident durations meaningful.
        """
        d = next_state(_inp(AnomalyState.RESOLVED, DetectorSignal.ANOMALOUS, anomalous=1))
        assert d.state is AnomalyState.CANDIDATE

    def test_every_decision_carries_a_reason(self) -> None:
        """Transitions are auditable; a state change with no reason is not."""
        for state in AnomalyState:
            for signal in DetectorSignal:
                d = next_state(_inp(state, signal, anomalous=1, healthy=1))
                assert d.reason, f"{state}/{signal} produced no reason"
