"""Routing policy matching. Alert objects are built in memory (not persisted),
since RoutingRule.matches only reads attributes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.alerts.routing import RoutingPolicy, RoutingRule, default_policy
from app.models.alert import Alert
from app.models.enums import AlertStatus, NotificationChannel, Severity


def _alert(*, severity=Severity.HIGH, fingerprint="anomaly:abc", owner=None) -> Alert:
    now = datetime.now(UTC)
    return Alert(
        dedup_key=fingerprint, fingerprint=fingerprint, title="t", severity=severity,
        status=AlertStatus.OPEN, source_type="log_source", source_id=uuid.uuid4(),
        opened_at=now, first_seen_at=now, context={"owner": owner} if owner else {},
    )


def test_min_severity_filters() -> None:
    rule = RoutingRule(channel=NotificationChannel.SLACK, target="hook",
                       min_severity=Severity.HIGH)
    assert rule.matches(_alert(severity=Severity.CRITICAL))
    assert rule.matches(_alert(severity=Severity.HIGH))
    assert not rule.matches(_alert(severity=Severity.MEDIUM))


def test_alert_type_filter() -> None:
    rule = RoutingRule(channel=NotificationChannel.TEAMS, target="hook",
                       alert_types=frozenset({"search_threshold"}))
    assert not rule.matches(_alert(fingerprint="anomaly:x"))
    assert rule.matches(_alert(fingerprint="search_threshold:y"))


def test_owner_filter() -> None:
    rule = RoutingRule(channel=NotificationChannel.EMAIL, target="soc@x",
                       owners=frozenset({"payments"}))
    assert rule.matches(_alert(owner="payments"))
    assert not rule.matches(_alert(owner="infra"))


def test_policy_deduplicates_routes() -> None:
    rule = RoutingRule(channel=NotificationChannel.SLACK, target="hook")
    policy = RoutingPolicy(rules=[rule, rule])  # same rule twice
    routes = policy.resolve(_alert())
    assert len(routes) == 1


def test_default_policy_routes_each_configured_email_recipient(monkeypatch) -> None:
    # An EmailNotifier is always built, so without a routing rule email would be
    # silently undeliverable.
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "soc@x.internal, oncall@x.internal")
    routes = default_policy().resolve(_alert())
    email_targets = {r.target for r in routes if r.channel is NotificationChannel.EMAIL}
    assert email_targets == {"soc@x.internal", "oncall@x.internal"}


def test_default_policy_ignores_blank_email_configuration(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", " , ")
    routes = default_policy().resolve(_alert())
    assert not [r for r in routes if r.channel is NotificationChannel.EMAIL]


def test_policy_returns_multiple_channels() -> None:
    policy = RoutingPolicy(rules=[
        RoutingRule(channel=NotificationChannel.SLACK, target="s"),
        RoutingRule(channel=NotificationChannel.TEAMS, target="t"),
    ])
    channels = {r.channel for r in policy.resolve(_alert())}
    assert channels == {NotificationChannel.SLACK, NotificationChannel.TEAMS}
