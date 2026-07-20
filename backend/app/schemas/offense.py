"""Offense API schemas.

Presentation shapes, deliberately distinct from both the ORM model and the
provider DTO. Notably absent: any raw QRadar payload. Text fields are sanitized
at collection time and re-sanitized here, because a snapshot written by an older
build predates the current sanitizer and the API is the last line before the DOM.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.security.sanitizer import sanitize_text


class _Sanitized(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("*", mode="after")
    @classmethod
    def _strip_markup(cls, value: object) -> object:
        if isinstance(value, str):
            return sanitize_text(value)
        if isinstance(value, list):
            return [sanitize_text(v) if isinstance(v, str) else v for v in value]
        return value


class OffenseSummary(_Sanitized):
    instance_id: uuid.UUID
    qradar_offense_id: int
    captured_at: datetime
    description: str | None = None
    status: str | None = None
    magnitude: int | None = None
    severity: int | None = None
    credibility: int | None = None
    relevance: int | None = None
    assigned_to: str | None = None
    is_assigned: bool = False
    offense_type_name: str | None = None
    offense_source: str | None = None
    source_network: str | None = None
    event_count: int | None = None
    flow_count: int | None = None
    source_count: int | None = None
    destination_count: int | None = None
    start_time: datetime | None = None
    last_updated_time: datetime | None = None
    close_time: datetime | None = None
    age_seconds: int | None = None
    categories: list[str] = Field(default_factory=list)


class OffenseDetail(OffenseSummary):
    offense_type: int | None = None
    device_count: int | None = None
    closing_reason_id: int | None = None
    closing_reason: str | None = None
    source_addresses: list[str] = Field(default_factory=list)
    local_destination_addresses: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    log_source_ids: list[int] = Field(default_factory=list)
    rule_ids: list[int] = Field(default_factory=list)


class OffenseHistoryEntry(_Sanitized):
    captured_at: datetime
    status: str | None = None
    magnitude: int | None = None
    severity: int | None = None
    assigned_to: str | None = None
    is_assigned: bool = False
    event_count: int | None = None
    age_seconds: int | None = None
    close_time: datetime | None = None
    closing_reason: str | None = None


class OffensePage(BaseModel):
    """Offset pagination with an explicit total, so the UI can show page counts."""

    items: list[OffenseSummary]
    total: int
    limit: int
    offset: int


class OffenseAggregates(BaseModel):
    active: int
    critical: int
    unassigned: int
    exceeding_sla: int
    oldest_age_seconds: int | None = None
    average_magnitude: float | None = None
    sla_hours: int
    critical_magnitude: int


class CountEntry(BaseModel):
    key: str
    count: int


class AgingBucket(BaseModel):
    bucket: str
    count: int


class MagnitudeTrendPoint(BaseModel):
    bucket_start: datetime
    average_magnitude: float | None = None
    peak_magnitude: int | None = None
    snapshot_count: int


class OffenseAnalytics(BaseModel):
    """Everything the offense analytics page needs, in one round trip."""

    generated_at: datetime
    aggregates: OffenseAggregates
    aging: list[AgingBucket]
    magnitude_trend: list[MagnitudeTrendPoint]
    status_distribution: list[CountEntry]
    magnitude_distribution: list[CountEntry]
    category_distribution: list[CountEntry]
    assignment_distribution: list[CountEntry]
    top_rules: list[CountEntry]
    top_usernames: list[CountEntry]
    top_source_ips: list[CountEntry]
    top_destination_ips: list[CountEntry]
