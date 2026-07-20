"""Scheduled-search lifecycle: create, version, update, list, manual trigger.

Only persisted, validated search definitions can ever be scheduled or executed.
The AQL text is validated at authoring time and stored; every change to the AQL
mints a new immutable SearchQueryVersion, and each execution records which
version it ran. Nothing here accepts ad-hoc AQL from a request for direct
execution — a manual run always executes the stored query of an existing search.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.search import ScheduledSearch, SearchExecution, SearchQueryVersion
from app.schemas.search import ScheduledSearchCreate, ScheduledSearchUpdate


class ScheduledSearchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, payload: ScheduledSearchCreate, *, actor: str | None) -> ScheduledSearch:
        search = ScheduledSearch(
            name=payload.name,
            description=payload.description,
            owner=payload.owner,
            category=payload.category,
            mitre_techniques=payload.mitre_techniques,
            aql_query=payload.aql_query,
            query_version=1,
            schedule_cron=payload.schedule_cron,
            schedule_timezone=payload.schedule_timezone,
            threshold_value=payload.threshold_value,
            threshold_operator=payload.threshold_operator,
            severity=payload.severity,
            timeout_seconds=payload.timeout_seconds,
            max_time_range_hours=payload.max_time_range_hours,
            max_result_rows=payload.max_result_rows,
            visualization_type=payload.visualization_type,
            enabled=payload.enabled,
        )
        self.session.add(search)
        await self.session.flush()
        self._add_version(search, actor=actor, note="initial version")
        await self.session.flush()
        return search

    async def get(self, search_id: uuid.UUID) -> ScheduledSearch | None:
        return await self.session.get(ScheduledSearch, search_id)

    async def list(self) -> list[ScheduledSearch]:
        result = await self.session.scalars(
            select(ScheduledSearch).order_by(ScheduledSearch.name)
        )
        return list(result.all())

    async def versions(self, search_id: uuid.UUID) -> list[SearchQueryVersion]:
        return list(
            (
                await self.session.scalars(
                    select(SearchQueryVersion)
                    .where(SearchQueryVersion.search_id == search_id)
                    .order_by(SearchQueryVersion.version.desc())
                )
            ).all()
        )

    async def update(
        self, search: ScheduledSearch, payload: ScheduledSearchUpdate, *, actor: str | None
    ) -> ScheduledSearch:
        data = payload.model_dump(exclude_unset=True, exclude={"change_note"})
        new_aql = data.pop("aql_query", None)

        for field, value in data.items():
            setattr(search, field, value)

        # An AQL change is a new immutable version; the query string in-place is
        # kept as the "current" pointer, history lives in SearchQueryVersion.
        if new_aql is not None and new_aql != search.aql_query:
            search.aql_query = new_aql
            search.query_version += 1
            self._add_version(search, actor=actor, note=payload.change_note or "aql updated")

        await self.session.flush()
        return search

    async def executions(
        self, search_id: uuid.UUID, *, limit: int = 50
    ) -> list[SearchExecution]:
        return list(
            (
                await self.session.scalars(
                    select(SearchExecution)
                    .where(SearchExecution.search_id == search_id)
                    .order_by(SearchExecution.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    def manual_run_key(self, search: ScheduledSearch, actor: str) -> str:
        """A run key unique to this manual trigger instant, so two manual runs
        are distinct executions but a retry of one reuses its row."""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        return f"manual:{actor}:{stamp}"

    @staticmethod
    def scheduled_run_key(search: ScheduledSearch, scheduled_for: datetime) -> str:
        """Deterministic per-tick key: the same scheduled instant maps to the
        same execution, so a duplicate beat delivery cannot double-run."""
        return f"scheduled:{scheduled_for.strftime('%Y%m%dT%H%M%S')}"

    def _add_version(self, search: ScheduledSearch, *, actor: str | None, note: str | None) -> None:
        self.session.add(
            SearchQueryVersion(
                search_id=search.id,
                version=search.query_version,
                aql_query=search.aql_query,
                changed_by=actor,
                change_note=note,
            )
        )
