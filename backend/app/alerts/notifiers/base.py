"""Notifier interface and the payload contract.

A Notifier turns a normalized NotificationMessage into a delivery on one channel.
It raises NotifierError on failure so the dispatcher can apply retry/backoff and,
eventually, dead-letter. Notifiers never see raw secrets beyond the channel
credential they were constructed with, and payloads are redacted upstream.

Tests use MockNotifier (records sends, can be told to fail N times) so no real
external notification is ever sent during testing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models.enums import AlertTransition, NotificationChannel, Severity


@dataclass
class NotificationMessage:
    """Channel-agnostic, already-redacted alert notification."""

    title: str
    body: str
    severity: Severity
    transition: AlertTransition
    alert_id: str
    fingerprint: str
    # Structured fields channels may render (evidence, entity, links).
    fields: dict = field(default_factory=dict)

    def summary_line(self) -> str:
        return f"[{self.severity.value}] {self.title} ({self.transition.value})"


class NotifierError(RuntimeError):
    """Delivery failed. Retryable unless `permanent` is set."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class Notifier(ABC):
    channel: NotificationChannel

    @abstractmethod
    async def send(self, target: str, message: NotificationMessage) -> None:
        """Deliver `message` to `target`. Raise NotifierError on failure."""


class MockNotifier(Notifier):
    """Records deliveries; optionally fails the first `fail_times` attempts."""

    def __init__(self, channel: NotificationChannel, *, fail_times: int = 0,
                 permanent: bool = False) -> None:
        self.channel = channel
        self.sent: list[tuple[str, NotificationMessage]] = []
        self._fail_times = fail_times
        self._permanent = permanent
        self.attempts = 0

    async def send(self, target: str, message: NotificationMessage) -> None:
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise NotifierError(
                f"mock failure {self.attempts}", permanent=self._permanent
            )
        self.sent.append((target, message))
