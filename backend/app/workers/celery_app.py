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
}
