"""Phase 3 jobs must be both registered and periodically scheduled."""

import asyncio

from app.core.config import get_settings
from app.workers import tasks as _tasks
from app.workers.celery_app import celery_app


def test_phase3_tasks_are_registered() -> None:
    expected = {
        "sync_log_sources",
        "collect_offenses",
        "sync_rule_inventory",
        "collect_rule_metrics",
        "detect_stale_offense_collection",
        "calculate_offense_aggregates",
        "evaluate_rule_health",
        "evaluate_detection_coverage",
    }
    assert expected <= set(celery_app.tasks)


def test_new_phase3_beat_entries_are_configurable() -> None:
    settings = get_settings()
    schedule = celery_app.conf.beat_schedule

    assert schedule["collect-rule-metrics"]["schedule"] == float(
        settings.rule_metric_collection_interval_seconds
    )
    assert schedule["detect-stale-offense-collection"]["schedule"] == float(
        settings.offense_stale_check_interval_seconds
    )
    assert schedule["calculate-offense-aggregates"]["schedule"] == float(
        settings.offense_aggregate_interval_seconds
    )


def test_sync_task_runner_disposes_async_engine(monkeypatch) -> None:
    disposed = False

    async def dispose() -> None:
        nonlocal disposed
        disposed = True

    async def work() -> str:
        await asyncio.sleep(0)
        return "done"

    monkeypatch.setattr(_tasks, "dispose_engine", dispose)
    assert _tasks._run(work()) == "done"
    assert disposed is True
