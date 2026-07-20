"""Offense queries.

Everything reads from `offense_snapshot`, which is append-on-change history
rather than a mutable mirror. "The current state of every offense" is therefore
"the latest snapshot per offense", expressed as a single `DISTINCT ON` query
rather than a per-offense lookup — the N+1 shape this deliberately avoids.

All filters and sorts are restricted to an approved column set. Nothing here
interpolates caller input into SQL, and the sort field is resolved through a
lookup table so an unknown value cannot reach the query builder.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, desc, func, or_, select
from sqlalchemy import true as sa_true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import NamedFromClause, Subquery

from app.models.offense import OffenseSnapshot

#: Columns a caller may sort by. Anything else is rejected before it reaches
#: SQL, so sort input can never become an injection vector.
SORTABLE = {
    "last_updated_time": OffenseSnapshot.last_updated_time,
    "start_time": OffenseSnapshot.start_time,
    "magnitude": OffenseSnapshot.magnitude,
    "severity": OffenseSnapshot.severity,
    "age_seconds": OffenseSnapshot.age_seconds,
    "event_count": OffenseSnapshot.event_count,
    "qradar_offense_id": OffenseSnapshot.qradar_offense_id,
}

#: Columns free-text search may touch. Descriptions and usernames only — never
#: an unbounded scan across every column.
SEARCHABLE = (
    OffenseSnapshot.description,
    OffenseSnapshot.offense_source,
    OffenseSnapshot.assigned_to,
)


@dataclass
class OffenseFilters:
    status: str | None = None
    min_magnitude: int | None = None
    max_magnitude: int | None = None
    min_severity: int | None = None
    assigned: bool | None = None
    assigned_to: str | None = None
    category: str | None = None
    instance_id: uuid.UUID | None = None
    min_age_seconds: int | None = None
    max_age_seconds: int | None = None
    search: str | None = None
    sort: str = "last_updated_time"
    descending: bool = True


class OffenseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------ base view
    def _latest_subquery(self, instance_id: uuid.UUID | None = None) -> Subquery:
        """One row per offense: its most recent snapshot.

        `DISTINCT ON` with a matching ORDER BY is the cheapest way to express
        "latest per group" in PostgreSQL and uses the
        (instance_id, qradar_offense_id, captured_at) index directly.
        """
        stmt = select(OffenseSnapshot).distinct(
            OffenseSnapshot.instance_id, OffenseSnapshot.qradar_offense_id
        )
        if instance_id is not None:
            stmt = stmt.where(OffenseSnapshot.instance_id == instance_id)
        return stmt.order_by(
            OffenseSnapshot.instance_id,
            OffenseSnapshot.qradar_offense_id,
            OffenseSnapshot.captured_at.desc(),
        ).subquery()

    def _apply_filters(
        self, stmt: Select, f: OffenseFilters, model: _AliasedOffense
    ) -> Select:
        if f.status:
            stmt = stmt.where(model.status == f.status)
        if f.min_magnitude is not None:
            stmt = stmt.where(model.magnitude >= f.min_magnitude)
        if f.max_magnitude is not None:
            stmt = stmt.where(model.magnitude <= f.max_magnitude)
        if f.min_severity is not None:
            stmt = stmt.where(model.severity >= f.min_severity)
        if f.assigned is not None:
            stmt = stmt.where(model.is_assigned.is_(f.assigned))
        if f.assigned_to:
            stmt = stmt.where(model.assigned_to == f.assigned_to)
        if f.category:
            # JSONB containment; uses the column's default GIN-able operator.
            stmt = stmt.where(model.categories.contains([f.category]))
        if f.min_age_seconds is not None:
            stmt = stmt.where(model.age_seconds >= f.min_age_seconds)
        if f.max_age_seconds is not None:
            stmt = stmt.where(model.age_seconds <= f.max_age_seconds)
        if f.search:
            # Bound parameters throughout; the wildcards are ours, the value is
            # never concatenated into the statement.
            pattern = f"%{f.search}%"
            stmt = stmt.where(
                or_(*[col.ilike(pattern) for col in _searchable_of(model)])
            )
        return stmt

    async def list_offenses(
        self, filters: OffenseFilters, *, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of current offense state, plus the unpaged total.

        Returns plain mappings rather than ORM instances: these rows come from a
        DISTINCT ON subquery, not from an identity-mapped entity, and handing
        back detached objects that look persistent invites accidental writes.
        """
        sub = self._latest_subquery(filters.instance_id).alias("latest")
        model = _AliasedOffense(sub)

        count_stmt = select(func.count()).select_from(
            self._apply_filters(select(sub.c.qradar_offense_id), filters, model).subquery()
        )
        total = int(await self.session.scalar(count_stmt) or 0)

        sort_col = _sortable_aliased(sub).get(filters.sort, sub.c.last_updated_time)
        order = desc(sort_col) if filters.descending else sort_col.asc()
        stmt = self._apply_filters(select(sub), filters, model)
        # Deterministic tiebreak: two offenses updated in the same millisecond
        # must not swap places between pages.
        stmt = stmt.order_by(order, sub.c.qradar_offense_id.desc()).limit(limit).offset(offset)

        rows = (await self.session.execute(stmt)).mappings().all()
        return [dict(r) for r in rows], total

    async def get_latest(
        self, instance_id: uuid.UUID, qradar_offense_id: int
    ) -> OffenseSnapshot | None:
        return await self.session.scalar(
            select(OffenseSnapshot)
            .where(
                OffenseSnapshot.instance_id == instance_id,
                OffenseSnapshot.qradar_offense_id == qradar_offense_id,
            )
            .order_by(OffenseSnapshot.captured_at.desc())
            .limit(1)
        )

    async def history(
        self,
        instance_id: uuid.UUID,
        qradar_offense_id: int,
        *,
        limit: int,
        since: datetime | None = None,
    ) -> list[OffenseSnapshot]:
        stmt = select(OffenseSnapshot).where(
            OffenseSnapshot.instance_id == instance_id,
            OffenseSnapshot.qradar_offense_id == qradar_offense_id,
        )
        if since is not None:
            stmt = stmt.where(OffenseSnapshot.captured_at >= since)
        stmt = stmt.order_by(OffenseSnapshot.captured_at.desc()).limit(limit)
        return list((await self.session.scalars(stmt)).all())

    # ----------------------------------------------------------- aggregates
    async def aggregate_metrics(
        self, instance_id: uuid.UUID | None, *, sla_hours: int, critical_magnitude: int
    ) -> dict[str, Any]:
        sub = self._latest_subquery(instance_id).alias("latest")
        open_only = sub.c.status == "OPEN"
        sla_seconds = sla_hours * 3600

        row = (
            await self.session.execute(
                select(
                    func.count().filter(open_only),
                    func.count().filter(
                        and_(open_only, sub.c.magnitude >= critical_magnitude)
                    ),
                    func.count().filter(and_(open_only, sub.c.is_assigned.is_(False))),
                    func.count().filter(
                        and_(open_only, sub.c.age_seconds >= sla_seconds)
                    ),
                    func.max(sub.c.age_seconds).filter(open_only),
                    func.avg(sub.c.magnitude).filter(open_only),
                ).select_from(sub)
            )
        ).one()

        return {
            "active": int(row[0] or 0),
            "critical": int(row[1] or 0),
            "unassigned": int(row[2] or 0),
            "exceeding_sla": int(row[3] or 0),
            "oldest_age_seconds": int(row[4]) if row[4] is not None else None,
            "average_magnitude": round(float(row[5]), 2) if row[5] is not None else None,
        }

    async def distribution(
        self, instance_id: uuid.UUID | None, column: str, *, open_only: bool = True
    ) -> list[dict[str, Any]]:
        """Grouped counts over one approved column."""
        sub = self._latest_subquery(instance_id).alias("latest")
        allowed = {
            "status": sub.c.status,
            "magnitude": sub.c.magnitude,
            "assigned_to": sub.c.assigned_to,
            "offense_type_name": sub.c.offense_type_name,
        }
        col = allowed.get(column)
        if col is None:
            raise ValueError(f"unsupported distribution column: {column}")

        stmt = select(col, func.count()).select_from(sub).group_by(col)
        if open_only:
            stmt = stmt.where(sub.c.status == "OPEN")
        stmt = stmt.order_by(func.count().desc())
        rows = (await self.session.execute(stmt)).all()
        return [{"key": (k if k is not None else "unassigned"), "count": int(c)} for k, c in rows]

    async def aging_buckets(
        self, instance_id: uuid.UUID | None, *, boundaries_hours: tuple[int, ...]
    ) -> list[dict[str, Any]]:
        """Open-offense counts per age bucket, computed in one pass."""
        sub = self._latest_subquery(instance_id).alias("latest")
        open_only = sub.c.status == "OPEN"

        selects = []
        labels: list[str] = []
        previous = 0
        for hours in boundaries_hours:
            selects.append(
                func.count().filter(
                    and_(
                        open_only,
                        sub.c.age_seconds >= previous * 3600,
                        sub.c.age_seconds < hours * 3600,
                    )
                )
            )
            labels.append(f"{previous}-{hours}h")
            previous = hours
        selects.append(
            func.count().filter(and_(open_only, sub.c.age_seconds >= previous * 3600))
        )
        labels.append(f">{previous}h")

        row = (await self.session.execute(select(*selects).select_from(sub))).one()
        return [
            {"bucket": label, "count": int(value or 0)}
            for label, value in zip(labels, row, strict=True)
        ]

    async def top_entities(
        self, instance_id: uuid.UUID | None, field_name: str, *, limit: int
    ) -> list[dict[str, Any]]:
        """Most frequent values inside a JSONB array column, across open offenses.

        `jsonb_array_elements_text` unnests server-side so the whole set is
        aggregated in the database rather than pulled into Python.
        """
        sub = self._latest_subquery(instance_id).alias("latest")
        allowed = {
            "rule_ids": sub.c.rule_ids,
            "usernames": sub.c.usernames,
            "source_addresses": sub.c.source_addresses,
            "local_destination_addresses": sub.c.local_destination_addresses,
            "categories": sub.c.categories,
            "log_source_ids": sub.c.log_source_ids,
        }
        col = allowed.get(field_name)
        if col is None:
            raise ValueError(f"unsupported entity field: {field_name}")

        element = func.jsonb_array_elements_text(col).table_valued("value").render_derived()
        stmt = (
            select(element.c.value, func.count())
            .select_from(sub.join(element, sa_true()))
            .where(sub.c.status == "OPEN")
            .group_by(element.c.value)
            .order_by(func.count().desc(), element.c.value)
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [{"key": key, "count": int(count)} for key, count in rows]

    async def magnitude_trend(
        self, instance_id: uuid.UUID | None, *, days: int, bucket_hours: int = 24
    ) -> list[dict[str, Any]]:
        """Average and peak magnitude of captured snapshots over time."""
        since = datetime.now(UTC) - timedelta(days=days)
        bucket = func.date_trunc(
            "hour" if bucket_hours < 24 else "day", OffenseSnapshot.captured_at
        )
        stmt = (
            select(
                bucket.label("bucket"),
                func.avg(OffenseSnapshot.magnitude),
                func.max(OffenseSnapshot.magnitude),
                func.count(),
            )
            .where(OffenseSnapshot.captured_at >= since)
            .group_by(bucket)
            .order_by(bucket)
        )
        if instance_id is not None:
            stmt = stmt.where(OffenseSnapshot.instance_id == instance_id)

        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "bucket_start": b,
                "average_magnitude": round(float(avg), 2) if avg is not None else None,
                "peak_magnitude": int(peak) if peak is not None else None,
                "snapshot_count": int(count),
            }
            for b, avg, peak, count in rows
        ]


# The latest-snapshot subquery is used as an alias in most queries, so filters
# need to resolve against the alias's columns rather than the ORM class.
class _AliasedOffense:
    def __init__(self, sub: NamedFromClause) -> None:
        self._c = sub.c

    def __getattr__(self, item: str) -> Any:
        return getattr(self._c, item)


def _searchable_of(model: _AliasedOffense) -> list:
    return [model.description, model.offense_source, model.assigned_to]


def _sortable_aliased(sub: NamedFromClause) -> dict:
    return {
        "last_updated_time": sub.c.last_updated_time,
        "start_time": sub.c.start_time,
        "magnitude": sub.c.magnitude,
        "severity": sub.c.severity,
        "age_seconds": sub.c.age_seconds,
        "event_count": sub.c.event_count,
        "qradar_offense_id": sub.c.qradar_offense_id,
    }
