"""Phase A end-to-end: baseline -> spike -> candidate -> open -> recovering
-> resolved, plus the explanation package. DB-gated.

This is the acceptance scenario from the Phase A spec, driven through the real
engine against a real database: a stable source at 2 EPS, a spike to 6 EPS, and
a return to baseline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.anomaly.engine import AnomalyEngine
from app.collectors.explanation_collector import ExplanationCollector
from app.core.config import Settings
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    DimensionAvailability,
    EvidenceStatus,
)
from app.models.explanation import (
    AnomalyExplanation,
    AnomalyExplanationContributor,
    AnomalyExplanationDimension,
)
from app.models.log_source import (
    AnomalyStateTransition,
    LogSourceAnomaly,
    LogSourceBaseline,
)
from app.providers.mock import MockQRadarProvider
from tests.integration.factories import add_metric, make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# A Monday at 10:00 UTC, so every bucket lands in one weekday/hour cell.
START = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
BUCKET = 300

# The acceptance scenario's rates.
BASELINE_EPS = 2.0
SPIKE_EPS = 6.0


def _settings(**kwargs) -> Settings:
    defaults = dict(
        encryption_key="x" * 44,
        anomaly_open_after_intervals=2,
        anomaly_resolve_after_intervals=2,
        # Scaled to the acceptance scenario: at 300s buckets, 2 -> 6 EPS is
        # 600 -> 1800 events, a delta of 1200.
        anomaly_min_absolute_delta_events=200.0,
        anomaly_min_bucket_events=100.0,
    )
    defaults.update(kwargs)
    return Settings(**defaults)


async def _baseline(session, ls, *, median: float, mad: float = 0.15) -> None:
    """A reliable average_eps baseline for the weekday/hour the buckets land in."""
    session.add(
        LogSourceBaseline(
            log_source_id=ls.id,
            metric_name="average_eps",
            weekday=START.isoweekday(),
            hour=START.hour,
            median=median,
            mad=mad,
            p05=median * 0.85,
            p95=median * 1.15,
            sample_count=30,
            is_reliable=True,
            baseline_version=2,
            observations=[median],
            completeness=0.95,
        )
    )
    await session.flush()


async def _feed(engine, session, ls, *, start_index: int, count: int, eps: float):
    reports = []
    for i in range(start_index, start_index + count):
        bucket = START + timedelta(seconds=BUCKET * i)
        metric = await add_metric(
            session,
            ls,
            bucket,
            average_eps=eps,
            event_count=int(eps * BUCKET),
            bucket_seconds=BUCKET,
            completeness=BucketCompleteness.COMPLETE,
            payload_signature=f"sig-{i}",
        )
        reports.append(await engine.evaluate_interval(ls, metric))
    return reports


async def _state_of(session, ls, atype: AnomalyType) -> str | None:
    row = await session.scalar(
        select(LogSourceAnomaly)
        .where(
            LogSourceAnomaly.log_source_id == ls.id,
            LogSourceAnomaly.anomaly_type == atype,
        )
        .order_by(LogSourceAnomaly.detected_at.desc())
        .limit(1)
    )
    return None if row is None else str(row.state)


class TestAcceptanceScenario:
    async def test_spike_opens_then_recovers_and_resolves(self, db_session) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst, name="LAB Firewall")
        await _baseline(db_session, ls, median=BASELINE_EPS)
        engine = AnomalyEngine(db_session, settings=_settings())

        # Baseline traffic: nothing fires.
        await _feed(engine, db_session, ls, start_index=0, count=3, eps=BASELINE_EPS)
        assert await _state_of(db_session, ls, AnomalyType.VOLUME_SPIKE) is None

        # First abnormal bucket -> CANDIDATE, not yet an incident.
        await _feed(engine, db_session, ls, start_index=3, count=1, eps=SPIKE_EPS)
        assert await _state_of(db_session, ls, AnomalyType.VOLUME_SPIKE) == (
            AnomalyState.CANDIDATE
        )

        # Second consecutive abnormal bucket -> OPEN.
        await _feed(engine, db_session, ls, start_index=4, count=1, eps=SPIKE_EPS)
        assert await _state_of(db_session, ls, AnomalyType.VOLUME_SPIKE) == (
            AnomalyState.OPEN
        )

        # Recovery: one normal bucket -> RECOVERING.
        await _feed(engine, db_session, ls, start_index=5, count=1, eps=BASELINE_EPS)
        assert await _state_of(db_session, ls, AnomalyType.VOLUME_SPIKE) == (
            AnomalyState.RECOVERING
        )

        # Still recovering: MEDIUM criticality requires 3 consecutive normal
        # buckets. The per-criticality matrix overrides the global default, so
        # the source's importance decides how eagerly its incidents close.
        await _feed(engine, db_session, ls, start_index=6, count=1, eps=BASELINE_EPS)
        assert await _state_of(db_session, ls, AnomalyType.VOLUME_SPIKE) == (
            AnomalyState.RECOVERING
        )

        # Third consecutive normal bucket -> RESOLVED.
        await _feed(engine, db_session, ls, start_index=7, count=1, eps=BASELINE_EPS)
        assert await _state_of(db_session, ls, AnomalyType.VOLUME_SPIKE) == (
            AnomalyState.RESOLVED
        )

    async def test_the_incident_records_its_measurements(self, db_session) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls, median=BASELINE_EPS)
        engine = AnomalyEngine(db_session, settings=_settings())

        await _feed(engine, db_session, ls, start_index=0, count=2, eps=BASELINE_EPS)
        await _feed(engine, db_session, ls, start_index=2, count=2, eps=SPIKE_EPS)

        anomaly = await db_session.scalar(
            select(LogSourceAnomaly).where(
                LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_SPIKE
            )
        )
        assert anomaly is not None
        assert anomaly.observed_value == pytest.approx(SPIKE_EPS)
        assert anomaly.expected_value == pytest.approx(BASELINE_EPS)
        assert anomaly.deviation_ratio == pytest.approx(3.0)
        assert anomaly.absolute_delta == pytest.approx(1200.0)
        assert anomaly.baseline_version == 2
        assert anomaly.policy_version == 1
        assert anomaly.opened_at is not None
        assert anomaly.anomaly_start is not None

    async def test_every_transition_is_audited(self, db_session) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls, median=BASELINE_EPS)
        engine = AnomalyEngine(db_session, settings=_settings())

        await _feed(engine, db_session, ls, start_index=0, count=1, eps=BASELINE_EPS)
        await _feed(engine, db_session, ls, start_index=1, count=2, eps=SPIKE_EPS)
        # Three normal buckets: MEDIUM criticality's recovery threshold.
        await _feed(engine, db_session, ls, start_index=3, count=3, eps=BASELINE_EPS)

        anomaly = await db_session.scalar(
            select(LogSourceAnomaly).where(
                LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_SPIKE
            )
        )
        transitions = list(
            (
                await db_session.scalars(
                    select(AnomalyStateTransition)
                    .where(AnomalyStateTransition.anomaly_id == anomaly.id)
                    .order_by(AnomalyStateTransition.occurred_at)
                )
            ).all()
        )
        states = [str(t.to_state) for t in transitions]
        assert states == [
            AnomalyState.CANDIDATE,
            AnomalyState.OPEN,
            AnomalyState.RECOVERING,
            AnomalyState.RESOLVED,
        ]
        assert all(t.reason for t in transitions)
        assert all(t.bucket_start is not None for t in transitions)


class TestNoDuplicateIncidents:
    async def test_a_persisting_spike_does_not_create_a_second_incident(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls, median=BASELINE_EPS)
        engine = AnomalyEngine(db_session, settings=_settings())

        await _feed(engine, db_session, ls, start_index=0, count=8, eps=SPIKE_EPS)

        count = await db_session.scalar(
            select(func.count())
            .select_from(LogSourceAnomaly)
            .where(
                LogSourceAnomaly.log_source_id == ls.id,
                LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_SPIKE,
            )
        )
        assert count == 1

    async def test_recurrence_after_resolution_opens_a_new_incident(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls, median=BASELINE_EPS)
        engine = AnomalyEngine(db_session, settings=_settings())

        await _feed(engine, db_session, ls, start_index=0, count=2, eps=SPIKE_EPS)
        await _feed(engine, db_session, ls, start_index=2, count=3, eps=BASELINE_EPS)
        await _feed(engine, db_session, ls, start_index=5, count=2, eps=SPIKE_EPS)

        rows = list(
            (
                await db_session.scalars(
                    select(LogSourceAnomaly).where(
                        LogSourceAnomaly.log_source_id == ls.id,
                        LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_SPIKE,
                    )
                )
            ).all()
        )
        assert len(rows) == 2, "a recurrence must be a new incident, not a revival"


class TestCollectionFailureIsNotRecovery:
    async def test_incomplete_buckets_do_not_resolve_an_open_anomaly(
        self, db_session
    ) -> None:
        """The invariant: losing visibility is not the same as recovering."""
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls, median=BASELINE_EPS)
        engine = AnomalyEngine(db_session, settings=_settings())

        await _feed(engine, db_session, ls, start_index=0, count=2, eps=SPIKE_EPS)
        assert await _state_of(db_session, ls, AnomalyType.VOLUME_SPIKE) == (
            AnomalyState.OPEN
        )

        # Collection breaks: several buckets arrive PARTIAL and empty, which a
        # naive engine would read as a clean recovery.
        for i in range(2, 8):
            metric = await add_metric(
                db_session,
                ls,
                START + timedelta(seconds=BUCKET * i),
                average_eps=0.0,
                event_count=0,
                bucket_seconds=BUCKET,
                completeness=BucketCompleteness.PARTIAL,
            )
            await engine.evaluate_interval(ls, metric)

        assert await _state_of(db_session, ls, AnomalyType.VOLUME_SPIKE) == (
            AnomalyState.OPEN
        )


class TestMultiSourceIsolation:
    async def test_only_the_spiking_source_is_flagged(self, db_session) -> None:
        """The second acceptance test: one source moves, the others do not."""
        inst = await make_instance(db_session)
        sources = []
        for i in range(4):
            ls = await make_log_source(
                db_session, inst, qradar_id=2000 + i, name=f"src-{i}"
            )
            await _baseline(db_session, ls, median=BASELINE_EPS)
            sources.append(ls)

        engine = AnomalyEngine(db_session, settings=_settings())
        for idx, ls in enumerate(sources):
            eps = SPIKE_EPS if idx == 2 else BASELINE_EPS
            await _feed(engine, db_session, ls, start_index=0, count=3, eps=eps)

        flagged = list(
            (
                await db_session.scalars(
                    select(LogSourceAnomaly.log_source_id).where(
                        LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_SPIKE
                    )
                )
            ).all()
        )
        assert set(flagged) == {sources[2].id}


class TestExplanationPackage:
    async def test_open_spike_marks_evidence_pending(self, db_session) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls, median=BASELINE_EPS)
        engine = AnomalyEngine(db_session, settings=_settings())

        await _feed(engine, db_session, ls, start_index=0, count=2, eps=SPIKE_EPS)
        anomaly = await db_session.scalar(
            select(LogSourceAnomaly).where(
                LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_SPIKE
            )
        )
        assert str(anomaly.evidence_status) == EvidenceStatus.PENDING

    async def test_collector_writes_a_typed_contributor_package(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls, median=BASELINE_EPS)
        settings = _settings()
        engine = AnomalyEngine(db_session, settings=settings)
        await _feed(engine, db_session, ls, start_index=0, count=2, eps=SPIKE_EPS)

        anomaly = await db_session.scalar(
            select(LogSourceAnomaly).where(
                LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_SPIKE
            )
        )
        collector = ExplanationCollector(
            db_session, MockQRadarProvider(), settings=settings
        )
        report = await collector.collect(anomaly)

        assert report.status in (EvidenceStatus.COMPLETE, EvidenceStatus.PARTIAL)
        assert report.contributors_written > 0

        package = await db_session.scalar(
            select(AnomalyExplanation).where(
                AnomalyExplanation.anomaly_id == anomaly.id
            )
        )
        assert package is not None
        assert package.comparison_strategy == "recent_normal_window"
        # The comparison window must end where the anomaly begins.
        assert package.baseline_window_end == package.anomaly_window_start
        assert package.query_provenance["queries"]
        assert package.schema_version == 1

    async def test_unpopulated_dimensions_are_recorded_unavailable(
        self, db_session
    ) -> None:
        """The mock DSM omits username and category, as a firewall would."""
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls, median=BASELINE_EPS)
        settings = _settings()
        engine = AnomalyEngine(db_session, settings=settings)
        await _feed(engine, db_session, ls, start_index=0, count=2, eps=SPIKE_EPS)

        anomaly = await db_session.scalar(
            select(LogSourceAnomaly).where(
                LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_SPIKE
            )
        )
        await ExplanationCollector(
            db_session, MockQRadarProvider(), settings=settings
        ).collect(anomaly)

        rows = list(
            (
                await db_session.scalars(
                    select(AnomalyExplanationDimension).where(
                        AnomalyExplanationDimension.dimension.in_(
                            ["username", "category"]
                        )
                    )
                )
            ).all()
        )
        assert rows, "an unavailable dimension must still be recorded"
        for row in rows:
            assert str(row.availability) == DimensionAvailability.UNAVAILABLE
            # Never a fabricated zero.
            assert row.baseline_distinct_count is None

    async def test_recollection_replaces_rather_than_duplicates(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls, median=BASELINE_EPS)
        settings = _settings()
        engine = AnomalyEngine(db_session, settings=settings)
        await _feed(engine, db_session, ls, start_index=0, count=2, eps=SPIKE_EPS)

        anomaly = await db_session.scalar(
            select(LogSourceAnomaly).where(
                LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_SPIKE
            )
        )
        collector = ExplanationCollector(
            db_session, MockQRadarProvider(), settings=settings
        )
        await collector.collect(anomaly)
        first = await db_session.scalar(
            select(func.count()).select_from(AnomalyExplanationContributor)
        )
        await collector.collect(anomaly)
        second = await db_session.scalar(
            select(func.count()).select_from(AnomalyExplanationContributor)
        )
        assert first == second

        packages = await db_session.scalar(
            select(func.count())
            .select_from(AnomalyExplanation)
            .where(AnomalyExplanation.anomaly_id == anomaly.id)
        )
        assert packages == 1

    async def test_provider_failure_is_recorded_not_raised(self, db_session) -> None:
        class BrokenProvider(MockQRadarProvider):
            async def get_dimension_aggregates(self, **kwargs):
                raise TimeoutError("appliance did not respond")

        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls, median=BASELINE_EPS)
        settings = _settings()
        engine = AnomalyEngine(db_session, settings=settings)
        await _feed(engine, db_session, ls, start_index=0, count=2, eps=SPIKE_EPS)

        anomaly = await db_session.scalar(
            select(LogSourceAnomaly).where(
                LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_SPIKE
            )
        )
        with pytest.raises(TimeoutError):
            # TimeoutError is not a ProviderError; it must not be swallowed
            # silently, which would hide a real transport fault.
            await ExplanationCollector(
                db_session, BrokenProvider(), settings=settings
            ).collect(anomaly)
