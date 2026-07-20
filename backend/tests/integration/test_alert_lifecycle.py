"""Alert lifecycle service: open, dedup, acknowledge, resolve, reopen. DB-gated."""

from __future__ import annotations

import uuid

import pytest

from app.alerts.service import AlertInput, AlertService
from app.models.enums import AlertStatus, AlertTransition, Severity

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _input(fp: str = "anomaly:abc") -> AlertInput:
    return AlertInput(
        fingerprint=fp, title="t", severity=Severity.HIGH,
        source_type="log_source", source_id=uuid.uuid4(),
        evidence={"observed_value": 3.0}, source_anomaly_ids=[str(uuid.uuid4())],
    )


async def test_open_then_update_dedups(db_session) -> None:
    svc = AlertService(db_session)
    first = await svc.open_or_update(_input())
    assert first.opened and first.transition is AlertTransition.OPENED

    second = await svc.open_or_update(_input())
    assert second.updated and not second.opened
    assert second.transition is None  # dedup: no notification on update
    assert second.alert.id == first.alert.id
    assert second.alert.occurrence_count == 2


async def test_acknowledge_transition(db_session) -> None:
    svc = AlertService(db_session)
    r = await svc.open_or_update(_input("anomaly:ack"))
    ack = await svc.acknowledge(r.alert.id, actor="analyst")
    assert ack.alert.status == AlertStatus.ACKNOWLEDGED
    assert ack.alert.acknowledged_by == "analyst"
    assert ack.transition is AlertTransition.ACKNOWLEDGED
    # Idempotent: acknowledging again is a no-op transition.
    again = await svc.acknowledge(r.alert.id, actor="analyst")
    assert again.transition is None


async def test_resolve_then_reopen_allows_new_alert(db_session) -> None:
    svc = AlertService(db_session)
    r1 = await svc.open_or_update(_input("anomaly:cycle"))
    await svc.resolve(r1.alert.id, actor="analyst", reason="fixed")
    # After resolution the same condition may open a brand-new alert.
    r2 = await svc.open_or_update(_input("anomaly:cycle"))
    assert r2.opened
    assert r2.alert.id != r1.alert.id


async def test_resolve_by_fingerprint(db_session) -> None:
    svc = AlertService(db_session)
    await svc.open_or_update(_input("anomaly:byfp"))
    result = await svc.resolve_by_fingerprint("anomaly:byfp", actor="engine", reason="recovered")
    assert result is not None
    assert result.alert.status == AlertStatus.RESOLVED
    # Nothing active left to resolve.
    assert await svc.resolve_by_fingerprint("anomaly:byfp", actor="engine") is None
