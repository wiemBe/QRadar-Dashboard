"""API schemas for anomalies, alerts and notifications."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AlertStatus,
    AnomalyType,
    NotificationChannel,
    NotificationStatus,
    Severity,
)


class AnomalyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    log_source_id: uuid.UUID
    anomaly_type: AnomalyType
    severity: Severity
    detected_at: datetime
    resolved_at: datetime | None
    observed_value: float | None
    expected_value: float | None
    deviation_score: float | None
    explanation: str | None
    details: dict
    suppressed: bool


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fingerprint: str
    title: str
    description: str | None
    severity: Severity
    status: AlertStatus
    source_type: str
    source_id: uuid.UUID | None
    opened_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime | None
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_reason: str | None
    occurrence_count: int
    evidence_snapshot: dict
    source_anomaly_ids: list[str]


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    alert_id: uuid.UUID
    channel: NotificationChannel
    target: str
    transition: str
    status: NotificationStatus
    attempts: int
    max_attempts: int
    next_attempt_at: datetime | None
    sent_at: datetime | None
    error_message: str | None


class AlertActionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)
