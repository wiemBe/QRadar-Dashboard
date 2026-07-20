"""Robust statistics for baselining. Pure functions, no I/O.

Median and MAD (median absolute deviation), not mean and standard deviation:
SIEM volume is heavy-tailed and a single incident would inflate a standard
deviation enough to blind the detector for weeks. No ML — everything here is
explainable to an analyst and reproducible from the stored samples.
"""

from __future__ import annotations

import statistics as _stats

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
