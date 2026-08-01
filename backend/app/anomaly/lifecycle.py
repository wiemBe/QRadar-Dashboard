"""Anomaly lifecycle state machine.

Pure decision logic over counters and a current state. No I/O and no ORM, so
every transition rule is unit-testable against exact inputs; the engine owns
persistence and side effects.

    INSUFFICIENT_DATA ──baseline becomes adequate──▶ NORMAL
    NORMAL ──1 abnormal bucket──▶ CANDIDATE
    CANDIDATE ──N consecutive abnormal──▶ OPEN
    CANDIDATE ──returns to normal──▶ NORMAL         (never was an incident)
    OPEN ──normal bucket──▶ RECOVERING
    RECOVERING ──abnormal again──▶ OPEN             (relapse, same incident)
    RECOVERING ──M consecutive normal──▶ RESOLVED
    any ──operator action / maintenance──▶ SUPPRESSED

Two invariants the state machine exists to enforce:

  * An UNKNOWN bucket never advances anything. Missing or incomplete data is
    not recovery, and a collection failure must never resolve an anomaly.
  * Only one incident is active per (log source, detector type) at a time. A
    recurrence after RESOLVED opens a new incident rather than reviving the
    old one, so incident durations stay meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import AnomalyState, DetectorSignal


@dataclass(frozen=True)
class LifecycleInput:
    state: AnomalyState
    signal: DetectorSignal
    #: Consecutive abnormal buckets *including* the current one.
    consecutive_anomalous: int
    #: Consecutive healthy buckets *including* the current one.
    consecutive_healthy: int
    open_after: int
    resolve_after: int
    #: The detector could not reach a verdict for want of usable data.
    insufficient_data: bool = False
    #: Maintenance window or operator suppression is in force.
    suppressed: bool = False


@dataclass(frozen=True)
class LifecycleDecision:
    state: AnomalyState
    reason: str


def next_state(inp: LifecycleInput) -> LifecycleDecision:
    """Compute the next lifecycle state. Returns the current state unchanged
    when no rule fires, so callers can compare and persist only real moves."""
    current = inp.state

    # Suppression wins over everything: an operator or a maintenance window has
    # said "do not act on this source". Evidence is retained, not deleted.
    if inp.suppressed:
        if current is AnomalyState.SUPPRESSED:
            return LifecycleDecision(current, "already suppressed")
        return LifecycleDecision(AnomalyState.SUPPRESSED, "suppressed")

    # Leaving suppression returns to the neutral state; the counters resume
    # from whatever the detectors report next.
    if current is AnomalyState.SUPPRESSED:
        return LifecycleDecision(AnomalyState.NORMAL, "suppression lifted")

    if inp.insufficient_data:
        if current in (AnomalyState.NORMAL, AnomalyState.INSUFFICIENT_DATA):
            return LifecycleDecision(
                AnomalyState.INSUFFICIENT_DATA, "no verdict possible"
            )
        # An active incident is NOT downgraded because one bucket was
        # unreadable. Losing visibility mid-incident is not recovery, and
        # collapsing to INSUFFICIENT_DATA here would silently close it.
        return LifecycleDecision(current, "holding state; bucket not judgeable")

    if inp.signal is DetectorSignal.UNKNOWN:
        return LifecycleDecision(current, "holding state; detector returned unknown")

    if inp.signal is DetectorSignal.ANOMALOUS:
        return _on_anomalous(inp, current)
    return _on_healthy(inp, current)


def _on_anomalous(inp: LifecycleInput, current: AnomalyState) -> LifecycleDecision:
    if current in (
        AnomalyState.NORMAL,
        AnomalyState.INSUFFICIENT_DATA,
        AnomalyState.RESOLVED,
    ):
        # A single abnormal bucket is recorded as a candidate, not an incident.
        # Promoting immediately is how a detector flaps on a one-bucket blip.
        if inp.consecutive_anomalous >= inp.open_after:
            return LifecycleDecision(
                AnomalyState.OPEN,
                f"{inp.consecutive_anomalous} consecutive abnormal bucket(s)",
            )
        return LifecycleDecision(AnomalyState.CANDIDATE, "first abnormal bucket")

    if current is AnomalyState.CANDIDATE:
        if inp.consecutive_anomalous >= inp.open_after:
            return LifecycleDecision(
                AnomalyState.OPEN,
                f"{inp.consecutive_anomalous} consecutive abnormal bucket(s) "
                f"reached the confirmation threshold of {inp.open_after}",
            )
        return LifecycleDecision(current, "still confirming")

    if current is AnomalyState.RECOVERING:
        # Relapse before the recovery completed. The same incident reopens
        # rather than a second one being created alongside it.
        return LifecycleDecision(AnomalyState.OPEN, "relapsed before recovery completed")

    return LifecycleDecision(current, "still abnormal")


def _on_healthy(inp: LifecycleInput, current: AnomalyState) -> LifecycleDecision:
    if current is AnomalyState.CANDIDATE:
        # Never promoted, so this was never an incident.
        return LifecycleDecision(AnomalyState.NORMAL, "returned to normal before opening")

    if current is AnomalyState.OPEN:
        if inp.resolve_after <= 1:
            return LifecycleDecision(AnomalyState.RESOLVED, "1 normal bucket observed")
        return LifecycleDecision(AnomalyState.RECOVERING, "normal bucket observed")

    if current is AnomalyState.RECOVERING:
        if inp.consecutive_healthy >= inp.resolve_after:
            return LifecycleDecision(
                AnomalyState.RESOLVED,
                f"{inp.consecutive_healthy} consecutive normal bucket(s) reached the "
                f"recovery threshold of {inp.resolve_after}",
            )
        return LifecycleDecision(current, "still recovering")

    if current is AnomalyState.INSUFFICIENT_DATA:
        return LifecycleDecision(AnomalyState.NORMAL, "baseline became adequate")

    return LifecycleDecision(AnomalyState.NORMAL, "within expected range")
