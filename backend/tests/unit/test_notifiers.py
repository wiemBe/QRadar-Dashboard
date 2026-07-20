"""Notifier channels. No real external notification is ever sent:
HTTP channels are mocked with respx; syslog's socket is monkeypatched."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.alerts.notifiers.base import MockNotifier, NotificationMessage, NotifierError
from app.alerts.notifiers.channels import (
    GenericWebhookNotifier,
    SlackNotifier,
    SyslogNotifier,
    TeamsNotifier,
)
from app.models.enums import AlertTransition, NotificationChannel, Severity

pytestmark = pytest.mark.asyncio


def _msg() -> NotificationMessage:
    return NotificationMessage(
        title="VOLUME_DROP on fw-01",
        body="average EPS below baseline",
        severity=Severity.HIGH,
        transition=AlertTransition.OPENED,
        alert_id="a1",
        fingerprint="anomaly:abc",
        fields={"observed_value": 3.0, "expected_value": 50.0},
    )


async def test_mock_notifier_records_and_can_fail() -> None:
    n = MockNotifier(NotificationChannel.SLACK, fail_times=1)
    with pytest.raises(NotifierError):
        await n.send("hook", _msg())      # first attempt fails
    await n.send("hook", _msg())          # second succeeds
    assert len(n.sent) == 1


@respx.mock
async def test_slack_posts_blocks() -> None:
    route = respx.post("https://hooks.slack.test/x").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        await SlackNotifier(client).send("https://hooks.slack.test/x", _msg())
    assert route.called
    body = route.calls[0].request.content
    assert b"blocks" in body


@respx.mock
async def test_teams_posts_messagecard() -> None:
    route = respx.post("https://teams.test/hook").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        await TeamsNotifier(client).send("https://teams.test/hook", _msg())
    assert b"MessageCard" in route.calls[0].request.content


@respx.mock
async def test_generic_webhook_5xx_is_retryable() -> None:
    respx.post("https://hook.test/x").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        with pytest.raises(NotifierError) as exc:
            await GenericWebhookNotifier(client).send("https://hook.test/x", _msg())
    assert not exc.value.permanent  # 5xx -> retry


@respx.mock
async def test_generic_webhook_4xx_is_permanent() -> None:
    respx.post("https://hook.test/x").mock(return_value=httpx.Response(400))
    async with httpx.AsyncClient() as client:
        with pytest.raises(NotifierError) as exc:
            await GenericWebhookNotifier(client).send("https://hook.test/x", _msg())
    assert exc.value.permanent  # 4xx -> do not retry


async def test_syslog_sends_udp_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[bytes, tuple]] = []

    class FakeSock:
        def __init__(self, *a, **k) -> None: ...
        def sendto(self, data: bytes, addr: tuple) -> None:
            sent.append((data, addr))
        def close(self) -> None: ...

    monkeypatch.setattr("socket.socket", lambda *a, **k: FakeSock())
    await SyslogNotifier().send("logs.internal:514", _msg())
    assert sent
    data, addr = sent[0]
    assert addr == ("logs.internal", 514)
    assert b"qradar-observability" in data
