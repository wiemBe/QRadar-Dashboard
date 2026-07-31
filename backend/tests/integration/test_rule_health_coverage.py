"""Rule health and detection coverage against a real database.

Covers what the pure-function tests cannot: that `evaluate_instance` reads the
right rows, that the observation-completeness gate is driven by a real
CollectionWatermark, that snapshots accumulate as history, and that flap
damping actually holds a stored verdict back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.enums import CoverageStatus, MappingSource, RuleHealthStatus
from app.models.monitoring import CollectionWatermark
from app.models.rule import (
    AnalyticsRule,
    DetectionCoverage,
    DetectionCoverageSnapshot,
    RuleHealthSnapshot,
    RuleMetric,
    TechniqueMapping,
)
from app.services.detection_coverage import DetectionCoverageEvaluator
from app.services.rule_health import RULE_METRIC_COLLECTOR, RuleHealthEvaluator
from tests.integration.factories import make_instance

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


async def make_rule(
    session,
    instance,
    *,
    qradar_id: int = 100,
    name: str = "Suspicious Auth",
    enabled: bool = True,
    is_building_block: bool = False,
    age_days: float = 90.0,
    last_fired_at: datetime | None = None,
    health_status: RuleHealthStatus = RuleHealthStatus.UNKNOWN,
    **kwargs,
) -> AnalyticsRule:
    rule = AnalyticsRule(
        instance_id=instance.id,
        qradar_id=qradar_id,
        name=name,
        enabled=enabled,
        is_building_block=is_building_block,
        qradar_created_at=NOW - timedelta(days=age_days),
        last_fired_at=last_fired_at,
        health_status=health_status,
        **kwargs,
    )
    session.add(rule)
    await session.flush()
    return rule


async def set_metric_watermark(
    session, instance, *, at: datetime, intervals: int = 1, metadata: dict | None = None
):
    """Record that a rule-metric collection completed up to `at`."""
    wm = CollectionWatermark(
        instance_id=instance.id,
        collector=RULE_METRIC_COLLECTOR,
        watermark_at=at,
        intervals_collected=intervals,
        collection_metadata=metadata or {},
    )
    session.add(wm)
    await session.flush()
    return wm


async def add_rule_metric(session, rule, *, bucket_start: datetime, fire_count: int,
                          offense_count: int = 0):
    metric = RuleMetric(
        rule_id=rule.id,
        bucket_start=bucket_start,
        bucket_seconds=3600,
        fire_count=fire_count,
        offense_contribution_count=offense_count,
    )
    session.add(metric)
    await session.flush()
    return metric


def evaluator(session, **cfg) -> RuleHealthEvaluator:
    return RuleHealthEvaluator(
        session,
        settings=get_settings().model_copy(update=cfg) if cfg else get_settings(),
        clock=lambda: NOW,
    )


async def snapshots_for(session, rule) -> list[RuleHealthSnapshot]:
    return list(
        (
            await session.scalars(
                select(RuleHealthSnapshot)
                .where(RuleHealthSnapshot.rule_id == rule.id)
                .order_by(RuleHealthSnapshot.evaluated_at)
            )
        ).all()
    )


# ============================================================================
class TestObservationCompletenessAgainstTheDatabase:
    """The gate must be driven by a real watermark, not a caller-supplied flag."""

    @pytest.mark.asyncio
    async def test_no_watermark_yields_insufficient_data(self, db_session) -> None:
        """The current deployment state: rule-metric collection has never run."""
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=120)

        await evaluator(db_session).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert snap.status != RuleHealthStatus.NEVER_OBSERVED
        assert snap.evidence["observation_complete"] is False

    @pytest.mark.asyncio
    async def test_a_completed_collection_unlocks_never_observed(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=120)
        # A collection that covers the whole 30-day evaluation window.
        await set_metric_watermark(db_session, inst, at=NOW)

        await evaluator(db_session).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.status == RuleHealthStatus.NEVER_OBSERVED

    @pytest.mark.asyncio
    async def test_a_watermark_stopping_before_the_window_does_not_count(
        self, db_session
    ) -> None:
        """Progress that never reached the window leaves the window unobserved."""
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=200)
        await set_metric_watermark(
            db_session, inst, at=NOW - timedelta(days=60)  # window starts at -30d
        )

        await evaluator(db_session, rule_health_window_days=30).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.status == RuleHealthStatus.INSUFFICIENT_DATA

    @pytest.mark.asyncio
    async def test_a_watermark_with_zero_intervals_does_not_count(self, db_session) -> None:
        """A row created but never advanced is not a completed collection."""
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=120)
        await set_metric_watermark(db_session, inst, at=NOW, intervals=0)

        await evaluator(db_session).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.status == RuleHealthStatus.INSUFFICIENT_DATA

    @pytest.mark.asyncio
    async def test_incomplete_collection_cannot_verify_zero(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=120)
        await set_metric_watermark(
            db_session,
            inst,
            at=NOW,
            metadata={
                "provenance": "offense_contribution",
                "completeness": "incomplete",
                "zero_is_verified": False,
            },
        )

        await evaluator(db_session).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert snap.evidence["observation_status"] == "incomplete"

    @pytest.mark.asyncio
    async def test_another_collectors_watermark_does_not_count(self, db_session) -> None:
        """Offense collection running says nothing about rule firings."""
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=120)
        db_session.add(
            CollectionWatermark(
                instance_id=inst.id,
                collector="offense_snapshot",
                watermark_at=NOW,
                intervals_collected=99,
            )
        )
        await db_session.flush()

        await evaluator(db_session).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.status == RuleHealthStatus.INSUFFICIENT_DATA

    @pytest.mark.asyncio
    async def test_another_instances_watermark_does_not_count(self, db_session) -> None:
        inst = await make_instance(db_session)
        other = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=120)
        await set_metric_watermark(db_session, other, at=NOW)

        await evaluator(db_session).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.status == RuleHealthStatus.INSUFFICIENT_DATA


class TestEvaluationOverRealRows:
    @pytest.mark.asyncio
    async def test_firing_counts_come_from_rule_metric_rows(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, age_days=120, last_fired_at=NOW - timedelta(hours=2)
        )
        await set_metric_watermark(db_session, inst, at=NOW)
        for i in range(3):
            await add_rule_metric(
                db_session, rule,
                bucket_start=NOW - timedelta(days=i + 1),
                fire_count=10, offense_count=2,
            )

        await evaluator(db_session).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.status == RuleHealthStatus.HEALTHY
        assert snap.trigger_count == 30
        assert snap.offense_contribution_count == 6

    @pytest.mark.asyncio
    async def test_metrics_outside_the_window_are_excluded(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, age_days=200, last_fired_at=NOW - timedelta(days=100)
        )
        await set_metric_watermark(db_session, inst, at=NOW)
        await add_rule_metric(
            db_session, rule, bucket_start=NOW - timedelta(days=90), fire_count=500
        )

        await evaluator(db_session, rule_health_window_days=30).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.trigger_count == 0
        # Fired once, long ago, with nothing in the window: inactive.
        assert snap.status == RuleHealthStatus.INACTIVE

    @pytest.mark.asyncio
    async def test_building_blocks_are_not_evaluated_as_detections(
        self, db_session
    ) -> None:
        """Building blocks are judged through the rules that use them."""
        inst = await make_instance(db_session)
        bb = await make_rule(
            db_session, inst, qradar_id=900, name="BB: Admin", is_building_block=True
        )
        rule = await make_rule(db_session, inst, qradar_id=100)

        report = await evaluator(db_session).evaluate_instance(inst)

        assert report.evaluated == 1
        assert await snapshots_for(db_session, bb) == []
        assert len(await snapshots_for(db_session, rule)) == 1

    @pytest.mark.asyncio
    async def test_instances_are_isolated(self, db_session) -> None:
        inst = await make_instance(db_session)
        other = await make_instance(db_session)
        await make_rule(db_session, inst, qradar_id=1)
        other_rule = await make_rule(db_session, other, qradar_id=1)

        report = await evaluator(db_session).evaluate_instance(inst)

        assert report.evaluated == 1
        assert await snapshots_for(db_session, other_rule) == []

    @pytest.mark.asyncio
    async def test_an_instance_with_no_rules_is_a_clean_no_op(self, db_session) -> None:
        inst = await make_instance(db_session)
        report = await evaluator(db_session).evaluate_instance(inst)
        assert report.evaluated == 0
        assert report.snapshots_written == 0

    @pytest.mark.asyncio
    async def test_disabled_rule_is_recorded_as_disabled(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, enabled=False, age_days=120)

        await evaluator(db_session).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.status == RuleHealthStatus.DISABLED
        assert snap.enabled is False


class TestSnapshotHistory:
    @pytest.mark.asyncio
    async def test_repeated_evaluation_appends_history(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=120)

        await evaluator(db_session).evaluate_instance(inst)
        later = NOW + timedelta(days=1)
        await RuleHealthEvaluator(
            db_session, settings=get_settings(), clock=lambda: later
        ).evaluate_instance(inst)

        snaps = await snapshots_for(db_session, rule)
        assert len(snaps) == 2
        assert [s.evaluated_at for s in snaps] == [NOW, later]

    @pytest.mark.asyncio
    async def test_re_evaluating_the_same_instant_upserts(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=120)

        await evaluator(db_session).evaluate_instance(inst)
        await evaluator(db_session).evaluate_instance(inst)

        assert len(await snapshots_for(db_session, rule)) == 1

    @pytest.mark.asyncio
    async def test_snapshot_records_the_logic_version(self, db_session) -> None:
        """A verdict must be re-interpretable after the logic changes."""
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=120)

        await evaluator(db_session, rule_health_logic_version=3).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.logic_version == 3

    @pytest.mark.asyncio
    async def test_snapshot_carries_the_window_and_reason(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=120)

        await evaluator(db_session, rule_health_window_days=30).evaluate_instance(inst)

        (snap,) = await snapshots_for(db_session, rule)
        assert snap.window_end == NOW
        assert snap.window_start == NOW - timedelta(days=30)
        assert snap.reason
        assert 0.0 <= snap.confidence <= 1.0


class TestFlapDamping:
    @pytest.mark.asyncio
    async def test_a_new_verdict_is_held_until_it_settles(self, db_session) -> None:
        """A rule firing every few weeks would otherwise oscillate on every run."""
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, age_days=120, health_status=RuleHealthStatus.HEALTHY
        )

        report = await evaluator(db_session, rule_health_flap_threshold=2).evaluate_instance(
            inst
        )

        # The verdict (INSUFFICIENT_DATA) differs from the stored HEALTHY, but a
        # single observation is not enough to move it.
        assert report.held_for_flap == 1
        assert report.changed == 0
        await db_session.refresh(rule)
        assert rule.health_status == RuleHealthStatus.HEALTHY
        assert rule.health_evaluated_at == NOW

    @pytest.mark.asyncio
    async def test_a_repeated_verdict_is_applied(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, age_days=120, health_status=RuleHealthStatus.HEALTHY
        )

        for i in range(3):
            at = NOW + timedelta(days=i)
            await RuleHealthEvaluator(
                db_session,
                settings=get_settings().model_copy(update={"rule_health_flap_threshold": 2}),
                clock=lambda at=at: at,
            ).evaluate_instance(inst)

        await db_session.refresh(rule)
        assert rule.health_status == RuleHealthStatus.INSUFFICIENT_DATA

    @pytest.mark.asyncio
    async def test_a_threshold_of_one_applies_immediately(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, age_days=120, health_status=RuleHealthStatus.HEALTHY
        )

        report = await evaluator(db_session, rule_health_flap_threshold=1).evaluate_instance(
            inst
        )

        assert report.changed == 1
        await db_session.refresh(rule)
        assert rule.health_status == RuleHealthStatus.INSUFFICIENT_DATA


# ============================================================================
class TestCoveragePersistence:
    async def make_mapping(
        self, session, instance, rule, *, technique_id="T1059",
        source=MappingSource.EXPLICIT, confidence=1.0,
    ) -> TechniqueMapping:
        mapping = TechniqueMapping(
            instance_id=instance.id,
            technique_id=technique_id,
            technique_name="Command and Scripting Interpreter",
            tactic="Execution",
            rule_id=rule.id,
            source=source,
            confidence=confidence,
        )
        session.add(mapping)
        await session.flush()
        return mapping

    def evaluator(self, session, **cfg) -> DetectionCoverageEvaluator:
        return DetectionCoverageEvaluator(
            session,
            settings=get_settings().model_copy(update=cfg) if cfg else get_settings(),
            clock=lambda: NOW,
        )

    async def coverage_rows(self, session, instance) -> list[DetectionCoverage]:
        return list(
            (
                await session.scalars(
                    select(DetectionCoverage)
                    .where(DetectionCoverage.instance_id == instance.id)
                    .order_by(DetectionCoverage.technique_id)
                )
            ).all()
        )

    @pytest.mark.asyncio
    async def test_a_healthy_rule_produces_covered(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, health_status=RuleHealthStatus.HEALTHY
        )
        await self.make_mapping(db_session, inst, rule)

        report = await self.evaluator(db_session).evaluate_instance(inst)

        (row,) = await self.coverage_rows(db_session, inst)
        assert report.techniques_evaluated == 1
        assert row.status == CoverageStatus.COVERED
        assert row.coverage_score == 1.0
        assert row.technique_id == "T1059"
        assert row.tactic == "Execution"
        assert row.last_evaluated_at == NOW

    @pytest.mark.asyncio
    async def test_an_unassessed_rule_is_not_evaluated_not_covered(
        self, db_session
    ) -> None:
        """The default state of a freshly synced rule must not read as coverage."""
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, health_status=RuleHealthStatus.UNKNOWN
        )
        await self.make_mapping(db_session, inst, rule)

        await self.evaluator(db_session).evaluate_instance(inst)

        (row,) = await self.coverage_rows(db_session, inst)
        assert row.status == CoverageStatus.NOT_EVALUATED
        assert row.coverage_score == 0.0

    @pytest.mark.asyncio
    async def test_a_disabled_rule_yields_missing(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, enabled=False, health_status=RuleHealthStatus.HEALTHY
        )
        await self.make_mapping(db_session, inst, rule)

        await self.evaluator(db_session).evaluate_instance(inst)

        (row,) = await self.coverage_rows(db_session, inst)
        assert row.status == CoverageStatus.MISSING
        assert row.enabled_rule_count == 0
        assert row.mapped_rule_count == 1

    @pytest.mark.asyncio
    async def test_evidence_is_persisted_for_review(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, name="Encoded PowerShell",
            health_status=RuleHealthStatus.DEPENDENCY_DEGRADED,
        )
        await self.make_mapping(db_session, inst, rule)

        await self.evaluator(db_session).evaluate_instance(inst)

        (row,) = await self.coverage_rows(db_session, inst)
        assert row.status == CoverageStatus.DEGRADED
        rules = row.evidence["rules"]
        assert len(rules) == 1
        assert rules[0]["rule_name"] == "Encoded PowerShell"
        assert rules[0]["contributes"] is False
        assert row.reason

    @pytest.mark.asyncio
    async def test_multiple_rules_roll_up_to_one_technique_row(self, db_session) -> None:
        inst = await make_instance(db_session)
        healthy = await make_rule(
            db_session, inst, qradar_id=1, health_status=RuleHealthStatus.HEALTHY
        )
        degraded = await make_rule(
            db_session, inst, qradar_id=2, health_status=RuleHealthStatus.INACTIVE
        )
        await self.make_mapping(db_session, inst, healthy)
        await self.make_mapping(db_session, inst, degraded)

        await self.evaluator(db_session).evaluate_instance(inst)

        rows = await self.coverage_rows(db_session, inst)
        assert len(rows) == 1
        assert rows[0].status == CoverageStatus.DEGRADED
        assert rows[0].mapped_rule_count == 2
        assert rows[0].firing_rule_count == 1
        assert rows[0].degraded_rule_count == 1

    @pytest.mark.asyncio
    async def test_inferred_provenance_is_retained(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, health_status=RuleHealthStatus.HEALTHY
        )
        await self.make_mapping(
            db_session, inst, rule, source=MappingSource.INFERRED, confidence=0.7
        )

        await self.evaluator(db_session).evaluate_instance(inst)

        (row,) = await self.coverage_rows(db_session, inst)
        assert row.inferred_rule_count == 1
        assert row.confidence == pytest.approx(0.7)
        assert row.evidence["rules"][0]["mapping_source"] == str(MappingSource.INFERRED)

    @pytest.mark.asyncio
    async def test_re_evaluation_updates_in_place_and_appends_a_snapshot(
        self, db_session
    ) -> None:
        """The current row is a projection; history is the snapshot table."""
        inst = await make_instance(db_session)
        rule = await make_rule(
            db_session, inst, health_status=RuleHealthStatus.HEALTHY
        )
        await self.make_mapping(db_session, inst, rule)

        await self.evaluator(db_session).evaluate_instance(inst)
        rule.enabled = False
        await db_session.flush()
        later = NOW + timedelta(days=1)
        await DetectionCoverageEvaluator(
            db_session, settings=get_settings(), clock=lambda: later
        ).evaluate_instance(inst)

        rows = await self.coverage_rows(db_session, inst)
        assert len(rows) == 1
        assert rows[0].status == CoverageStatus.MISSING

        snaps = list(
            (
                await db_session.scalars(
                    select(DetectionCoverageSnapshot)
                    .where(DetectionCoverageSnapshot.instance_id == inst.id)
                    .order_by(DetectionCoverageSnapshot.captured_at)
                )
            ).all()
        )
        assert [s.status for s in snaps] == [
            CoverageStatus.COVERED, CoverageStatus.MISSING
        ]

    @pytest.mark.asyncio
    async def test_techniques_are_isolated_per_instance(self, db_session) -> None:
        inst = await make_instance(db_session)
        other = await make_instance(db_session)
        rule = await make_rule(db_session, inst, health_status=RuleHealthStatus.HEALTHY)
        other_rule = await make_rule(
            db_session, other, health_status=RuleHealthStatus.HEALTHY
        )
        await self.make_mapping(db_session, inst, rule)
        await self.make_mapping(db_session, other, other_rule)

        await self.evaluator(db_session).evaluate_instance(inst)

        assert len(await self.coverage_rows(db_session, inst)) == 1
        assert await self.coverage_rows(db_session, other) == []

    @pytest.mark.asyncio
    async def test_an_unmapped_instance_is_a_clean_no_op(self, db_session) -> None:
        inst = await make_instance(db_session)
        await make_rule(db_session, inst, health_status=RuleHealthStatus.HEALTHY)

        report = await self.evaluator(db_session).evaluate_instance(inst)

        assert report.techniques_evaluated == 0
        assert await self.coverage_rows(db_session, inst) == []

    @pytest.mark.asyncio
    async def test_health_and_coverage_compose_end_to_end(self, db_session) -> None:
        """The property the two services exist to produce together.

        With no completed metric collection, an enabled silent rule is
        INSUFFICIENT_DATA -- and the technique it covers is therefore
        NOT_EVALUATED, not COVERED and not MISSING.
        """
        inst = await make_instance(db_session)
        rule = await make_rule(db_session, inst, age_days=120)
        await self.make_mapping(db_session, inst, rule)

        await evaluator(db_session, rule_health_flap_threshold=1).evaluate_instance(inst)
        await db_session.refresh(rule)
        assert rule.health_status == RuleHealthStatus.INSUFFICIENT_DATA

        await self.evaluator(db_session).evaluate_instance(inst)

        (row,) = await self.coverage_rows(db_session, inst)
        assert row.status == CoverageStatus.NOT_EVALUATED
