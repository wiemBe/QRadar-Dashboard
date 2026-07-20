"""Alert lifecycle service.

Lifecycle: OPEN -> ACKNOWLEDGED -> RESOLVED.

Deduplication is two-layered (see app/alerts/fingerprint.py):
  1. Application layer (primary): look up the open alert by fingerprint; if one
     exists, update it (bump occurrence_count, refresh evidence, enqueue no
     duplicate notification) instead of opening a new alert.
  2. Database (final safeguard): the partial unique index on
     alert(dedup_key) WHERE status <> 'RESOLVED' catches the concurrent-worker
     race; the service handles the IntegrityError by reloading the winner.

A notification is enqueued exactly once per (alert, transition, channel) — the
notification router owns delivery; this service only records the intent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.enums import AlertStatus, AlertTransition, Severity


@dataclass
class AlertInput:
    fingerprint: str
    title: str
    severity: Severity
    source_type: str
    source_id: uuid.UUID | None
    description: str | None = None
    evidence: dict | None = None
    source_anomaly_ids: list[str] | None = None
    context: dict | None = None


@dataclass
class AlertResult:
    alert: Alert
    opened: bool          # a new alert was created
    updated: bool         # an existing open alert was refreshed
    transition: AlertTransition | None


class AlertService:
    def __init__(self, session: AsyncSession, *, clock=None) -> None:
        self.session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    async def open_or_update(self, data: AlertInput) -> AlertResult:
        """Open a new alert or update the existing open one for this condition."""
        now = self._clock()
        existing = await self._find_active(data.fingerprint)

        if existing is not None:
            existing.occurrence_count += 1
            existing.last_seen_at = now
            existing.severity = data.severity
            if data.evidence is not None:
                existing.evidence_snapshot = data.evidence
            if data.source_anomaly_ids:
                merged = set(existing.source_anomaly_ids) | set(data.source_anomaly_ids)
                existing.source_anomaly_ids = sorted(merged)
            await self.session.flush()
            # No transition -> no notification. This is the dedup guarantee.
            return AlertResult(alert=existing, opened=False, updated=True, transition=None)

        alert = Alert(
            dedup_key=data.fingerprint,
            fingerprint=data.fingerprint,
            title=data.title,
            description=data.description,
            severity=data.severity,
            status=AlertStatus.OPEN,
            source_type=data.source_type,
            source_id=data.source_id,
            context=data.context or {},
            opened_at=now,
            first_seen_at=now,
            last_seen_at=now,
            occurrence_count=1,
            evidence_snapshot=data.evidence or {},
            source_anomaly_ids=data.source_anomaly_ids or [],
        )
        self.session.add(alert)
        try:
            await self.session.flush()
        except IntegrityError:
            # Concurrent worker won the race; reload and treat as update.
            await self.session.rollback()
            existing = await self._find_active(data.fingerprint)
            if existing is None:
                raise
            existing.occurrence_count += 1
            existing.last_seen_at = self._clock()
            await self.session.flush()
            return AlertResult(alert=existing, opened=False, updated=True, transition=None)

        return AlertResult(alert=alert, opened=True, updated=False,
                           transition=AlertTransition.OPENED)

    async def acknowledge(self, alert_id: uuid.UUID, *, actor: str) -> AlertResult:
        alert = await self.session.get(Alert, alert_id)
        if alert is None:
            raise ValueError("alert not found")
        if alert.status != AlertStatus.OPEN:
            # Idempotent / invalid transition: only OPEN -> ACKNOWLEDGED.
            return AlertResult(alert=alert, opened=False, updated=False, transition=None)
        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_at = self._clock()
        alert.acknowledged_by = actor
        await self.session.flush()
        return AlertResult(alert=alert, opened=False, updated=True,
                           transition=AlertTransition.ACKNOWLEDGED)

    async def resolve(
        self, alert_id: uuid.UUID, *, actor: str, reason: str | None = None
    ) -> AlertResult:
        alert = await self.session.get(Alert, alert_id)
        if alert is None:
            raise ValueError("alert not found")
        if alert.status == AlertStatus.RESOLVED:
            return AlertResult(alert=alert, opened=False, updated=False, transition=None)
        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = self._clock()
        alert.resolved_by = actor
        alert.resolution_reason = reason
        await self.session.flush()
        return AlertResult(alert=alert, opened=False, updated=True,
                           transition=AlertTransition.RESOLVED)

    async def resolve_by_fingerprint(
        self, fingerprint: str, *, actor: str, reason: str | None = None
    ) -> AlertResult | None:
        alert = await self._find_active(fingerprint)
        if alert is None:
            return None
        return await self.resolve(alert.id, actor=actor, reason=reason)

    async def _find_active(self, fingerprint: str) -> Alert | None:
        return await self.session.scalar(
            select(Alert).where(
                Alert.dedup_key == fingerprint,
                Alert.status != AlertStatus.RESOLVED,
            )
        )
