"""Log-source health scoring: a pure, deterministic 0-100 function.

Weighting is fixed by the platform spec:

    40%  event freshness      — is data arriving on time?
    25%  volume normality     — is the volume where the baseline says it should be?
    20%  parsing quality      — are events being parsed, or landing as unknown?
    15%  collection health    — errors during collection.

Kept free of I/O and ORM types so it is trivially unit-tested against exact
inputs. The caller assembles a `HealthInputs` from the latest metric and
baseline; this module only does arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass

WEIGHT_FRESHNESS = 0.40
WEIGHT_VOLUME = 0.25
WEIGHT_PARSING = 0.20
WEIGHT_COLLECTION = 0.15


@dataclass(frozen=True)
class HealthInputs:
    # freshness
    event_delay_seconds: float | None
    expected_interval_seconds: float | None
    # volume
    observed_eps: float | None
    baseline_median_eps: float | None
    baseline_is_reliable: bool
    # parsing
    parsed_username_ratio: float | None
    parsed_source_ip_ratio: float | None
    unknown_event_count: int
    total_event_count: int
    # collection
    collection_error_count: int


@dataclass(frozen=True)
class HealthComponents:
    freshness: float
    volume: float
    parsing: float
    collection: float

    @property
    def score(self) -> float:
        raw = (
            self.freshness * WEIGHT_FRESHNESS
            + self.volume * WEIGHT_VOLUME
            + self.parsing * WEIGHT_PARSING
            + self.collection * WEIGHT_COLLECTION
        )
        return round(raw, 1)


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _freshness_component(inp: HealthInputs) -> float:
    """Full marks until data is one expected interval late, then linear decay to
    zero at five intervals late. Absent delay data is treated as fresh."""
    if inp.event_delay_seconds is None:
        return 100.0
    interval = inp.expected_interval_seconds or 300.0
    delay_ratio = inp.event_delay_seconds / interval
    if delay_ratio <= 1.0:
        return 100.0
    if delay_ratio >= 5.0:
        return 0.0
    # 1x -> 100, 5x -> 0
    return _clamp(100.0 * (5.0 - delay_ratio) / 4.0)


def _volume_component(inp: HealthInputs) -> float:
    """Deviation from baseline median. Symmetric: a spike is as much a signal as
    a drop. Neutral 100 when there is no reliable baseline yet — we do not
    penalise a source for the platform still learning it."""
    if not inp.baseline_is_reliable or not inp.baseline_median_eps:
        return 100.0
    if inp.observed_eps is None:
        return 0.0
    median = inp.baseline_median_eps
    deviation = abs(inp.observed_eps - median) / median  # relative
    if deviation <= 0.25:
        return 100.0
    if deviation >= 1.0:
        return 0.0
    # 0.25 -> 100, 1.0 -> 0
    return _clamp(100.0 * (1.0 - deviation) / 0.75)


def _parsing_component(inp: HealthInputs) -> float:
    """Blend of unknown-event share and field-extraction ratios."""
    scores: list[float] = []

    if inp.total_event_count > 0:
        unknown_ratio = inp.unknown_event_count / inp.total_event_count
        scores.append(_clamp(100.0 * (1.0 - unknown_ratio)))

    for ratio in (inp.parsed_username_ratio, inp.parsed_source_ip_ratio):
        if ratio is not None:
            scores.append(_clamp(100.0 * ratio))

    if not scores:
        return 100.0
    return _clamp(sum(scores) / len(scores))


def _collection_component(inp: HealthInputs) -> float:
    """Each collection error costs 20 points; five or more errors zeroes it."""
    return _clamp(100.0 - inp.collection_error_count * 20.0)


def compute_health(inp: HealthInputs) -> HealthComponents:
    return HealthComponents(
        freshness=round(_freshness_component(inp), 1),
        volume=round(_volume_component(inp), 1),
        parsing=round(_parsing_component(inp), 1),
        collection=round(_collection_component(inp), 1),
    )
