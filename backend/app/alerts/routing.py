"""Notification routing.

A RoutingPolicy decides which channels+targets an alert notification goes to,
matched on severity, alert type (condition family from the fingerprint),
log-source owner and instance. This is deliberately data-driven so operators can
route "critical anomalies on owner=payments → Teams+PagerDuty webhook" without
code changes.

The default policy is derived from configured channel credentials in Settings
and a minimum severity, so a fresh deployment routes everything above LOW to
whatever webhooks are configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings, get_settings
from app.models.alert import Alert
from app.models.enums import NotificationChannel, Severity

_SEVERITY_RANK = {
    Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2,
    Severity.HIGH: 3, Severity.CRITICAL: 4,
}


@dataclass(frozen=True)
class Route:
    channel: NotificationChannel
    target: str


@dataclass
class RoutingRule:
    channel: NotificationChannel
    target: str
    min_severity: Severity = Severity.LOW
    # Match conditions (None = match any).
    alert_types: frozenset[str] | None = None      # e.g. {"anomaly", "search_threshold"}
    owners: frozenset[str] | None = None
    instances: frozenset[str] | None = None

    def matches(self, alert: Alert) -> bool:
        if _SEVERITY_RANK[alert.severity] < _SEVERITY_RANK[self.min_severity]:
            return False
        if self.alert_types is not None:
            condition = alert.fingerprint.split(":", 1)[0]
            if condition not in self.alert_types:
                return False
        if self.owners is not None:
            owner = (alert.context or {}).get("owner")
            if owner not in self.owners:
                return False
        if self.instances is not None:
            instance = (alert.context or {}).get("instance")
            if instance not in self.instances:
                return False
        return True


@dataclass
class RoutingPolicy:
    rules: list[RoutingRule] = field(default_factory=list)

    def resolve(self, alert: Alert) -> list[Route]:
        seen: set[tuple[str, str]] = set()
        routes: list[Route] = []
        for rule in self.rules:
            if rule.matches(alert):
                key = (rule.channel.value, rule.target)
                if key not in seen:
                    seen.add(key)
                    routes.append(Route(channel=rule.channel, target=rule.target))
        return routes


def default_policy(settings: Settings | None = None) -> RoutingPolicy:
    """Build a policy from configured channel credentials.

    Reads the webhook/email/syslog settings. A channel with no configured target
    contributes no rule, so unconfigured channels are simply never routed to.
    """
    import os

    settings = settings or get_settings()
    rules: list[RoutingRule] = []

    def _add(channel: NotificationChannel, target: str | None) -> None:
        if target:
            rules.append(RoutingRule(channel=channel, target=target, min_severity=Severity.LOW))

    _add(NotificationChannel.TEAMS, os.environ.get("NOTIFY_TEAMS_WEBHOOK_URL"))
    _add(NotificationChannel.SLACK, os.environ.get("NOTIFY_SLACK_WEBHOOK_URL"))
    _add(NotificationChannel.WEBHOOK, os.environ.get("NOTIFY_GENERIC_WEBHOOK_URL"))
    # One rule per recipient, so delivery to one mailbox failing does not park
    # the notification for the others.
    for recipient in os.environ.get("NOTIFY_EMAIL_RECIPIENTS", "").split(","):
        _add(NotificationChannel.EMAIL, recipient.strip())
    syslog_host = os.environ.get("NOTIFY_SYSLOG_HOST")
    if syslog_host:
        syslog_port = os.environ.get("NOTIFY_SYSLOG_PORT", "514")
        _add(NotificationChannel.SYSLOG, f"{syslog_host}:{syslog_port}")
    return RoutingPolicy(rules=rules)
