"""Robust statistics for baselining. Pure functions, no I/O.

Median and MAD (median absolute deviation), not mean and standard deviation:
SIEM volume is heavy-tailed and a single incident would inflate a standard
deviation enough to blind the detector for weeks. No ML — everything here is
explainable to an analyst and reproducible from the stored samples.
"""

from __future__ import annotations

import statistics as _stats

from app.models.enums import RobustScoreStatus

# Scale factor making MAD a consistent estimator of the standard deviation for
# normally distributed data. robust_z ≈ classic z-score when data is ~normal.
MAD_TO_STD = 1.4826


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median of empty sequence")
    return float(_stats.median(values))


def mad(values: list[float], center: float | None = None) -> float:
    """Median absolute deviation about the median."""
    if not values:
        raise ValueError("mad of empty sequence")
    c = center if center is not None else median(values)
    return float(_stats.median([abs(v - c) for v in values]))


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile, q in [0, 100]."""
    if not values:
        raise ValueError("percentile of empty sequence")
    if not 0 <= q <= 100:
        raise ValueError("q must be in [0, 100]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (q / 100) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def effective_scale(med: float, mad_value: float, *, mad_floor_ratio: float) -> float:
    """Deviation denominator, robust to MAD = 0.

    MAD is exactly zero whenever more than half the samples are identical — very
    common for a steady low-volume source. A zero scale would make every tiny
    change look infinitely anomalous (division by zero / instant spike). We floor
    the scale at `mad_floor_ratio * median` (and at a small absolute epsilon when
    the median is also ~0), so a rock-steady source needs a genuinely large
    change to trip, not a one-event wobble.
    """
    scaled = mad_value * MAD_TO_STD
    floor = max(abs(med) * mad_floor_ratio, 1e-9)
    return max(scaled, floor)


def robust_z(value: float, med: float, mad_value: float, *, mad_floor_ratio: float) -> float:
    """Signed robust z-score of `value` against a (median, MAD) baseline."""
    scale = effective_scale(med, mad_value, mad_floor_ratio=mad_floor_ratio)
    return (value - med) / scale


def robust_z_with_status(
    value: float, med: float, mad_value: float, *, mad_floor_ratio: float
) -> tuple[float, RobustScoreStatus]:
    """Robust z-score plus whether it is trustworthy for a verdict.

    MAD is exactly zero whenever more than half the baseline samples are
    identical — the normal case for a steady low-volume source. `robust_z` still
    returns a finite number there (the scale is floored), but that number is an
    artefact of the floor, not a measure of surprise: it scales with an
    arbitrary ratio rather than with observed variability.

    Callers must therefore not treat a DEGENERATE score as evidence in either
    direction. Crossing the threshold is not proof of an anomaly, and failing to
    cross it is not proof of normality — the second error is the dangerous one,
    because it lets a zero-MAD baseline silently suppress a real, material
    deviation. Fall back to the deterministic expected-bound, ratio and
    absolute-delta tests instead, and record that the fallback was used.
    """
    z = robust_z(value, med, mad_value, mad_floor_ratio=mad_floor_ratio)
    status = RobustScoreStatus.DEGENERATE if mad_value <= 0.0 else RobustScoreStatus.OK
    return z, status


def ratio(observed: float, expected: float) -> float | None:
    """observed / expected, or None when expected is zero.

    None means "no ratio exists", which is a different finding from a very large
    ratio and must never be rendered as one. Division by zero here would be a
    silent infinity flowing into a severity calculation.
    """
    if expected == 0:
        return None
    return observed / expected
