"""Robust statistics: median, MAD, the MAD=0 rule, percentiles, robust-z."""

from __future__ import annotations

import pytest

from app.anomaly import statistics as rstats


def test_median_and_mad_basic() -> None:
    values = [10, 10, 10, 12, 8]
    assert rstats.median(values) == 10
    assert rstats.mad(values) == 0  # majority identical -> MAD 0


def test_mad_nonzero() -> None:
    assert rstats.mad([1, 2, 3, 4, 5]) == 1.0  # median 3, deviations [2,1,0,1,2] -> median 1


def test_effective_scale_floors_when_mad_zero() -> None:
    # MAD = 0 must not yield a zero scale (which would make any change infinite z).
    scale = rstats.effective_scale(100.0, 0.0, mad_floor_ratio=0.1)
    assert scale == pytest.approx(10.0)  # 10% of the median


def test_effective_scale_uses_mad_when_larger() -> None:
    scale = rstats.effective_scale(100.0, 20.0, mad_floor_ratio=0.1)
    assert scale == pytest.approx(20.0 * rstats.MAD_TO_STD)


def test_effective_scale_epsilon_when_median_and_mad_zero() -> None:
    # A dead-zero cell must still produce a positive scale.
    assert rstats.effective_scale(0.0, 0.0, mad_floor_ratio=0.1) > 0


def test_robust_z_zero_at_median() -> None:
    assert rstats.robust_z(100.0, 100.0, 10.0, mad_floor_ratio=0.1) == 0.0


def test_robust_z_sign_and_symmetry() -> None:
    up = rstats.robust_z(150.0, 100.0, 10.0, mad_floor_ratio=0.1)
    down = rstats.robust_z(50.0, 100.0, 10.0, mad_floor_ratio=0.1)
    assert up > 0 and down < 0
    assert up == pytest.approx(-down)


def test_percentile_interpolates() -> None:
    values = [0, 10, 20, 30, 40]
    assert rstats.percentile(values, 0) == 0
    assert rstats.percentile(values, 100) == 40
    assert rstats.percentile(values, 50) == 20


def test_empty_raises() -> None:
    with pytest.raises(ValueError):
        rstats.median([])
