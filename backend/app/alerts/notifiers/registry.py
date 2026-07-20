"""Build the channel -> Notifier map from configuration.

Kept separate from the notifiers themselves so the dispatcher can be handed a
map without importing httpx/smtplib at module load, and so tests can substitute
MockNotifiers wholesale.
"""

from __future__ import annotations

import os

import httpx

from app.alerts.notifiers.base import Notifier
from app.alerts.notifiers.channels import (
    EmailNotifier,
    GenericWebhookNotifier,
    SlackNotifier,
    SyslogNotifier,
    TeamsNotifier,
)
from app.models.enums import NotificationChannel


def build_notifiers(client: httpx.AsyncClient | None = None) -> dict[NotificationChannel, Notifier]:
    client = client or httpx.AsyncClient()
    return {
        NotificationChannel.TEAMS: TeamsNotifier(client),
        NotificationChannel.SLACK: SlackNotifier(client),
        NotificationChannel.WEBHOOK: GenericWebhookNotifier(client),
        NotificationChannel.SYSLOG: SyslogNotifier(),
        NotificationChannel.EMAIL: EmailNotifier(
            host=os.environ.get("SMTP_HOST", ""),
            port=int(os.environ.get("SMTP_PORT", "587")),
            username=os.environ.get("SMTP_USERNAME") or None,
            password=os.environ.get("SMTP_PASSWORD") or None,
            sender=os.environ.get("SMTP_FROM", "qradar-observability@example.internal"),
            use_tls=os.environ.get("SMTP_USE_TLS", "true").lower() == "true",
        ),
    }
