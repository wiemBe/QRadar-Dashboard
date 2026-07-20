"""Analytics rule and rule-health queries.

Rule lists always eager-load dependencies via a single `selectinload`, because
the rule list page renders each rule's dependency state — lazily loading it per
row is the classic N+1 that turns a 300-rule inventory into 301 queries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import RuleHealthStatus
from app.models.rule import AnalyticsRule, RuleHealthSnapshot, RuleStateTransition

SORTABLE = {
    "name": AnalyticsRule.name,
    "last_fired_at": AnalyticsRule.last_fired_at,
    "health_status": AnalyticsRule.health_status,
    "qradar_id": AnalyticsRule.qradar_id,
    "offense_contribution_count": AnalyticsRule.offense_contribution_count,
    "event_contribution_count": AnalyticsRule.event_contribution_count,
}


@dataclass
class RuleFilters:
    instance_id: uuid.UUID | None = None
    enabled: bool | None = None
    health_status: RuleHealthStatus | None = None
    is_building_block: bool | None = False
    origin: str | None = None
    owner: str | None = None
    technique: str | None = None
    search: str | None = None
    sort: str = "name"
    descending: bool = False


class RuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _apply(self, stmt: Select, f: RuleFilters) -> Select:
        if f.instance_id is not None:
            stmt = stmt.where(AnalyticsRule.instance_id == f.instance_id)
        if f.enabled is not None:
            stmt = stmt.where(AnalyticsRule.enabled.is_(f.enabled))
        if f.health_status is not None:
            stmt = stmt.where(AnalyticsRule.health_status == f.health_status)
        if f.is_building_block is not None:
            stmt = stmt.where(AnalyticsRule.is_building_block.is_(f.is_building_block))
        if f.origin:
            stmt = stmt.where(AnalyticsRule.origin == f.origin)
        if f.owner:
            stmt = stmt.where(AnalyticsRule.owner == f.owner)
        if f.technique:
            stmt = stmt.where(AnalyticsRule.mitre_techniques.contains([f.technique]))
        if f.search:
            pattern = f"%{f.search}%"
            stmt = stmt.where(
                or_(
                    AnalyticsRule.name.ilike(pattern),
                    AnalyticsRule.description.ilike(pattern),
                )
            )
        return stmt

    async def list_rules(
        self, filters: RuleFilters, *, limit: int, offset: int
    ) -> tuple[list[AnalyticsRule], int]:
        total = int(
            await self.session.scalar(
                self._apply(select(func.count(AnalyticsRule.id)), filters)
            )
            or 0
        )
        sort_col = SORTABLE.get(filters.sort, AnalyticsRule.name)
        order = desc(sort_col) if filters.descending else sort_col.asc()
        stmt = (
            self._apply(select(AnalyticsRule), filters)
            .options(selectinload(AnalyticsRule.dependencies))
            # Stable tiebreak so pagination cannot repeat or skip a rule.
            .order_by(order, AnalyticsRule.qradar_id.asc())
            .limit(limit)
            .offset(offset)
        )
        rules = list((await self.session.scalars(stmt)).unique().all())
        return rules, total

    async def get(self, rule_id: uuid.UUID) -> AnalyticsRule | None:
        return await self.session.scalar(
            select(AnalyticsRule)
            .where(AnalyticsRule.id == rule_id)
            .options(selectinload(AnalyticsRule.dependencies))
        )

    async def health_history(
        self, rule_id: uuid.UUID, *, limit: int, since: datetime | None = None
    ) -> list[RuleHealthSnapshot]:
        stmt = select(RuleHealthSnapshot).where(RuleHealthSnapshot.rule_id == rule_id)
        if since is not None:
            stmt = stmt.where(RuleHealthSnapshot.evaluated_at >= since)
        stmt = stmt.order_by(RuleHealthSnapshot.evaluated_at.desc()).limit(limit)
        return list((await self.session.scalars(stmt)).all())

    async def transitions(
        self, rule_id: uuid.UUID, *, limit: int
    ) -> list[RuleStateTransition]:
        return list(
            (
                await self.session.scalars(
                    select(RuleStateTransition)
                    .where(RuleStateTransition.rule_id == rule_id)
                    .order_by(RuleStateTransition.observed_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def health_summary(
        self, instance_id: uuid.UUID | None = None
    ) -> list[dict[str, Any]]:
        stmt = select(AnalyticsRule.health_status, func.count()).where(
            AnalyticsRule.is_building_block.is_(False)
        )
        if instance_id is not None:
            stmt = stmt.where(AnalyticsRule.instance_id == instance_id)
        rows = (
            await self.session.execute(
                stmt.group_by(AnalyticsRule.health_status).order_by(func.count().desc())
            )
        ).all()
        return [{"status": str(status), "count": int(count)} for status, count in rows]
