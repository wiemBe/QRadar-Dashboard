"""Offense contribution -> RuleMetric with honest completeness semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.collectors.rule_metric_collector import RuleMetricCollector
from app.core.config import get_settings
from app.models.enums import RuleHealthStatus
from app.models.monitoring import CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.models.rule import AnalyticsRule, RuleMetric
from app.services.rule_health import RuleHealthEvaluator
from tests.integration.factories import make_instance

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


async def add_rule(session, instance, qradar_id: int) -> AnalyticsRule:
    rule = AnalyticsRule(
        instance_id=instance.id,
        qradar_id=qradar_id,
        name=f"Rule {qradar_id}",
        enabled=True,
        is_building_block=False,
        qradar_created_at=NOW - timedelta(days=90),
        first_seen_at=NOW - timedelta(days=90),
    )
    session.add(rule)
    await session.flush()
    return rule


async def add_offense(session, instance, *, qradar_id: int, rule_ids: list[int]) -> None:
    session.add(
        OffenseSnapshot(
            instance_id=instance.id,
            qradar_offense_id=qradar_id,
            captured_at=NOW - timedelta(hours=1),
            start_time=NOW - timedelta(hours=2),
            status="OPEN",
            rule_ids=rule_ids,
        )
    )
    await session.flush()


def collector(session, **overrides) -> RuleMetricCollector:
    settings = get_settings().model_copy(
        update={"rule_metric_lookback_days": 30, **overrides}
    )
    return RuleMetricCollector(session, settings=settings, clock=lambda: NOW)


@pytest.mark.asyncio
async def test_writes_inferred_metric_with_provenance_and_incomplete_watermark(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    rule = await add_rule(db_session, instance, 100)
    await add_offense(db_session, instance, qradar_id=7, rule_ids=[100, 100, 999])

    report = await collector(db_session).collect(instance)

    metric = await db_session.scalar(select(RuleMetric).where(RuleMetric.rule_id == rule.id))
    assert metric is not None
    assert metric.fire_count == 1
    assert metric.offense_contribution_count == 1
    assert metric.provenance == "offense_contribution"
    assert metric.completeness == "incomplete"
    assert metric.inferred is True
    assert report.rules_matched == 1
    assert report.unmatched_rule_ids == 1

    watermark = await db_session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance.id,
            CollectionWatermark.collector == "rule_metric",
        )
    )
    assert watermark is not None
    assert watermark.collection_metadata["zero_is_verified"] is False
    assert watermark.collection_metadata["completeness"] == "incomplete"


@pytest.mark.asyncio
async def test_second_run_is_idempotent(db_session) -> None:
    instance = await make_instance(db_session)
    await add_rule(db_session, instance, 100)
    await add_offense(db_session, instance, qradar_id=7, rule_ids=[100])

    first = await collector(db_session).collect(instance)
    second = await collector(db_session).collect(instance)

    assert first.metrics_written == second.metrics_written == 1
    assert await db_session.scalar(select(func.count()).select_from(RuleMetric)) == 1
    metric = await db_session.scalar(select(RuleMetric))
    assert metric is not None
    assert metric.fire_count == 1


@pytest.mark.asyncio
async def test_positive_inference_is_healthy_but_silence_remains_insufficient(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    positive = await add_rule(db_session, instance, 100)
    silent = await add_rule(db_session, instance, 200)
    await add_offense(db_session, instance, qradar_id=7, rule_ids=[100])
    await collector(db_session).collect(instance)

    settings = get_settings().model_copy(update={"rule_health_flap_threshold": 1})
    await RuleHealthEvaluator(
        db_session, settings=settings, clock=lambda: NOW
    ).evaluate_instance(instance)

    assert positive.health_status == RuleHealthStatus.HEALTHY
    assert silent.health_status == RuleHealthStatus.INSUFFICIENT_DATA
    assert silent.health_status != RuleHealthStatus.NEVER_OBSERVED


@pytest.mark.asyncio
async def test_row_ceiling_does_not_advance_watermark(db_session) -> None:
    instance = await make_instance(db_session)
    await add_rule(db_session, instance, 100)
    await add_offense(db_session, instance, qradar_id=7, rule_ids=[100])
    await add_offense(db_session, instance, qradar_id=8, rule_ids=[100])

    report = await collector(db_session, rule_metric_max_offenses=1).collect(instance)

    assert report.partial_failure is True
    watermark = await db_session.scalar(select(CollectionWatermark))
    assert watermark is not None
    assert watermark.watermark_at is None
    assert watermark.intervals_collected == 0
    assert watermark.consecutive_failures == 1
