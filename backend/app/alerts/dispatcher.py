"""Notification dispatch: enqueue on transition, then deliver with retry.

Delivery model:
  * `enqueue(alert, transition)` creates one AlertNotification per routed
    (channel, target). The unique constraint on (alert, transition, channel,
    target) means a repeated transition cannot enqueue a duplicate — the second
    insert is caught and skipped. Resolve notifications are only enqueued when
    recovery notifications are enabled.
  * `dispatch_due(...)` picks PENDING/FAILED rows whose next_attempt_at has
    passed, sends them via the channel's Notifier, and records the outcome:
      - success            -> SENT (delivery audit trail: sent_at, attempts)
      - retryable failure  -> FAILED, next_attempt_at = now + backoff(jitter)
      - permanent failure
        or retries exhausted -> DEAD_LETTER (parked for inspection)

Clock, sleep and RNG are injectable; the notifier map is injected so tests use
MockNotifiers and no real notification is ever sent under test.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.notifiers.base import NotificationMessage, Notifier, NotifierError
from app.alerts.routing import RoutingPolicy
from app.core.config import Settings, get_settings
from app.models.alert import Alert, AlertNotification
from app.models.enums import AlertTransition, NotificationChannel, NotificationStatus
from app.security.redaction import redact
from app.services.backoff import backoff_delay


class AlertEnqueuer(Protocol):
    """What the detection engines need from a dispatcher: record the intent to
    notify. Structural, so a test double needs no inheritance and the engines
    never depend on delivery."""

    async def enqueue(
        self, alert: Alert, transition: AlertTransition
    ) -> list[AlertNotification]: ...


class NotificationDispatcher:
    def __init__(
        self,
        session: AsyncSession,
        notifiers: dict[NotificationChannel, Notifier],
        policy: RoutingPolicy,
        *,
        settings: Settings | None = None,
        clock=None,
        rng: random.Random | None = None,
    ) -> None:
        self.session = session
        self.notifiers = notifiers
        self.policy = policy
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not crypto

    async def enqueue(self, alert: Alert, transition: AlertTransition) -> list[AlertNotification]:
        if transition is AlertTransition.RESOLVED and not self.settings.notify_send_recovery:
            return []

        created: list[AlertNotification] = []
        for route in self.policy.resolve(alert):
            payload = self._render(alert, transition)
            notif = AlertNotification(
                alert_id=alert.id,
                channel=route.channel,
                target=route.target,
                transition=transition,
                status=NotificationStatus.PENDING,
                max_attempts=self.settings.notify_max_retries + 1,
                next_attempt_at=self._clock(),
                payload=redact(payload),
            )
            self.session.add(notif)
            try:
                await self.session.flush()
            except IntegrityError:
                # Duplicate (alert, transition, channel, target): already queued.
                await self.session.rollback()
                continue
            created.append(notif)
        return created

    async def dispatch_due(self, *, limit: int = 100) -> dict[str, int]:
        now = self._clock()
        due = list(
            (
                await self.session.scalars(
                    select(AlertNotification)
                    .where(
                        AlertNotification.status.in_(
                            [NotificationStatus.PENDING, NotificationStatus.FAILED]
                        ),
                        or_(
                            AlertNotification.next_attempt_at.is_(None),
                            AlertNotification.next_attempt_at <= now,
                        ),
                    )
                    .order_by(AlertNotification.next_attempt_at)
                    .limit(limit)
                )
            ).all()
        )

        stats = {"sent": 0, "failed": 0, "dead_letter": 0}
        for notif in due:
            await self._attempt(notif, stats)
        await self.session.flush()
        return stats

    async def _attempt(self, notif: AlertNotification, stats: dict[str, int]) -> None:
        notifier = self.notifiers.get(notif.channel)
        notif.attempts += 1
        if notifier is None:
            notif.status = NotificationStatus.DEAD_LETTER
            notif.error_message = f"no notifier registered for {notif.channel}"
            stats["dead_letter"] += 1
            return

        message = self._message_from_payload(notif)
        try:
            await notifier.send(notif.target, message)
        except NotifierError as exc:
            self._handle_failure(notif, exc, stats)
            return
        except Exception as exc:
            self._handle_failure(notif, NotifierError(str(exc)), stats)
            return

        notif.status = NotificationStatus.SENT
        notif.sent_at = self._clock()
        notif.error_message = None
        stats["sent"] += 1

    def _handle_failure(
        self, notif: AlertNotification, exc: NotifierError, stats: dict[str, int]
    ) -> None:
        notif.error_message = str(exc)[:2000]
        exhausted = notif.attempts >= notif.max_attempts
        if exc.permanent or exhausted:
            notif.status = NotificationStatus.DEAD_LETTER
            notif.next_attempt_at = None
            stats["dead_letter"] += 1
            return
        delay = backoff_delay(
            notif.attempts,
            base_seconds=self.settings.notify_retry_base_seconds,
            max_seconds=self.settings.notify_retry_max_seconds,
            rng=self._rng,
        )
        notif.status = NotificationStatus.FAILED
        notif.next_attempt_at = self._clock() + timedelta(seconds=delay)
        stats["failed"] += 1

    def _render(self, alert: Alert, transition: AlertTransition) -> dict:
        return {
            "title": alert.title,
            "body": alert.description or alert.title,
            "severity": alert.severity.value,
            "transition": transition.value,
            "alert_id": str(alert.id),
            "fingerprint": alert.fingerprint,
            "fields": {
                "occurrences": alert.occurrence_count,
                "first_seen": alert.first_seen_at.isoformat() if alert.first_seen_at else None,
                **{k: v for k, v in (alert.evidence_snapshot or {}).items()
                   if k in ("observed_value", "expected_value", "deviation_score", "reason")},
            },
        }

    def _message_from_payload(self, notif: AlertNotification) -> NotificationMessage:
        from app.models.enums import Severity

        p = notif.payload or {}
        return NotificationMessage(
            title=p.get("title", "alert"),
            body=p.get("body", ""),
            severity=Severity(p.get("severity", "MEDIUM")),
            transition=AlertTransition(p.get("transition", notif.transition.value)),
            alert_id=p.get("alert_id", str(notif.alert_id)),
            fingerprint=p.get("fingerprint", ""),
            fields=p.get("fields", {}),
        )
