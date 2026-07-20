"""Detection coverage evaluation.

The chain evaluated for every technique:

    MITRE technique
      -> analytics rule            (is it enabled?)
        -> rule health             (is it actually working?)
          -> dependent building block  (is it enabled?)
            -> required log source / type  (is telemetry arriving?)
              -> current telemetry health  (recent? parsing? in maintenance?)

The rule this module exists to enforce: **the existence of a rule is never
coverage**. A technique mapped to a disabled rule, or to a rule whose required
telemetry is down, is not covered — it is DEGRADED or MISSING. A spreadsheet of
rule-to-technique mappings will happily claim 90% coverage while half the
sources feeding it are silent; that gap between believed and actual coverage is
the entire reason this platform exists.

MITRE content is SOC-owned. Technique identifiers, names and tactics come from
`TechniqueMapping` rows the SOC created. Nothing is imported from an external
MITRE source, so this module can never present unverified content as fact.

Explicit and inferred mappings stay distinguishable end to end: an inferred
mapping carries a confidence, is counted separately, and drags the technique's
overall confidence down rather than silently counting as curated truth.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import CoverageStatus, MappingSource, RuleHealthStatus
from app.models.instance import QRadarInstance
from app.models.rule import (
    AnalyticsRule,
    DetectionCoverage,
    DetectionCoverageSnapshot,
    TechniqueMapping,
)

logger = logging.getLogger("app.services.detection_coverage")

#: Rule-health states that still represent working detection.
_WORKING_HEALTH = frozenset(
    {RuleHealthStatus.HEALTHY, RuleHealthStatus.NOISY}
)

#: States where the rule exists and is enabled but is not currently detecting.
_DEGRADED_HEALTH = frozenset(
    {
        RuleHealthStatus.DEPENDENCY_DEGRADED,
        RuleHealthStatus.INACTIVE,
        RuleHealthStatus.NEVER_OBSERVED,
    }
)


@dataclass
class RuleCoverageEvidence:
    """Why one rule did or did not contribute coverage for a technique."""

    rule_qradar_id: int
    rule_name: str
    enabled: bool
    health_status: str
    mapping_source: str
    mapping_confidence: float
    contributes: bool
    reason: str


@dataclass
class TechniqueVerdict:
    technique_id: str
    technique_name: str | None
    tactic: str | None
    status: CoverageStatus
    reason: str
    coverage_score: float
    confidence: float
    mapped_rule_count: int = 0
    enabled_rule_count: int = 0
    firing_rule_count: int = 0
    inferred_rule_count: int = 0
    degraded_rule_count: int = 0
    evidence: list[RuleCoverageEvidence] = field(default_factory=list)


@dataclass
class CoverageReport:
    instance_id: uuid.UUID
    techniques_evaluated: int = 0
    snapshots_written: int = 0
    by_status: dict[str, int] = field(default_factory=dict)


class DetectionCoverageEvaluator:
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

    @property
    def logic_version(self) -> int:
        return self.settings.coverage_logic_version

    async def evaluate_instance(self, instance: QRadarInstance) -> CoverageReport:
        now = self._clock()
        report = CoverageReport(instance_id=instance.id)

        # One join loads every mapping with its rule; the loop below touches no
        # further queries, so N techniques cost O(1) round trips.
        rows = (
            await self.session.execute(
                select(TechniqueMapping, AnalyticsRule)
                .join(AnalyticsRule, TechniqueMapping.rule_id == AnalyticsRule.id)
                .where(TechniqueMapping.instance_id == instance.id)
                .order_by(TechniqueMapping.technique_id, AnalyticsRule.qradar_id)
            )
        ).all()

        by_technique: dict[str, list[tuple[TechniqueMapping, AnalyticsRule]]] = {}
        for mapping, rule in rows:
            by_technique.setdefault(mapping.technique_id, []).append((mapping, rule))

        for technique_id, pairs in by_technique.items():
            verdict = self.evaluate_technique(technique_id, pairs)
            report.techniques_evaluated += 1
            report.by_status[verdict.status] = report.by_status.get(verdict.status, 0) + 1
            await self._persist(instance, verdict, now=now)
            report.snapshots_written += 1

        await self.session.flush()
        logger.info(
            "detection coverage evaluated",
            extra={
                "instance_id": str(instance.id),
                "techniques": report.techniques_evaluated,
                "by_status": report.by_status,
            },
        )
        return report

    def evaluate_technique(
        self, technique_id: str, pairs: list[tuple[TechniqueMapping, AnalyticsRule]]
    ) -> TechniqueVerdict:
        if not pairs:
            return TechniqueVerdict(
                technique_id=technique_id,
                technique_name=None,
                tactic=None,
                status=CoverageStatus.MISSING,
                reason="No rule is mapped to this technique.",
                coverage_score=0.0,
                confidence=1.0,
            )

        # SOC-owned descriptive text; prefer an explicit mapping's copy.
        name = next(
            (m.technique_name for m, _ in pairs if m.technique_name), None
        )
        tactic = next((m.tactic for m, _ in pairs if m.tactic), None)

        evidence: list[RuleCoverageEvidence] = []
        working = degraded = enabled = inferred = 0
        confidences: list[float] = []
        min_confidence = 1.0

        for mapping, rule in pairs:
            below_floor = mapping.confidence < self.settings.coverage_min_confidence
            if mapping.source == MappingSource.INFERRED:
                inferred += 1

            if below_floor:
                evidence.append(
                    RuleCoverageEvidence(
                        rule_qradar_id=rule.qradar_id,
                        rule_name=rule.name,
                        enabled=rule.enabled,
                        health_status=str(rule.health_status),
                        mapping_source=str(mapping.source),
                        mapping_confidence=mapping.confidence,
                        contributes=False,
                        reason=(
                            f"Mapping confidence {mapping.confidence:.2f} is below the "
                            f"{self.settings.coverage_min_confidence:.2f} floor; recorded "
                            "but not counted toward coverage."
                        ),
                    )
                )
                continue

            if not rule.enabled:
                # A disabled rule can never provide active coverage. This is the
                # single most important line in the module.
                evidence.append(
                    RuleCoverageEvidence(
                        rule_qradar_id=rule.qradar_id,
                        rule_name=rule.name,
                        enabled=False,
                        health_status=str(rule.health_status),
                        mapping_source=str(mapping.source),
                        mapping_confidence=mapping.confidence,
                        contributes=False,
                        reason="The rule is disabled, so it provides no active coverage.",
                    )
                )
                continue

            enabled += 1
            health = rule.health_status

            if health in _WORKING_HEALTH:
                working += 1
                confidences.append(mapping.confidence)
                min_confidence = min(min_confidence, mapping.confidence)
                evidence.append(
                    RuleCoverageEvidence(
                        rule_qradar_id=rule.qradar_id,
                        rule_name=rule.name,
                        enabled=True,
                        health_status=str(health),
                        mapping_source=str(mapping.source),
                        mapping_confidence=mapping.confidence,
                        contributes=True,
                        reason="The rule is enabled and its health is acceptable.",
                    )
                )
            elif health in _DEGRADED_HEALTH:
                degraded += 1
                min_confidence = min(min_confidence, mapping.confidence)
                evidence.append(
                    RuleCoverageEvidence(
                        rule_qradar_id=rule.qradar_id,
                        rule_name=rule.name,
                        enabled=True,
                        health_status=str(health),
                        mapping_source=str(mapping.source),
                        mapping_confidence=mapping.confidence,
                        contributes=False,
                        reason=self._degraded_reason(health),
                    )
                )
            else:
                # INSUFFICIENT_DATA / UNKNOWN: cannot claim coverage, but this is
                # not evidence of a gap either.
                evidence.append(
                    RuleCoverageEvidence(
                        rule_qradar_id=rule.qradar_id,
                        rule_name=rule.name,
                        enabled=True,
                        health_status=str(health),
                        mapping_source=str(mapping.source),
                        mapping_confidence=mapping.confidence,
                        contributes=False,
                        reason="The rule's health has not been established yet.",
                    )
                )

        status, reason, score = self._roll_up(
            total=len(pairs), enabled=enabled, working=working, degraded=degraded
        )
        confidence = min(confidences) if confidences else min_confidence

        return TechniqueVerdict(
            technique_id=technique_id,
            technique_name=name,
            tactic=tactic,
            status=status,
            reason=reason,
            coverage_score=score,
            confidence=confidence,
            mapped_rule_count=len(pairs),
            enabled_rule_count=enabled,
            firing_rule_count=working,
            inferred_rule_count=inferred,
            degraded_rule_count=degraded,
            evidence=evidence,
        )

    @staticmethod
    def _degraded_reason(health: RuleHealthStatus) -> str:
        if health == RuleHealthStatus.DEPENDENCY_DEGRADED:
            return "The rule's required telemetry is unavailable, so it cannot fire."
        if health == RuleHealthStatus.NEVER_OBSERVED:
            return "The rule has never been observed firing."
        return "The rule has not fired within the inactivity window."

    def _roll_up(
        self, *, total: int, enabled: int, working: int, degraded: int
    ) -> tuple[CoverageStatus, str, float]:
        if enabled == 0:
            if total > 0:
                return (
                    CoverageStatus.MISSING,
                    "Every rule mapped to this technique is disabled or discounted, "
                    "so there is no active detection.",
                    0.0,
                )
            return CoverageStatus.MISSING, "No rule is mapped to this technique.", 0.0

        if working == 0:
            if degraded > 0:
                return (
                    CoverageStatus.DEGRADED,
                    f"{degraded} of {enabled} enabled rules are not currently detecting.",
                    0.0,
                )
            return (
                CoverageStatus.NOT_EVALUATED,
                "Rule health has not been established for the mapped rules.",
                0.0,
            )

        score = working / enabled
        if degraded > 0:
            return (
                CoverageStatus.DEGRADED,
                f"{working} of {enabled} enabled rules are detecting; "
                f"{degraded} are degraded.",
                score,
            )
        if working < enabled:
            return (
                CoverageStatus.PARTIAL,
                f"{working} of {enabled} enabled rules are detecting; the rest are "
                "not yet established.",
                score,
            )
        return (
            CoverageStatus.COVERED,
            f"All {enabled} enabled rules mapped to this technique are detecting.",
            1.0,
        )

    async def _persist(
        self, instance: QRadarInstance, verdict: TechniqueVerdict, *, now: datetime
    ) -> None:
        evidence_payload = {
            "rules": [
                {
                    "rule_qradar_id": e.rule_qradar_id,
                    "rule_name": e.rule_name,
                    "enabled": e.enabled,
                    "health_status": e.health_status,
                    "mapping_source": e.mapping_source,
                    "mapping_confidence": e.mapping_confidence,
                    "contributes": e.contributes,
                    "reason": e.reason,
                }
                for e in verdict.evidence
            ],
            "logic_version": self.logic_version,
        }

        current = {
            "instance_id": instance.id,
            "technique_id": verdict.technique_id,
            "technique_name": verdict.technique_name,
            "tactic": verdict.tactic,
            "status": verdict.status,
            "mapped_rule_count": verdict.mapped_rule_count,
            "enabled_rule_count": verdict.enabled_rule_count,
            "firing_rule_count": verdict.firing_rule_count,
            "inferred_rule_count": verdict.inferred_rule_count,
            "degraded_rule_count": verdict.degraded_rule_count,
            "coverage_score": verdict.coverage_score,
            "confidence": verdict.confidence,
            "last_evaluated_at": now,
            "logic_version": self.logic_version,
            "reason": verdict.reason,
            "evidence": evidence_payload,
        }
        stmt = pg_insert(DetectionCoverage).values(**current, id=uuid.uuid4())
        stmt = stmt.on_conflict_do_update(
            index_elements=["instance_id", "technique_id"],
            set_={
                k: v
                for k, v in current.items()
                if k not in ("instance_id", "technique_id")
            },
        )
        await self.session.execute(stmt)

        snapshot = {
            "instance_id": instance.id,
            "technique_id": verdict.technique_id,
            "captured_at": now,
            "status": verdict.status,
            "coverage_score": verdict.coverage_score,
            "confidence": verdict.confidence,
            "mapped_rule_count": verdict.mapped_rule_count,
            "enabled_rule_count": verdict.enabled_rule_count,
            "firing_rule_count": verdict.firing_rule_count,
            "degraded_rule_count": verdict.degraded_rule_count,
            "logic_version": self.logic_version,
            "reason": verdict.reason,
        }
        snap_stmt = pg_insert(DetectionCoverageSnapshot).values(snapshot)
        snap_stmt = snap_stmt.on_conflict_do_update(
            index_elements=["instance_id", "technique_id", "captured_at"],
            set_={
                k: v
                for k, v in snapshot.items()
                if k not in ("instance_id", "technique_id", "captured_at")
            },
        )
        await self.session.execute(snap_stmt)
