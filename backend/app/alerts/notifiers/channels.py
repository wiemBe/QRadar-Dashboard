"""Concrete notifiers: Teams, Slack, generic webhook, email, syslog.

Each maps a NotificationMessage onto the channel's wire format. HTTP channels
share an injected httpx.AsyncClient so tests can mock transport with respx and
production reuses connections. No secret is logged; the webhook URL is the
credential and is passed as the target, never emitted.
"""

from __future__ import annotations

import json
import logging
import socket

import httpx

from app.alerts.notifiers.base import NotificationMessage, Notifier, NotifierError
from app.models.enums import NotificationChannel, Severity

logger = logging.getLogger("app.notifiers")

_SEVERITY_COLOR = {
    Severity.CRITICAL: "D70000",
    Severity.HIGH: "E8710A",
    Severity.MEDIUM: "E3B341",
    Severity.LOW: "2EA043",
    Severity.INFO: "0969DA",
}

# Syslog severity per RFC 5424 (we map onto user.* facility).
_SYSLOG_SEVERITY = {
    Severity.CRITICAL: 2, Severity.HIGH: 3, Severity.MEDIUM: 4,
    Severity.LOW: 5, Severity.INFO: 6,
}


class _HttpNotifier(Notifier):
    def __init__(self, client: httpx.AsyncClient, *, timeout: float = 10.0) -> None:
        self._client = client
        self._timeout = timeout

    async def _post(self, url: str, payload: dict) -> None:
        try:
            resp = await self._client.post(url, json=payload, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise NotifierError(f"transport error: {exc}") from exc
        if resp.status_code >= 500 or resp.status_code == 429:
            raise NotifierError(f"retryable status {resp.status_code}")
        if resp.status_code >= 400:
            # 4xx (bad webhook, unauthorized) won't fix itself — permanent.
            raise NotifierError(f"permanent status {resp.status_code}", permanent=True)


class TeamsNotifier(_HttpNotifier):
    channel = NotificationChannel.TEAMS

    async def send(self, target: str, message: NotificationMessage) -> None:
        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": _SEVERITY_COLOR.get(message.severity, "0969DA"),
            "summary": message.summary_line(),
            "title": message.summary_line(),
            "text": message.body,
            "sections": [
                {
                    "facts": [
                        {"name": k, "value": str(v)} for k, v in message.fields.items()
                    ]
                }
            ],
        }
        await self._post(target, card)


class SlackNotifier(_HttpNotifier):
    channel = NotificationChannel.SLACK

    async def send(self, target: str, message: NotificationMessage) -> None:
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text", "text": message.summary_line()[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message.body[:2900]}},
        ]
        if message.fields:
            fields = [
                {"type": "mrkdwn", "text": f"*{k}*\n{v}"}
                for k, v in list(message.fields.items())[:10]
            ]
            blocks.append({"type": "section", "fields": fields})
        await self._post(target, {"text": message.summary_line(), "blocks": blocks})


class GenericWebhookNotifier(_HttpNotifier):
    channel = NotificationChannel.WEBHOOK

    async def send(self, target: str, message: NotificationMessage) -> None:
        await self._post(
            target,
            {
                "title": message.title,
                "body": message.body,
                "severity": message.severity.value,
                "transition": message.transition.value,
                "alert_id": message.alert_id,
                "fingerprint": message.fingerprint,
                "fields": message.fields,
            },
        )


class EmailNotifier(Notifier):
    """SMTP email. Runs the blocking smtplib call in a thread so it does not
    block the event loop. Config (host/port/creds/TLS) is injected."""

    channel = NotificationChannel.EMAIL

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        use_tls: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._use_tls = use_tls

    async def send(self, target: str, message: NotificationMessage) -> None:
        import asyncio

        await asyncio.to_thread(self._send_sync, target, message)

    def _send_sync(self, target: str, message: NotificationMessage) -> None:
        import smtplib
        from email.message import EmailMessage

        if not self._host:
            raise NotifierError("SMTP host not configured", permanent=True)
        email = EmailMessage()
        email["Subject"] = message.summary_line()
        email["From"] = self._sender
        email["To"] = target
        body = message.body + "\n\n" + "\n".join(
            f"{k}: {v}" for k, v in message.fields.items()
        )
        email.set_content(body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=15) as server:
                if self._use_tls:
                    server.starttls()
                if self._username:
                    server.login(self._username, self._password or "")
                server.send_message(email)
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused) as exc:
            raise NotifierError(f"permanent SMTP error: {exc}", permanent=True) from exc
        except OSError as exc:
            raise NotifierError(f"SMTP transport error: {exc}") from exc


class SyslogNotifier(Notifier):
    """RFC 5424-ish syslog over UDP. `target` is 'host:port'."""

    channel = NotificationChannel.SYSLOG
    _FACILITY = 1  # user-level

    async def send(self, target: str, message: NotificationMessage) -> None:
        import asyncio

        await asyncio.to_thread(self._send_sync, target, message)

    def _send_sync(self, target: str, message: NotificationMessage) -> None:
        host, _, port_s = target.partition(":")
        port = int(port_s or 514)
        sev = _SYSLOG_SEVERITY.get(message.severity, 6)
        pri = self._FACILITY * 8 + sev
        payload = json.dumps(
            {
                "title": message.title,
                "transition": message.transition.value,
                "fingerprint": message.fingerprint,
                "fields": message.fields,
            }
        )
        line = f"<{pri}>qradar-observability: {message.summary_line()} {payload}"
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.sendto(line.encode()[:2048], (host, port))
            finally:
                sock.close()
        except OSError as exc:
            raise NotifierError(f"syslog transport error: {exc}") from exc
