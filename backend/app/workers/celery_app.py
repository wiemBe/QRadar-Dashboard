"""Celery application.

Phase 1 defines the app and the beat schedule placeholder only; the collection,
anomaly and notification tasks are registered in Phase 2. APScheduler is the
documented MVP fallback for teams not running Celery — see README.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "qradar_observability",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)

# Ensure task modules are imported so the workers register them.
celery_app.autodiscover_tasks(["app.workers"])
celery_app.conf.imports = ("app.workers.tasks",)

# Periodic schedule. Intervals are conservative defaults; tune per deployment.
celery_app.conf.beat_schedule = {
    "collect-metrics": {
        "task": "collect_metrics",
        "schedule": float(settings.collection_interval_seconds),
    },
    "evaluate-anomalies": {
        # Runs shortly after each collection cycle.
        "task": "evaluate_anomalies",
        "schedule": float(settings.collection_interval_seconds),
    },
    "dispatch-notifications": {
        "task": "dispatch_notifications",
        "schedule": 30.0,
    },
    "run-due-searches": {
        "task": "run_due_searches",
        "schedule": 60.0,
    },
    "rebuild-baselines": {
        # Daily; baselining is expensive and does not need to be frequent.
        "task": "rebuild_baselines",
        "schedule": 86400.0,
    },
    "collect-anomaly-explanations": {
        # Phase A investigation evidence. Deliberately decoupled from
        # detection: each anomaly costs two bounded Ariel queries per
        # dimension, so an unresponsive appliance must delay evidence rather
        # than delay the alert that something is wrong.
        "task": "collect_anomaly_explanations",
        "schedule": float(settings.explanation_collection_interval_seconds),
    },
    # --- Phase 3 -------------------------------------------------------------
    # Every interval below is configurable; none is a hardcoded production
    # cadence. Ordering matters: inventory feeds health, health feeds coverage,
    # so each runs no more often than what it depends on.
    "sync-log-sources": {
        "task": "sync_log_sources",
        "schedule": float(settings.log_source_sync_interval_seconds),
    },
    "collect-offenses": {
        "task": "collect_offenses",
        "schedule": float(settings.offense_collection_interval_seconds),
    },
    "detect-stale-offense-collection": {
        "task": "detect_stale_offense_collection",
        "schedule": float(settings.offense_stale_check_interval_seconds),
    },
    "calculate-offense-aggregates": {
        "task": "calculate_offense_aggregates",
        "schedule": float(settings.offense_aggregate_interval_seconds),
    },
    "sync-rule-inventory": {
        # Also covers building blocks: RuleCollector merges both endpoints.
        "task": "sync_rule_inventory",
        "schedule": float(settings.rule_collection_interval_seconds),
    },
    "collect-rule-metrics": {
        # Inferred lower bounds from stored offense contribution; no QRadar
        # write and no invented rule-statistics endpoint.
        "task": "collect_rule_metrics",
        "schedule": float(settings.rule_metric_collection_interval_seconds),
    },
    "evaluate-rule-health": {
        "task": "evaluate_rule_health",
        "schedule": float(settings.rule_health_evaluation_interval_seconds),
    },
    "evaluate-detection-coverage": {
        "task": "evaluate_detection_coverage",
        "schedule": float(settings.coverage_evaluation_interval_seconds),
    },
}
