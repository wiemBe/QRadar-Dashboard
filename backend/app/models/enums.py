"""Domain enumerations shared across models and schemas."""

from __future__ import annotations

from enum import StrEnum


class Criticality(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class AnomalyType(StrEnum):
    """Log-source anomaly taxonomy.

    Ordering is meaningful: NO_EVENTS suppresses the downstream volume and
    parsing anomalies, because a source sending nothing trivially satisfies
    every other failure condition and would otherwise emit five alerts.
    """

    NO_EVENTS = "NO_EVENTS"
    VOLUME_DROP = "VOLUME_DROP"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    PARSING_DEGRADATION = "PARSING_DEGRADATION"
    UNKNOWN_EVENT_SPIKE = "UNKNOWN_EVENT_SPIKE"
    TIMESTAMP_DELAY = "TIMESTAMP_DELAY"
    CARDINALITY_DROP = "CARDINALITY_DROP"
    COLLECTION_ERROR = "COLLECTION_ERROR"
    REPEATED_PAYLOAD = "REPEATED_PAYLOAD"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class NotificationChannel(StrEnum):
    TEAMS = "TEAMS"
    EMAIL = "EMAIL"
    SLACK = "SLACK"
    WEBHOOK = "WEBHOOK"
    SYSLOG = "SYSLOG"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"          # failed this attempt; may retry
    DEAD_LETTER = "DEAD_LETTER"  # exhausted retries; parked for inspection
    SUPPRESSED = "SUPPRESSED"  # deduplicated / routed away


class AlertTransition(StrEnum):
    """The lifecycle transitions that may trigger a notification."""

    OPENED = "OPENED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class ExecutionErrorType(StrEnum):
    """Distinct failure modes for a search execution. Each drives different
    retry and alerting behaviour, so they must not be collapsed to a generic
    'error'."""

    VALIDATION = "VALIDATION"              # AQL rejected before dispatch — never retried
    AUTH = "AUTH"                          # QRadar authentication/authorization failure
    RATE_LIMITED = "RATE_LIMITED"          # QRadar throttled us — retry with backoff
    TIMEOUT = "TIMEOUT"                    # exceeded execution timeout
    SEARCH_FAILED = "SEARCH_FAILED"        # QRadar reported the search itself failed
    NETWORK = "NETWORK"                    # connectivity/transport failure — retryable
    PARSING = "PARSING"                    # result normalization failure
    CANCELLED = "CANCELLED"                # cancelled (expiry/operator)


class DetectorSignal(StrEnum):
    """Per-interval verdict feeding anomaly hysteresis."""

    HEALTHY = "HEALTHY"
    ANOMALOUS = "ANOMALOUS"
    UNKNOWN = "UNKNOWN"  # insufficient data / in maintenance


class VisualizationType(StrEnum):
    TABLE = "TABLE"
    LINE = "LINE"
    BAR = "BAR"
    PIE = "PIE"
    SINGLE_VALUE = "SINGLE_VALUE"
    HEATMAP = "HEATMAP"


class ChangeType(StrEnum):
    CREATED = "CREATED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"


class CoverageStatus(StrEnum):
    COVERED = "COVERED"
    PARTIAL = "PARTIAL"
    NOT_COVERED = "NOT_COVERED"
    DEGRADED = "DEGRADED"


class InstanceStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNREACHABLE = "UNREACHABLE"
    UNKNOWN = "UNKNOWN"
