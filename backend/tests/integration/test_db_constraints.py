"""Database-level guarantees: partial-unique alert dedup, notification
uniqueness, retention service, and transaction rollback. Gated on
TEST_DATABASE_URL (see conftest.requires_db)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.alert import Alert, AlertNotification
from app.models.enums import (
    AlertStatus,
    AlertTransition,
    NotificationChannel,
    Severity,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _alert(dedup: str, status: AlertStatus = AlertStatus.OPEN) -> Alert:
    now = datetime.now(UTC)
    return Alert(
        dedup_key=dedup,
        fingerprint=dedup,
        title="t",
        severity=Severity.HIGH,
        status=status,
        source_type="log_source",
        source_id=uuid.uuid4(),
        opened_at=now,
        first_seen_at=now,
    )


async def test_partial_unique_blocks_second_open_alert(db_session) -> None:
    db_session.add(_alert("anomaly:abc"))
    await db_session.flush()
    db_session.add(_alert("anomaly:abc"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_partial_unique_allows_new_alert_after_resolution(db_session) -> None:
    first = _alert("anomaly:xyz", status=AlertStatus.RESOLVED)
    first.resolved_at = datetime.now(UTC)
    db_session.add(first)
    await db_session.flush()
    # A resolved alert must not block a genuine new occurrence of the condition.
    db_session.add(_alert("anomaly:xyz"))
    await db_session.flush()  # must not raise

    open_count = len(
        (
            await db_session.scalars(
                select(Alert).where(
                    Alert.dedup_key == "anomaly:xyz", Alert.status == AlertStatus.OPEN
                )
            )
        ).all()
    )
    assert open_count == 1


async def test_notification_transition_uniqueness(db_session) -> None:
    alert = _alert("anomaly:note")
    db_session.add(alert)
    await db_session.flush()

    def _n() -> AlertNotification:
        return AlertNotification(
            alert_id=alert.id,
            channel=NotificationChannel.SLACK,
            target="hook",
            transition=AlertTransition.OPENED,
        )

    db_session.add(_n())
    await db_session.flush()
    db_session.add(_n())
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_rollback_leaves_no_partial_state(db_session) -> None:
    db_session.add(_alert("anomaly:rb"))
    await db_session.flush()
    # Force a constraint violation in the same transaction.
    db_session.add(_alert("anomaly:rb"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    remaining = len((await db_session.scalars(select(Alert))).all())
    assert remaining == 0, "rollback must discard the whole transaction"


async def test_retention_service_disabled_by_default(db_session) -> None:
    """With retention_enabled=False (default), the service applies no policy."""
    from app.core.config import Settings
    from app.services.timescale import apply_policies

    settings = Settings(encryption_key="x" * 44)  # retention_enabled defaults False
    outcome = await apply_policies(db_session, settings)
    # Either timescale is absent, or every table is explicitly retention-disabled.
    if outcome.get("_status") != "timescaledb-absent":
        assert all(v == "retention-disabled" for k, v in outcome.items() if not k.startswith("_"))
