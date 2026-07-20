"""Analytics rule and building-block inventory collection.

The central design constraint: **synchronization must never destroy SOC
curation**. QRadar knows nothing about MITRE mappings, ownership intent or
expected firing rates, so those columns are owned by this platform and are
skipped on every sync. Only the QRadar-mirrored block is overwritten.

Dependencies are recorded with provenance. A dependency QRadar told us about is
EXPLICIT with confidence 1.0. A dependency the platform guessed — for example a
log-source type inferred from a rule's name — is INFERRED, carries a confidence
below 1.0, and is stored in the same table with `source` set accordingly, so no
consumer can mistake a guess for a fact.

Nothing here mutates QRadar: rules are not enabled, disabled or edited, and
building blocks are not modified. The provider interface exposes no such call.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import MappingSource, RuleDependencyKind
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.models.rule import AnalyticsRule, RuleDependency, RuleStateTransition
from app.providers.base import ProviderError, QRadarProvider
from app.providers.dto import AnalyticsRuleDTO
from app.security.sanitizer import sanitize_text
from app.services.locks import CollectorAdvisoryLock

logger = logging.getLogger("app.collectors.rule")

COLLECTOR_NAME = "rule_inventory"

#: Columns the SOC owns. Listed explicitly rather than by exclusion so that
#: adding a QRadar-mirrored column can never silently start clobbering
#: analyst-entered data.
SOC_OWNED_FIELDS = frozenset(
    {
        "owner",
        "mitre_techniques",
        "soc_notes",
        "expected_daily_firings",
        "health_monitoring_enabled",
    }
)


@dataclass
class RuleSyncReport:
    instance_id: uuid.UUID
    rules_seen: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    building_blocks: int = 0
    dependencies_written: int = 0
    transitions_recorded: int = 0
    skipped_locked: bool = False
    partial_failure: bool = False
    error: str | None = None
    duration_ms: int = 0
    enabled_to_disabled: list[int] = field(default_factory=list)
    disabled_to_enabled: list[int] = field(default_factory=list)


class RuleCollector:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def sync(self, instance: QRadarInstance) -> RuleSyncReport:
        started = self._clock()
        report = RuleSyncReport(instance_id=instance.id)

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                report.skipped_locked = True
                return report

            watermark = await self._get_or_create_watermark(instance)

            try:
                rules = await self.provider.list_rules()
                building_blocks = await self.provider.list_building_blocks()
            except ProviderError as exc:
                watermark.consecutive_failures += 1
                watermark.last_run_at = started
                watermark.last_error = str(exc)[:512]
                await self.session.flush()
                report.partial_failure = True
                report.error = str(exc)[:512]
                logger.warning(
                    "rule inventory sync failed",
                    extra={"instance_id": str(instance.id), "error_class": type(exc).__name__},
                )
                return report

            # A building block returned by both endpoints must not be stored
            # twice; the dedicated endpoint wins because it sets the flag.
            merged: dict[int, AnalyticsRuleDTO] = {r.qradar_id: r for r in rules}
            for bb in building_blocks:
                merged[bb.qradar_id] = bb
            report.building_blocks = len(building_blocks)
            report.rules_seen = len(merged)

            existing = await self._existing_by_qradar_id(instance.id)

            for dto in merged.values():
                row = existing.get(dto.qradar_id)
                if row is None:
                    row = self._create(instance, dto, now=started)
                    self.session.add(row)
                    await self.session.flush()
                    report.created += 1
                else:
                    changed, transition = self._update(row, dto, now=started)
                    if transition is not None:
                        self.session.add(transition)
                        report.transitions_recorded += 1
                        if transition.previous_enabled and not transition.current_enabled:
                            report.enabled_to_disabled.append(dto.qradar_id)
                        else:
                            report.disabled_to_enabled.append(dto.qradar_id)
                    if changed:
                        report.updated += 1
                    else:
                        report.unchanged += 1

                report.dependencies_written += await self._sync_dependencies(row, dto)

            watermark.last_run_at = started
            watermark.watermark_at = started
            watermark.intervals_collected += 1
            watermark.consecutive_failures = 0
            watermark.last_error = None
            watermark.lag_seconds = 0
            await self.session.flush()

        report.duration_ms = int((self._clock() - started).total_seconds() * 1000)
        logger.info(
            "rule inventory sync complete",
            extra={
                "instance_id": str(instance.id),
                "rules_seen": report.rules_seen,
                # Not "created": LogRecord reserves that name for its own
                # timestamp and logging raises KeyError rather than shadowing
                # it, so this crashed rule sync wherever INFO was enabled.
                "rows_created": report.created,
                "rows_updated": report.updated,
                "transitions": report.transitions_recorded,
                "duration_ms": report.duration_ms,
            },
        )
        return report

    # ------------------------------------------------------------- mapping
    def _create(
        self, instance: QRadarInstance, dto: AnalyticsRuleDTO, *, now: datetime
    ) -> AnalyticsRule:
        row = AnalyticsRule(
            instance_id=instance.id,
            qradar_id=dto.qradar_id,
            # first_seen_at anchors the grace period. Without it a rule created
            # in QRadar years ago but only just collected would look brand new,
            # and one collected today would look ancient.
            first_seen_at=dto.created_at or now,
        )
        self._apply_qradar_fields(row, dto)
        return row

    def _update(
        self, row: AnalyticsRule, dto: AnalyticsRuleDTO, *, now: datetime
    ) -> tuple[bool, RuleStateTransition | None]:
        """Refresh QRadar-owned fields. SOC-owned fields are never touched."""
        previous_enabled = row.enabled
        before = self._qradar_fingerprint(row)
        self._apply_qradar_fields(row, dto)
        changed = self._qradar_fingerprint(row) != before

        transition: RuleStateTransition | None = None
        if previous_enabled != row.enabled:
            transition = RuleStateTransition(
                rule_id=row.id,
                observed_at=now,
                previous_enabled=previous_enabled,
                current_enabled=row.enabled,
            )
        if row.first_seen_at is None:
            row.first_seen_at = dto.created_at or now
        return changed, transition

    @staticmethod
    def _apply_qradar_fields(row: AnalyticsRule, dto: AnalyticsRuleDTO) -> None:
        row.name = sanitize_text(dto.name) or f"rule-{dto.qradar_id}"
        row.description = sanitize_text(dto.description)
        row.rule_type = dto.rule_type
        row.enabled = dto.enabled
        row.origin = dto.origin
        row.is_building_block = dto.is_building_block
        row.categories = [c for c in (sanitize_text(c) for c in dto.categories) if c]
        row.average_capacity = dto.average_capacity
        row.qradar_created_at = dto.created_at
        row.qradar_modified_at = dto.modified_at
        row.generates_offense = dto.generates_offense
        row.response_actions = [
            a for a in (sanitize_text(a) for a in dto.response_actions) if a
        ]
        if dto.last_triggered_at is not None:
            row.last_fired_at = dto.last_triggered_at
        if dto.event_contribution_count is not None:
            row.event_contribution_count = dto.event_contribution_count
        if dto.offense_contribution_count is not None:
            row.offense_contribution_count = dto.offense_contribution_count

    @staticmethod
    def _qradar_fingerprint(row: AnalyticsRule) -> tuple:
        """Comparable tuple of the QRadar-owned fields only.

        Used to report whether a sync actually changed anything, which keeps the
        idempotency assertion honest: re-running a sync over unchanged upstream
        data must report zero updates.
        """
        return (
            row.name,
            row.description,
            row.rule_type,
            row.enabled,
            row.origin,
            row.is_building_block,
            tuple(row.categories or ()),
            row.average_capacity,
            row.qradar_created_at,
            row.qradar_modified_at,
            row.generates_offense,
            tuple(row.response_actions or ()),
            row.last_fired_at,
            row.event_contribution_count,
            row.offense_contribution_count,
        )

    # --------------------------------------------------------- dependencies
    async def _sync_dependencies(self, row: AnalyticsRule, dto: AnalyticsRuleDTO) -> int:
        """Replace the EXPLICIT dependencies QRadar reported for this rule.

        INFERRED rows are left alone: they are the platform's own analysis (or a
        SOC correction of it) and are not QRadar's to overwrite.
        """
        existing = list(
            (
                await self.session.scalars(
                    select(RuleDependency).where(
                        RuleDependency.rule_id == row.id,
                        RuleDependency.source == MappingSource.EXPLICIT,
                    )
                )
            ).all()
        )
        by_key = {(d.kind, d.target_ref): d for d in existing}

        desired: list[tuple[RuleDependencyKind, str]] = [
            (RuleDependencyKind.BUILDING_BLOCK, str(bb_id))
            for bb_id in dto.building_block_ids
        ] + [
            (RuleDependencyKind.LOG_SOURCE_TYPE, str(ls_type))
            for ls_type in dto.log_source_type_ids
        ]

        written = 0
        for kind, target_ref in desired:
            if (kind, target_ref) in by_key:
                by_key.pop((kind, target_ref))
                continue
            self.session.add(
                RuleDependency(
                    rule_id=row.id,
                    kind=kind,
                    target_ref=target_ref,
                    source=MappingSource.EXPLICIT,
                    confidence=1.0,
                    provenance="qradar:rule_payload",
                )
            )
            written += 1

        # Anything QRadar no longer reports is stale; drop it so a rule that
        # lost a building block stops claiming the dependency.
        for stale in by_key.values():
            await self.session.delete(stale)

        await self.session.flush()
        return written

    async def _existing_by_qradar_id(
        self, instance_id: uuid.UUID
    ) -> dict[int, AnalyticsRule]:
        rows = (
            await self.session.scalars(
                select(AnalyticsRule).where(AnalyticsRule.instance_id == instance_id)
            )
        ).all()
        return {r.qradar_id: r for r in rows}

    async def _get_or_create_watermark(
        self, instance: QRadarInstance
    ) -> CollectionWatermark:
        wm = await self.session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector == COLLECTOR_NAME,
            )
        )
        if wm is None:
            wm = CollectionWatermark(instance_id=instance.id, collector=COLLECTOR_NAME)
            self.session.add(wm)
            await self.session.flush()
        return wm
