"""Aggregate model imports.

Importing this package registers every table on `Base.metadata`, which Alembic's
autogenerate and the test fixtures both rely on. Keep this list complete.
"""

from app.models.alert import Alert, AlertNotification
from app.models.base import Base
from app.models.config_change import ConfigurationChange, ConfigurationSnapshot
from app.models.explanation import (
    EXPLANATION_SCHEMA_VERSION,
    AnomalyExplanation,
    AnomalyExplanationContributor,
    AnomalyExplanationDimension,
)
from app.models.identity import AuditLog, Role, User, user_role
from app.models.instance import QRadarInstance
from app.models.log_source import (
    AnomalyStateTransition,
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
    LogSourceMetric,
)
from app.models.monitoring import CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.models.rule import (
    AnalyticsRule,
    DetectionCoverage,
    DetectionCoverageSnapshot,
    RuleDependency,
    RuleHealthSnapshot,
    RuleMetric,
    RuleStateTransition,
    TechniqueMapping,
)
from app.models.search import (
    ScheduledSearch,
    SearchExecution,
    SearchQueryVersion,
    SearchResultMetric,
)

# The set of tables Timescale should manage as hypertables, with their time
# column. Consumed by the migration so this stays the single source of truth.
HYPERTABLES: dict[str, str] = {
    "log_source_metric": "bucket_start",
    "search_result_metric": "bucket_start",
    "rule_metric": "bucket_start",
    "offense_snapshot": "captured_at",
    "rule_health_snapshot": "evaluated_at",
    "detection_coverage_snapshot": "captured_at",
}

__all__ = [
    "EXPLANATION_SCHEMA_VERSION",
    "HYPERTABLES",
    "Alert",
    "AlertNotification",
    "AnalyticsRule",
    "AnomalyExplanation",
    "AnomalyExplanationContributor",
    "AnomalyExplanationDimension",
    "AnomalyStateTransition",
    "AuditLog",
    "Base",
    "CollectionWatermark",
    "ConfigurationChange",
    "ConfigurationSnapshot",
    "DetectionCoverage",
    "DetectionCoverageSnapshot",
    "LogSource",
    "LogSourceAnomaly",
    "LogSourceBaseline",
    "LogSourceDetectorState",
    "LogSourceMetric",
    "OffenseSnapshot",
    "QRadarInstance",
    "Role",
    "RuleDependency",
    "RuleHealthSnapshot",
    "RuleMetric",
    "RuleStateTransition",
    "ScheduledSearch",
    "SearchExecution",
    "SearchQueryVersion",
    "SearchResultMetric",
    "TechniqueMapping",
    "User",
    "user_role",
]
