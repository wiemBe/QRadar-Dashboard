"""API schemas for log sources and the SOC overview.

Response models are separate from ORM models on purpose: they never expose
internal ids-as-secrets, they carry sanitized text, and their shape is a UI
contract that can evolve independently of the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AnomalyType, Criticality, Severity


class HealthBreakdown(BaseModel):
    score: float
    freshness: float
    volume: float
    parsing: float
    collection: float


class LogSourceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    qradar_id: int
    name: str
    type_name: str | None
    criticality: Criticality
    owner: str | None
    enabled: bool
    monitoring_enabled: bool
    maintenance_mode: bool
    qradar_status: str | None
    last_event_time: datetime | None
    health_score: float | None
    open_anomaly_count: int = 0


class LogSourceDetail(LogSourceSummary):
    description: str | None
    type_id: int | None
    protocol_type_id: int | None
    credibility: int | None
    owner_email: str | None
    expected_interval_seconds: int | None
    business_hours_only: bool
    business_hours_start: int
    business_hours_end: int
    business_days: list[int]
    timezone_name: str
    custom_thresholds: dict
    health_breakdown: HealthBreakdown | None = None
    health_computed_at: datetime | None = None


class LogSourceUpdate(BaseModel):
    """Operator-editable SOC metadata. QRadar-mirrored fields are read-only."""

    criticality: Criticality | None = None
    owner: str | None = Field(default=None, max_length=255)
    owner_email: str | None = Field(default=None, max_length=255)
    monitoring_enabled: bool | None = None
    maintenance_mode: bool | None = None
    maintenance_until: datetime | None = None
    maintenance_reason: str | None = Field(default=None, max_length=1000)
    expected_interval_seconds: int | None = Field(default=None, ge=1)
    business_hours_only: bool | None = None
    business_hours_start: int | None = Field(default=None, ge=0, le=23)
    business_hours_end: int | None = Field(default=None, ge=0, le=23)
    business_days: list[int] | None = None
    timezone_name: str | None = Field(default=None, max_length=64)
    custom_thresholds: dict | None = None


class AnomalySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    anomaly_type: AnomalyType
    severity: Severity
    detected_at: datetime
    resolved_at: datetime | None
    explanation: str | None
    suppressed: bool


class SyncResult(BaseModel):
    provider: str
    log_sources_seen: int
    created: int
    updated: int
    instance_version: str | None = None


# --- SOC overview ----------------------------------------------------------
class OverviewCounts(BaseModel):
    total_log_sources: int
    monitored_log_sources: int
    healthy_log_sources: int
    silent_log_sources: int
    anomalous_log_sources: int
    in_maintenance: int


class OverviewOffenses(BaseModel):
    active: int
    critical: int
    unassigned: int
    oldest_age_seconds: int | None = None


class OverviewAlerts(BaseModel):
    open: int
    acknowledged: int
    by_severity: dict[str, int] = Field(default_factory=dict)


class SocOverview(BaseModel):
    instance_status: str
    instance_version: str | None
    generated_at: datetime
    log_sources: OverviewCounts
    offenses: OverviewOffenses
    alerts: OverviewAlerts
    average_health_score: float | None
