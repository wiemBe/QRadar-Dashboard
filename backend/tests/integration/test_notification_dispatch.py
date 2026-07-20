"""Notification dispatch: enqueue dedup, delivery, retry, dead-letter, recovery.
No real notification is sent (MockNotifier). DB-gated."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.base import MockNotifier
from app.alerts.routing import RoutingPolicy, RoutingRule
from app.alerts.service import AlertInput, AlertService
from app.core.config import Settings
from app.models.alert import AlertNotification
from app.models.enums import (
    AlertTransition,
    NotificationChannel,
    NotificationStatus,
    Severity,
)
from tests.integration.factories import FakeClock, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _policy() -> RoutingPolicy:
    return RoutingPolicy(rules=[RoutingRule(channel=NotificationChannel.SLACK, target="hook")])


async def _open_alert(session):
    svc = AlertService(session)
    r = await svc.open_or_update(
        AlertInput(fingerprint=f"anomaly:{uuid.uuid4().hex[:6]}", title="t",
                   severity=Severity.HIGH, source_type="log_source", source_id=uuid.uuid4(),
                   evidence={"observed_value": 3.0})
    )
    return r.alert


async def test_enqueue_creates_one_row_per_route(db_session) -> None:
    alert = await _open_alert(db_session)
    d = NotificationDispatcher(db_session, {}, _policy())
    created = await d.enqueue(alert, AlertTransition.OPENED)
    assert len(created) == 1
    assert created[0].status == NotificationStatus.PENDING


async def test_enqueue_is_deduplicated_per_transition(db_session) -> None:
    alert = await _open_alert(db_session)
    d = NotificationDispatcher(db_session, {}, _policy())
    await d.enqueue(alert, AlertTransition.OPENED)
    await d.enqueue(alert, AlertTransition.OPENED)  # duplicate transition
    count = await db_session.scalar(
        select(func.count()).select_from(AlertNotification).where(
            AlertNotification.alert_id == alert.id
        )
    )
    assert count == 1


async def test_successful_delivery(db_session) -> None:
    alert = await _open_alert(db_session)
    notifier = MockNotifier(NotificationChannel.SLACK)
    d = NotificationDispatcher(db_session, {NotificationChannel.SLACK: notifier}, _policy())
    await d.enqueue(alert, AlertTransition.OPENED)
    stats = await d.dispatch_due()
    assert stats["sent"] == 1
    assert len(notifier.sent) == 1
    row = await db_session.scalar(select(AlertNotification))
    assert row.status == NotificationStatus.SENT
    assert row.sent_at is not None


async def test_retry_then_dead_letter(db_session) -> None:
    alert = await _open_alert(db_session)
    # Always fails; max 2 attempts total -> ends in DEAD_LETTER.
    notifier = MockNotifier(NotificationChannel.SLACK, fail_times=99)
    settings = Settings(encryption_key="x" * 44, notify_max_retries=1,
                        notify_retry_base_seconds=1, notify_retry_max_seconds=2)
    clock = FakeClock(utc(), step_seconds=0)
    d = NotificationDispatcher(
        db_session, {NotificationChannel.SLACK: notifier}, _policy(),
        settings=settings, clock=clock,
    )
    await d.enqueue(alert, AlertTransition.OPENED)

    s1 = await d.dispatch_due()          # attempt 1 fails -> FAILED
    assert s1["failed"] == 1
    row = await db_session.scalar(select(AlertNotification))
    assert row.status == NotificationStatus.FAILED
    assert row.next_attempt_at is not None

    clock.advance(3600)                  # move past backoff
    s2 = await d.dispatch_due()          # attempt 2 exhausts retries -> DEAD_LETTER
    assert s2["dead_letter"] == 1
    row = await db_session.scalar(select(AlertNotification))
    assert row.status == NotificationStatus.DEAD_LETTER


async def test_permanent_failure_dead_letters_immediately(db_session) -> None:
    alert = await _open_alert(db_session)
    notifier = MockNotifier(NotificationChannel.SLACK, fail_times=99, permanent=True)
    d = NotificationDispatcher(db_session, {NotificationChannel.SLACK: notifier}, _policy())
    await d.enqueue(alert, AlertTransition.OPENED)
    stats = await d.dispatch_due()
    assert stats["dead_letter"] == 1  # 4xx-style permanent -> no retries


async def test_recovery_notification_toggle(db_session) -> None:
    alert = await _open_alert(db_session)
    settings = Settings(encryption_key="x" * 44, notify_send_recovery=False)
    d = NotificationDispatcher(db_session, {}, _policy(), settings=settings)
    created = await d.enqueue(alert, AlertTransition.RESOLVED)
    assert created == []  # recovery disabled -> nothing enqueued
