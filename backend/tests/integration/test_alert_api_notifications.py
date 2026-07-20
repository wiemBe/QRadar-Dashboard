"""Operator-driven alert transitions must enqueue notifications.

Acknowledging or resolving an alert in the UI is a state change the rest of the
team needs to see; before this was wired, only the anomaly engine could produce
a notification and a manual resolve went out silently.

Enqueue-only is the property under test: the request records intent and returns,
it never blocks on an outbound webhook. Delivery is the dispatch_notifications
worker's job and is covered in test_notification_dispatch.py. DB-gated.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from app.alerts.service import AlertInput, AlertService
from app.core.database import get_session
from app.main import create_app
from app.models.alert import AlertNotification
from app.models.enums import AlertTransition, NotificationStatus, Severity
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _client(session, principal: Principal) -> httpx.AsyncClient:
    """An in-process async client sharing the test's session.

    Deliberately not the sync TestClient: that drives the app on its own event
    loop, while `db_session` holds an asyncpg connection bound to the test's
    loop, so the shared session would fail with "attached to a different loop".
    ASGITransport runs the app on this loop instead.
    """

    async def _session():
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _open_alert(session):
    result = await AlertService(session).open_or_update(
        AlertInput(
            fingerprint=f"anomaly:{uuid.uuid4().hex[:8]}", title="volume drop",
            severity=Severity.HIGH, source_type="log_source", source_id=uuid.uuid4(),
            evidence={"observed_value": 3.0},
        )
    )
    await session.commit()
    return result.alert


async def _notifications(session, alert_id) -> list[AlertNotification]:
    rows = await session.scalars(
        select(AlertNotification).where(AlertNotification.alert_id == alert_id)
    )
    return list(rows.all())


async def test_acknowledge_enqueues_a_notification(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", "https://hook.internal/alerts")
    alert = await _open_alert(db_session)
    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"admin:*"}))
    ) as client:
        resp = await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")
    assert resp.status_code == 200, resp.text

    rows = await _notifications(db_session, alert.id)
    assert [r.transition for r in rows] == [AlertTransition.ACKNOWLEDGED]
    # Intent only — the request itself sends nothing.
    assert rows[0].status == NotificationStatus.PENDING
    assert rows[0].sent_at is None


async def test_resolve_enqueues_a_recovery_notification(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", "https://hook.internal/alerts")
    alert = await _open_alert(db_session)
    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"admin:*"}))
    ) as client:
        resp = await client.post(
            f"/api/v1/alerts/{alert.id}/resolve", json={"reason": "log source restored"}
        )
    assert resp.status_code == 200, resp.text

    rows = await _notifications(db_session, alert.id)
    assert [r.transition for r in rows] == [AlertTransition.RESOLVED]


async def test_repeated_acknowledge_does_not_enqueue_twice(db_session, monkeypatch) -> None:
    """The second acknowledge is a no-op transition, and a no-op must not page."""
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", "https://hook.internal/alerts")
    alert = await _open_alert(db_session)
    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"admin:*"}))
    ) as client:
        await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")
        await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")

    assert len(await _notifications(db_session, alert.id)) == 1


async def test_no_configured_channel_enqueues_nothing(db_session, monkeypatch) -> None:
    # An unconfigured deployment must not accumulate undeliverable rows.
    for var in (
        "NOTIFY_GENERIC_WEBHOOK_URL", "NOTIFY_TEAMS_WEBHOOK_URL",
        "NOTIFY_SLACK_WEBHOOK_URL", "NOTIFY_SYSLOG_HOST", "NOTIFY_EMAIL_RECIPIENTS",
    ):
        monkeypatch.delenv(var, raising=False)
    alert = await _open_alert(db_session)
    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"admin:*"}))
    ) as client:
        resp = await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")

    assert resp.status_code == 200
    assert await _notifications(db_session, alert.id) == []
