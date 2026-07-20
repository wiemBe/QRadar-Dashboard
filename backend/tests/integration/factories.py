"""Helpers for integration tests: object factories and a controllable clock."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models.enums import Criticality, Severity, VisualizationType
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceMetric
from app.models.search import ScheduledSearch


class FakeClock:
    """A clock that advances by `step` on every call, plus manual `advance`.

    Used to make timeout/polling deterministic without real sleeping.
    """

    def __init__(self, start: datetime, step_seconds: float = 0.0) -> None:
        self._now = start
        self._step = timedelta(seconds=step_seconds)

    def __call__(self) -> datetime:
        value = self._now
        self._now = self._now + self._step
        return value

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


async def make_instance(session) -> QRadarInstance:
    inst = QRadarInstance(name=f"inst-{uuid.uuid4().hex[:8]}", console_host="mock",
                          provider_kind="mock")
    session.add(inst)
    await session.flush()
    return inst


async def make_log_source(
    session,
    instance: QRadarInstance,
    *,
    qradar_id: int = 1001,
    name: str = "src",
    criticality: Criticality = Criticality.MEDIUM,
    **kwargs,
) -> LogSource:
    ls = LogSource(
        instance_id=instance.id, qradar_id=qradar_id, name=name,
        criticality=criticality, **kwargs,
    )
    session.add(ls)
    await session.flush()
    return ls


async def add_metric(
    session, log_source: LogSource, bucket_start: datetime, **kwargs
) -> LogSourceMetric:
    defaults = dict(
        bucket_seconds=300, event_count=15000, average_eps=50.0, peak_eps=75.0,
        last_event_at=bucket_start, event_delay_seconds=5.0,
        unknown_event_count=100, stored_event_count=15000,
        parsed_username_ratio=0.9, parsed_source_ip_ratio=0.95,
        distinct_qid_count=20, distinct_username_count=40, distinct_source_ip_count=60,
        collection_error_count=0, payload_signature="sig",
    )
    defaults.update(kwargs)
    m = LogSourceMetric(log_source_id=log_source.id, bucket_start=bucket_start, **defaults)
    session.add(m)
    await session.flush()
    return m


async def make_search(
    session,
    *,
    name: str = "test-search",
    aql: str = "SELECT sourceip, COUNT(*) FROM events GROUP BY sourceip LAST 1 HOURS",
    threshold_value: float | None = None,
    timeout_seconds: int = 300,
    schedule_cron: str = "*/5 * * * *",
    **kwargs,
) -> ScheduledSearch:
    defaults = dict(
        query_version=1, severity=Severity.MEDIUM, max_time_range_hours=24,
        max_result_rows=1000, visualization_type=VisualizationType.TABLE, enabled=True,
    )
    defaults.update(kwargs)
    s = ScheduledSearch(
        name=name, aql_query=aql, schedule_cron=schedule_cron,
        threshold_value=threshold_value, timeout_seconds=timeout_seconds, **defaults,
    )
    session.add(s)
    await session.flush()
    return s


def utc(y=2026, mo=7, d=15, h=10, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)
