"""Infer lower-bound rule metrics from stored offense contribution data.

QRadar 7.6's analytics-rule inventory exposes no firing counters and there is
no rule-statistics endpoint to call.  Offense ``rule_ids`` are nevertheless
defensible positive evidence: a listed rule contributed to that offense and
therefore fired at least once.  They are *not* a complete view because many
rule firings create no offense.

This collector records that distinction on every row and on its watermark.
It never emits zero rows for unmentioned rules and never marks its observation
window complete, so downstream rule health cannot turn missing contribution
data into ``NEVER_OBSERVED``.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.models.rule import AnalyticsRule, RuleMetric
from app.services.locks import CollectorAdvisoryLock

logger = logging.getLogger("app.collectors.rule_metric")

COLLECTOR_NAME = "rule_metric"
PROVENANCE = "offense_contribution"
COMPLETENESS = "incomplete"
BUCKET_SECONDS = 86_400


@dataclass
class RuleMetricCollectionReport:
    instance_id: uuid.UUID
    offenses_seen: int = 0
    rules_matched: int = 0
    metrics_written: int = 0
    contributions: int = 0
    unmatched_rule_ids: int = 0
    skipped_locked: bool = False
    partial_failure: bool = False
    duration_ms: int = 0
    watermark_at: datetime | None = None
    error: str | None = None


def floor_to_day(value: datetime) -> datetime:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    aware = aware.astimezone(UTC)
    return aware.replace(hour=0, minute=0, second=0, microsecond=0)


class RuleMetricCollector:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def collect(self, instance: QRadarInstance) -> RuleMetricCollectionReport:
        started = self._clock()
        report = RuleMetricCollectionReport(instance_id=instance.id)

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                report.skipped_locked = True
                return report

            watermark = await self._get_or_create_watermark(instance)
            cutoff = started - timedelta(days=self.settings.rule_metric_lookback_days)
            offenses = await self._latest_offenses(instance.id, cutoff)
            if len(offenses) > self.settings.rule_metric_max_offenses:
                # The query deliberately fetches max+1 so truncation is known,
                # not silently mistaken for a complete operational pass.
                report.partial_failure = True
                report.error = "offense row ceiling reached"
                watermark.last_run_at = started
                watermark.consecutive_failures += 1
                watermark.last_error = report.error
                watermark.collection_metadata = {
                    "provenance": PROVENANCE,
                    "completeness": COMPLETENESS,
                    "truncated": True,
                }
                await self.session.flush()
                report.duration_ms = int(
                    (self._clock() - started).total_seconds() * 1000
                )
                return report

            report.offenses_seen = len(offenses)
            rules = list(
                (
                    await self.session.scalars(
                        select(AnalyticsRule).where(
                            AnalyticsRule.instance_id == instance.id
                        )
                    )
                ).all()
            )
            by_qradar_id = {rule.qradar_id: rule for rule in rules}

            # One offense contributes at most once to a rule, even if QRadar
            # repeats that rule id in a malformed list or the offense has many
            # change snapshots. _latest_offenses already chooses one snapshot.
            counts: dict[tuple[uuid.UUID, datetime], int] = defaultdict(int)
            latest_evidence: dict[uuid.UUID, datetime] = {}
            unmatched: set[int] = set()
            matched_rules: set[uuid.UUID] = set()

            for offense in offenses:
                observed_at = offense.start_time or offense.captured_at
                bucket = floor_to_day(observed_at)
                if bucket < floor_to_day(cutoff):
                    continue
                for qradar_id in set(offense.rule_ids or []):
                    rule = by_qradar_id.get(qradar_id)
                    if rule is None:
                        unmatched.add(qradar_id)
                        continue
                    counts[(rule.id, bucket)] += 1
                    matched_rules.add(rule.id)
                    previous = latest_evidence.get(rule.id)
                    if previous is None or observed_at > previous:
                        latest_evidence[rule.id] = observed_at

            rows = [
                {
                    "rule_id": rule_id,
                    "bucket_start": bucket,
                    "bucket_seconds": BUCKET_SECONDS,
                    # A contributing offense proves a lower bound of one
                    # firing. `inferred=True` makes that lower-bound semantics
                    # explicit to every consumer.
                    "fire_count": count,
                    "offense_contribution_count": count,
                    "false_positive_ratio": None,
                    "provenance": PROVENANCE,
                    "completeness": COMPLETENESS,
                    "inferred": True,
                }
                for (rule_id, bucket), count in counts.items()
            ]
            if rows:
                stmt = pg_insert(RuleMetric).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["rule_id", "bucket_start"],
                    set_={
                        "bucket_seconds": stmt.excluded.bucket_seconds,
                        "fire_count": stmt.excluded.fire_count,
                        "offense_contribution_count": (
                            stmt.excluded.offense_contribution_count
                        ),
                        "false_positive_ratio": stmt.excluded.false_positive_ratio,
                        "provenance": stmt.excluded.provenance,
                        "completeness": stmt.excluded.completeness,
                        "inferred": stmt.excluded.inferred,
                    },
                )
                await self.session.execute(stmt)

            totals: dict[uuid.UUID, int] = defaultdict(int)
            for (rule_id, _), count in counts.items():
                totals[rule_id] += count

            for rule in rules:
                when = latest_evidence.get(rule.id)
                if when is not None and (
                    rule.last_fired_at is None
                    or self._aware(rule.last_fired_at) < self._aware(when)
                ):
                    rule.last_fired_at = when
                rule.offense_contribution_count = totals.get(rule.id, 0)

            report.rules_matched = len(matched_rules)
            report.metrics_written = len(rows)
            report.contributions = sum(counts.values())
            report.unmatched_rule_ids = len(unmatched)

            watermark.watermark_at = started
            watermark.last_run_at = started
            watermark.lag_seconds = 0
            watermark.intervals_collected += 1
            watermark.consecutive_failures = 0
            watermark.last_error = None
            watermark.collection_metadata = {
                "provenance": PROVENANCE,
                "completeness": COMPLETENESS,
                "metric_kind": "inferred",
                "zero_is_verified": False,
                "lookback_days": self.settings.rule_metric_lookback_days,
                "offenses_seen": report.offenses_seen,
            }
            await self.session.flush()
            report.watermark_at = watermark.watermark_at

        report.duration_ms = int((self._clock() - started).total_seconds() * 1000)
        logger.info(
            "rule metric collection complete",
            extra={
                "instance_id": str(instance.id),
                "offenses_seen": report.offenses_seen,
                "rules_matched": report.rules_matched,
                "metrics_written": report.metrics_written,
                "contributions": report.contributions,
                "duration_ms": report.duration_ms,
                "completeness": COMPLETENESS,
            },
        )
        return report

    async def _latest_offenses(
        self, instance_id: uuid.UUID, cutoff: datetime
    ) -> list[OffenseSnapshot]:
        stmt = (
            select(OffenseSnapshot)
            .where(
                OffenseSnapshot.instance_id == instance_id,
                OffenseSnapshot.captured_at >= cutoff,
            )
            .distinct(OffenseSnapshot.qradar_offense_id)
            .order_by(
                OffenseSnapshot.qradar_offense_id,
                OffenseSnapshot.captured_at.desc(),
            )
            .limit(self.settings.rule_metric_max_offenses + 1)
        )
        return list((await self.session.scalars(stmt)).all())

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    async def _get_or_create_watermark(
        self, instance: QRadarInstance
    ) -> CollectionWatermark:
        watermark = await self.session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector == COLLECTOR_NAME,
            )
        )
        if watermark is None:
            watermark = CollectionWatermark(
                instance_id=instance.id,
                collector=COLLECTOR_NAME,
                collection_metadata={
                    "provenance": PROVENANCE,
                    "completeness": COMPLETENESS,
                    "zero_is_verified": False,
                },
            )
            self.session.add(watermark)
            await self.session.flush()
        return watermark
