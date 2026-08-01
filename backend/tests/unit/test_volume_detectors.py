"""Volume spike/drop/silence detector guards.

Pure detector logic against exact inputs. The cases that matter most are the
ones where a naive detector gets it wrong:

  * 0.2 EPS -> 0.4 EPS clears a 2x ratio and a large robust z-score but is
    operationally meaningless (absolute-delta guard).
  * A rock-steady baseline has MAD = 0, and must neither explode on a one-event
    wobble nor suppress a genuine tenfold increase (degenerate-score fallback).
  * A partially collected bucket looks exactly like a volume drop.
  * A normally idle hour looks exactly like an outage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.anomaly.detectors import (
    BaselineCell,
    DetectionContext,
    MetricPoint,
    detect_no_events,
    detect_volume_drop,
    detect_volume_spike,
)
from app.anomaly.thresholds import Thresholds
from app.models.enums import AnomalyType, BucketCompleteness, DetectorSignal

START = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
BUCKET = 300


def _point(
    *,
    eps: float,
    completeness: BucketCompleteness = BucketCompleteness.COMPLETE,
    event_count: int | None = None,
    last_event_at: datetime | None = None,
) -> MetricPoint:
    return MetricPoint(
        interval_start=START,
        interval_end=START + timedelta(seconds=BUCKET),
        completeness=completeness,
        event_count=int(eps * BUCKET) if event_count is None else event_count,
        average_eps=eps,
        peak_eps=eps,
        last_event_at=last_event_at,
        event_delay_seconds=1.0,
        unknown_event_count=0,
        stored_event_count=int(eps * BUCKET),
        parsed_username_ratio=0.9,
        parsed_source_ip_ratio=0.9,
        distinct_qid_count=5,
        distinct_username_count=5,
        distinct_source_ip_count=5,
        collection_error_count=0,
        payload_signature=None,
    )


def _cell(
    *, median: float, mad: float = 2.0, reliable: bool = True, samples: int = 30
) -> BaselineCell:
    return BaselineCell(
        median=median,
        mad=mad,
        p05=median * 0.8,
        p95=median * 1.2,
        sample_count=samples,
        is_reliable=reliable,
        completeness=1.0,
        baseline_version=3,
    )


def _ctx(
    point: MetricPoint,
    *,
    eps_cell: BaselineCell | None = None,
    count_cell: BaselineCell | None = None,
    thresholds: Thresholds | None = None,
    expected_active: bool = True,
    preceding_empty: int = 0,
) -> DetectionContext:
    baselines = {}
    if eps_cell is not None:
        baselines["average_eps"] = eps_cell
    if count_cell is not None:
        baselines["event_count"] = count_cell
    return DetectionContext(
        point=point,
        baselines=baselines,
        thresholds=thresholds or Thresholds(),
        expected_interval_seconds=float(BUCKET),
        is_expected_active=expected_active,
        preceding_empty_buckets=preceding_empty,
    )


class TestSpikeFires:
    def test_material_spike_fires(self) -> None:
        # 50 -> 200 EPS: 4x ratio, +45,000 events, huge robust z.
        ev = detect_volume_spike(_ctx(_point(eps=200.0), eps_cell=_cell(median=50.0)))
        assert ev.signal is DetectorSignal.ANOMALOUS
        assert ev.anomaly_type is AnomalyType.VOLUME_SPIKE
        assert ev.extra["ratio"] == pytest.approx(4.0)
        assert ev.extra["absolute_delta_events"] == pytest.approx(45000.0)

    def test_evidence_records_the_baseline_it_was_judged_against(self) -> None:
        ev = detect_volume_spike(_ctx(_point(eps=200.0), eps_cell=_cell(median=50.0)))
        assert ev.extra["baseline_version"] == 3
        assert ev.extra["robust_score_status"] == "OK"


class TestAbsoluteDeltaGuard:
    def test_tiny_source_doubling_does_not_fire(self) -> None:
        """The headline false positive: 0.2 EPS -> 0.4 EPS.

        A clean 2x ratio and a large robust z-score, on a change of 60 events
        per bucket. Operationally meaningless, and the reason the guard exists.
        """
        ctx = _ctx(_point(eps=0.4), eps_cell=_cell(median=0.2, mad=0.01))
        ev = detect_volume_spike(ctx)
        assert ev.signal is DetectorSignal.HEALTHY

    def test_ratio_alone_is_not_enough(self) -> None:
        # 3x ratio but only +40 events/bucket, under the 100-event floor.
        thr = Thresholds(min_bucket_events=10.0, min_absolute_delta_events=100.0)
        ctx = _ctx(
            _point(eps=0.2), eps_cell=_cell(median=0.0667, mad=0.001), thresholds=thr
        )
        ev = detect_volume_spike(ctx)
        assert ev.signal is DetectorSignal.HEALTHY
        assert "absolute delta" in ev.reason

    def test_minimum_volume_guard_blocks_a_trivial_source(self) -> None:
        thr = Thresholds(min_bucket_events=1000.0)
        ctx = _ctx(_point(eps=2.0), eps_cell=_cell(median=0.5), thresholds=thr)
        ev = detect_volume_spike(ctx)
        assert ev.signal is DetectorSignal.HEALTHY
        assert "minimum" in ev.reason


class TestRatioGuard:
    def test_below_the_spike_ratio_does_not_fire(self) -> None:
        # 1.5x: large absolute delta, but under the 2.0 ratio guard.
        ctx = _ctx(_point(eps=75.0), eps_cell=_cell(median=50.0, mad=0.5))
        ev = detect_volume_spike(ctx)
        assert ev.signal is DetectorSignal.HEALTHY
        assert "ratio" in ev.reason

    def test_above_the_drop_ratio_does_not_fire(self) -> None:
        ctx = _ctx(_point(eps=40.0), eps_cell=_cell(median=50.0, mad=0.5))
        ev = detect_volume_drop(ctx)
        assert ev.signal is DetectorSignal.HEALTHY


class TestDegenerateMad:
    """MAD = 0 is the normal case for a steady source, not an error."""

    def test_zero_mad_does_not_suppress_a_material_spike(self) -> None:
        """The dangerous failure: a perfectly steady baseline hiding a real spike.

        With MAD = 0 the robust score is an artefact of the floored scale. The
        detector must fall back to the expected band rather than conclude the
        source is normal.
        """
        ctx = _ctx(_point(eps=500.0), eps_cell=_cell(median=50.0, mad=0.0))
        ev = detect_volume_spike(ctx)
        assert ev.signal is DetectorSignal.ANOMALOUS
        assert ev.extra["robust_score_status"] == "DEGENERATE"
        assert "MAD=0" in ev.reason

    def test_zero_mad_still_respects_the_expected_band(self) -> None:
        # Inside [p05, p95]: the fallback must not fire on a small wobble.
        cell = _cell(median=50.0, mad=0.0)
        cell.p95 = 20000.0  # band deliberately wide
        ctx = _ctx(_point(eps=200.0), eps_cell=cell)
        ev = detect_volume_spike(ctx)
        assert ev.signal is DetectorSignal.HEALTHY
        assert "expected band" in ev.reason

    def test_zero_mad_never_divides_by_zero(self) -> None:
        ctx = _ctx(_point(eps=500.0), eps_cell=_cell(median=0.0, mad=0.0))
        ev = detect_volume_spike(ctx)
        # Whatever the verdict, it must be finite and must not raise.
        assert ev.deviation_score is None or abs(ev.deviation_score) < float("inf")

    def test_degenerate_score_caps_confidence(self) -> None:
        ctx = _ctx(_point(eps=500.0), eps_cell=_cell(median=50.0, mad=0.0))
        ev = detect_volume_spike(ctx)
        assert ev.confidence <= 0.7


class TestInsufficientData:
    def test_no_baseline_is_insufficient_not_healthy(self) -> None:
        ev = detect_volume_spike(_ctx(_point(eps=500.0)))
        assert ev.signal is DetectorSignal.UNKNOWN
        assert ev.insufficient_data is True

    def test_unreliable_baseline_is_insufficient(self) -> None:
        ctx = _ctx(
            _point(eps=500.0), eps_cell=_cell(median=50.0, reliable=False, samples=3)
        )
        ev = detect_volume_spike(ctx)
        assert ev.insufficient_data is True
        assert "below the minimum" in ev.reason

    @pytest.mark.parametrize(
        "completeness", [BucketCompleteness.PARTIAL, BucketCompleteness.MISSING]
    )
    def test_incomplete_bucket_yields_no_verdict(
        self, completeness: BucketCompleteness
    ) -> None:
        """An incompletely collected bucket looks exactly like a volume drop."""
        ctx = _ctx(
            _point(eps=1.0, completeness=completeness), eps_cell=_cell(median=50.0)
        )
        ev = detect_volume_drop(ctx)
        assert ev.insufficient_data is True
        assert ev.signal is DetectorSignal.UNKNOWN


class TestDropGuards:
    def test_material_drop_fires(self) -> None:
        ev = detect_volume_drop(_ctx(_point(eps=2.0), eps_cell=_cell(median=50.0)))
        assert ev.signal is DetectorSignal.ANOMALOUS
        assert ev.extra["ratio"] == pytest.approx(0.04)

    def test_zero_baseline_is_caught_by_the_minimum_volume_guard(self) -> None:
        """With default thresholds the min-volume guard fires first.

        A drop is judged on the *expected* side, and an expectation of zero is
        below any positive floor, so the source is never judged at all.
        """
        ctx = _ctx(_point(eps=0.0, event_count=0), eps_cell=_cell(median=0.0, mad=0.0))
        ev = detect_volume_drop(ctx)
        assert ev.signal is DetectorSignal.HEALTHY
        assert "minimum" in ev.reason

    def test_drop_against_a_zero_baseline_is_not_defined(self) -> None:
        """With the volume floor disabled, the ratio guard still refuses.

        A drop below an expectation of nothing is not a thing; the detector must
        say so rather than divide by zero or invent a ratio.
        """
        thr = Thresholds(min_bucket_events=0.0, min_absolute_delta_events=0.0)
        ctx = _ctx(
            _point(eps=0.0, event_count=0),
            eps_cell=_cell(median=0.0, mad=0.0),
            thresholds=thr,
        )
        ev = detect_volume_drop(ctx)
        assert ev.signal is DetectorSignal.HEALTHY
        assert "not defined" in ev.reason

    def test_zero_mad_does_not_suppress_a_material_drop(self) -> None:
        ctx = _ctx(_point(eps=1.0), eps_cell=_cell(median=50.0, mad=0.0))
        ev = detect_volume_drop(ctx)
        assert ev.signal is DetectorSignal.ANOMALOUS
        assert ev.extra["robust_score_status"] == "DEGENERATE"


class TestSilence:
    def test_silence_fires_when_events_are_expected(self) -> None:
        ctx = _ctx(
            _point(eps=0.0, event_count=0),
            count_cell=_cell(median=15000.0),
            preceding_empty=2,
        )
        ev = detect_no_events(ctx)
        assert ev.signal is DetectorSignal.ANOMALOUS
        assert ev.extra["empty_buckets"] == 3

    def test_grace_period_tolerates_a_short_gap(self) -> None:
        ctx = _ctx(
            _point(eps=0.0, event_count=0),
            count_cell=_cell(median=15000.0),
            preceding_empty=0,
        )
        ev = detect_no_events(ctx)
        assert ev.signal is DetectorSignal.HEALTHY
        assert "grace period" in ev.reason

    def test_normally_idle_hour_never_alarms(self) -> None:
        """A source that is normally silent at this hour is behaving."""
        ctx = _ctx(
            _point(eps=0.0, event_count=0),
            count_cell=_cell(median=0.0, mad=0.0),
            preceding_empty=99,
        )
        ev = detect_no_events(ctx)
        assert ev.signal is DetectorSignal.HEALTHY
        assert "normally inactive" in ev.reason

    def test_off_schedule_silence_never_alarms(self) -> None:
        ctx = _ctx(
            _point(eps=0.0, event_count=0),
            count_cell=_cell(median=15000.0),
            expected_active=False,
            preceding_empty=99,
        )
        ev = detect_no_events(ctx)
        assert ev.signal is DetectorSignal.HEALTHY

    def test_incomplete_bucket_is_not_evidence_of_silence(self) -> None:
        """A failed collection must not look like every source going dark."""
        ctx = _ctx(
            _point(
                eps=0.0, event_count=0, completeness=BucketCompleteness.PARTIAL
            ),
            count_cell=_cell(median=15000.0),
            preceding_empty=99,
        )
        ev = detect_no_events(ctx)
        assert ev.insufficient_data is True

    def test_no_baseline_yields_insufficient_not_an_alarm(self) -> None:
        ctx = _ctx(_point(eps=0.0, event_count=0), preceding_empty=99)
        ev = detect_no_events(ctx)
        assert ev.insufficient_data is True

    def test_events_present_is_healthy(self) -> None:
        ctx = _ctx(_point(eps=50.0), count_cell=_cell(median=15000.0))
        assert detect_no_events(ctx).signal is DetectorSignal.HEALTHY
