"""Detection-coverage queries.

The rule-centric and data-source-centric views both need mapping + rule state in
one shot; each is a single join rather than a per-technique lookup, so the
coverage pages cost a constant number of queries regardless of how many
techniques the SOC has mapped.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import CoverageStatus, MappingSource, RuleDependencyKind
from app.models.rule import (
    AnalyticsRule,
    DetectionCoverage,
    DetectionCoverageSnapshot,
    RuleDependency,
    TechniqueMapping,
)


class CoverageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def summary(self, instance_id: uuid.UUID | None = None) -> dict[str, Any]:
        stmt = select(
            DetectionCoverage.status,
            func.count(),
            func.avg(DetectionCoverage.coverage_score),
        )
        if instance_id is not None:
            stmt = stmt.where(DetectionCoverage.instance_id == instance_id)
        rows = (await self.session.execute(stmt.group_by(DetectionCoverage.status))).all()

        by_status = {str(status): int(count) for status, count, _ in rows}
        total = sum(by_status.values())
        covered = by_status.get(CoverageStatus.COVERED, 0)

        avg_stmt = select(func.avg(DetectionCoverage.coverage_score))
        if instance_id is not None:
            avg_stmt = avg_stmt.where(DetectionCoverage.instance_id == instance_id)
        average = await self.session.scalar(avg_stmt)

        return {
            "total_techniques": total,
            "by_status": by_status,
            # Deliberately reported alongside the full status breakdown: a
            # single percentage hides the difference between "no rule" and
            # "rule exists but its telemetry is down".
            "covered_ratio": round(covered / total, 4) if total else 0.0,
            "average_coverage_score": round(float(average), 4) if average is not None else 0.0,
        }

    async def list_techniques(
        self,
        *,
        instance_id: uuid.UUID | None = None,
        status: CoverageStatus | None = None,
        tactic: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[DetectionCoverage], int]:
        stmt = select(DetectionCoverage)
        count_stmt = select(func.count(DetectionCoverage.id))
        if instance_id is not None:
            stmt = stmt.where(DetectionCoverage.instance_id == instance_id)
            count_stmt = count_stmt.where(DetectionCoverage.instance_id == instance_id)
        if status is not None:
            stmt = stmt.where(DetectionCoverage.status == status)
            count_stmt = count_stmt.where(DetectionCoverage.status == status)
        if tactic:
            stmt = stmt.where(DetectionCoverage.tactic == tactic)
            count_stmt = count_stmt.where(DetectionCoverage.tactic == tactic)

        total = int(await self.session.scalar(count_stmt) or 0)
        stmt = stmt.order_by(DetectionCoverage.technique_id).limit(limit).offset(offset)
        return list((await self.session.scalars(stmt)).all()), total

    async def get_technique(
        self, instance_id: uuid.UUID, technique_id: str
    ) -> DetectionCoverage | None:
        return await self.session.scalar(
            select(DetectionCoverage).where(
                DetectionCoverage.instance_id == instance_id,
                DetectionCoverage.technique_id == technique_id,
            )
        )

    async def technique_history(
        self,
        instance_id: uuid.UUID,
        technique_id: str,
        *,
        limit: int,
        since: datetime | None = None,
    ) -> list[DetectionCoverageSnapshot]:
        stmt = select(DetectionCoverageSnapshot).where(
            DetectionCoverageSnapshot.instance_id == instance_id,
            DetectionCoverageSnapshot.technique_id == technique_id,
        )
        if since is not None:
            stmt = stmt.where(DetectionCoverageSnapshot.captured_at >= since)
        stmt = stmt.order_by(DetectionCoverageSnapshot.captured_at.desc()).limit(limit)
        return list((await self.session.scalars(stmt)).all())

    async def rule_centric(
        self, instance_id: uuid.UUID | None = None, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Every mapped rule with the techniques it claims to cover."""
        stmt = (
            select(AnalyticsRule, TechniqueMapping)
            .join(TechniqueMapping, TechniqueMapping.rule_id == AnalyticsRule.id)
            .order_by(AnalyticsRule.name, TechniqueMapping.technique_id)
            .limit(limit)
        )
        if instance_id is not None:
            stmt = stmt.where(TechniqueMapping.instance_id == instance_id)

        grouped: dict[uuid.UUID, dict[str, Any]] = {}
        for rule, mapping in (await self.session.execute(stmt)).all():
            entry = grouped.setdefault(
                rule.id,
                {
                    "rule_id": str(rule.id),
                    "qradar_id": rule.qradar_id,
                    "name": rule.name,
                    "enabled": rule.enabled,
                    "health_status": str(rule.health_status),
                    "techniques": [],
                },
            )
            entry["techniques"].append(
                {
                    "technique_id": mapping.technique_id,
                    "technique_name": mapping.technique_name,
                    "tactic": mapping.tactic,
                    "source": str(mapping.source),
                    "confidence": mapping.confidence,
                    "provenance": mapping.provenance,
                }
            )
        return list(grouped.values())

    async def data_source_centric(
        self, instance_id: uuid.UUID | None = None, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Log-source dependencies with the rules and techniques resting on them.

        This is the view that answers "if this source goes dark, what detection
        do we lose", which is the question a spreadsheet of mappings cannot
        answer at all.
        """
        stmt = (
            select(RuleDependency, AnalyticsRule, TechniqueMapping)
            .join(AnalyticsRule, RuleDependency.rule_id == AnalyticsRule.id)
            .outerjoin(TechniqueMapping, TechniqueMapping.rule_id == AnalyticsRule.id)
            .where(
                RuleDependency.kind.in_(
                    [RuleDependencyKind.LOG_SOURCE_TYPE, RuleDependencyKind.LOG_SOURCE]
                )
            )
            .order_by(RuleDependency.target_ref, AnalyticsRule.name)
            .limit(limit)
        )
        if instance_id is not None:
            stmt = stmt.where(AnalyticsRule.instance_id == instance_id)

        grouped: dict[str, dict[str, Any]] = {}
        for dep, rule, mapping in (await self.session.execute(stmt)).all():
            key = f"{dep.kind}:{dep.target_ref}"
            entry = grouped.setdefault(
                key,
                {
                    "kind": str(dep.kind),
                    "target_ref": dep.target_ref,
                    "target_name": dep.target_name,
                    "rules": {},
                    "techniques": set(),
                },
            )
            entry["rules"][str(rule.id)] = {
                "rule_id": str(rule.id),
                "qradar_id": rule.qradar_id,
                "name": rule.name,
                "enabled": rule.enabled,
                "health_status": str(rule.health_status),
                "dependency_source": str(dep.source),
                "dependency_confidence": dep.confidence,
            }
            if mapping is not None:
                entry["techniques"].add(mapping.technique_id)

        return [
            {
                "kind": e["kind"],
                "target_ref": e["target_ref"],
                "target_name": e["target_name"],
                "rules": list(e["rules"].values()),
                "techniques": sorted(e["techniques"]),
            }
            for e in grouped.values()
        ]

    async def mapping_provenance_summary(
        self, instance_id: uuid.UUID | None = None
    ) -> dict[str, int]:
        """Explicit vs inferred mapping counts — the honesty check on coverage."""
        stmt = select(TechniqueMapping.source, func.count())
        if instance_id is not None:
            stmt = stmt.where(TechniqueMapping.instance_id == instance_id)
        rows = (await self.session.execute(stmt.group_by(TechniqueMapping.source))).all()
        counts = {str(source): int(count) for source, count in rows}
        return {
            "explicit": counts.get(MappingSource.EXPLICIT, 0),
            "inferred": counts.get(MappingSource.INFERRED, 0),
        }
