"""Celery task entrypoints for background collection, detection and delivery.

Each task is a thin synchronous wrapper that runs an async orchestration on a
fresh event loop with its own AsyncSession. The orchestration functions are
also importable and awaited directly by the APScheduler MVP fallback and by
tests, so the scheduling mechanism and the work are decoupled.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.registry import build_notifiers
from app.alerts.routing import default_policy
from app.alerts.search_alerts import SearchAlertEvaluator
from app.anomaly.baseline import BaselineBuilder
from app.anomaly.engine import AnomalyEngine
from app.collectors.metric_collector import MetricCollector
from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.providers.factory import build_provider
from app.services.concurrency import InMemoryConcurrencyLimiter
from app.services.search_executor import SearchExecutor
from app.services.search_scheduler import SearchScheduler
from app.workers.celery_app import celery_app

logger = logging.getLogger("app.tasks")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- collection
async def collect_metrics() -> dict:
    maker = get_sessionmaker()
    provider = build_provider()
    try:
        async with maker() as session:
            instance = await session.scalar(select(QRadarInstance).limit(1))
            if instance is None:
                return {"status": "no-instance"}
            collector = MetricCollector(session, provider)
            report = await collector.collect(instance)
            await session.commit()
            return {
                "intervals": report.intervals_collected,
                "samples": report.samples_written,
                "skipped_locked": report.skipped_locked,
            }
    finally:
        await provider.aclose()


# ------------------------------------------------------------------ anomalies
async def evaluate_anomalies() -> dict:
    maker = get_sessionmaker()
    async with maker() as session:
        policy = default_policy()
        # Enqueue-only dispatcher: notifiers not needed to create rows.
        enqueuer = NotificationDispatcher(session, notifiers={}, policy=policy)
        engine = AnomalyEngine(session, enqueuer=enqueuer)
        sources = list(
            (
                await session.scalars(
                    select(LogSource).where(LogSource.monitoring_enabled.is_(True))
                )
            ).all()
        )
        opened = resolved = 0
        for source in sources:
            report = await engine.evaluate_latest(source)
            opened += len(report.opened)
            resolved += len(report.resolved)
        await session.commit()
        return {"sources": len(sources), "opened": opened, "resolved": resolved}


# ------------------------------------------------------------------ baselines
async def rebuild_baselines() -> dict:
    maker = get_sessionmaker()
    async with maker() as session:
        builder = BaselineBuilder(session)
        sources = list(
            (
                await session.scalars(
                    select(LogSource).where(LogSource.monitoring_enabled.is_(True))
                )
            ).all()
        )
        cells = 0
        for source in sources:
            cells += await builder.rebuild_for_source(source)
        await session.commit()
        return {"sources": len(sources), "cells": cells}


# ------------------------------------------------------------ scheduled search
async def run_due_searches() -> dict:
    """Execute every stored search whose cron schedule has come due.

    Threshold and failure alerts raised by a run are enqueued here (delivery is
    the dispatch_notifications task's job), so a breach detected by a scheduled
    search reaches the same notification pipeline as an anomaly.
    """
    settings = get_settings()
    maker = get_sessionmaker()
    provider = build_provider()
    try:
        async with maker() as session:
            limiter = InMemoryConcurrencyLimiter(
                per_instance=settings.ariel_max_concurrent_searches,
                global_limit=settings.ariel_global_max_concurrent_searches,
            )
            enqueuer = NotificationDispatcher(session, notifiers={}, policy=default_policy())
            scheduler = SearchScheduler(
                session,
                SearchExecutor(session, provider, limiter, settings=settings),
                settings=settings,
                alert_evaluator=SearchAlertEvaluator(
                    session, settings=settings, enqueuer=enqueuer
                ),
            )
            report = await scheduler.run_due()
            await session.commit()
            return {
                "considered": report.considered,
                "dispatched": len(report.dispatched),
                "seeded": len(report.seeded),
                "skipped_running": len(report.skipped_running),
                "invalid_cron": len(report.invalid_cron),
            }
    finally:
        await provider.aclose()


# --------------------------------------------------------------- notifications
async def dispatch_notifications() -> dict:
    maker = get_sessionmaker()
    notifiers = build_notifiers()
    try:
        async with maker() as session:
            dispatcher = NotificationDispatcher(session, notifiers, default_policy())
            stats = await dispatcher.dispatch_due()
            await session.commit()
            return stats
    finally:
        for n in notifiers.values():
            client = getattr(n, "_client", None)
            if client is not None:
                await client.aclose()


# --------------------------------------------------------------- celery shims
@celery_app.task(name="collect_metrics")
def collect_metrics_task() -> dict:
    return _run(collect_metrics())


@celery_app.task(name="evaluate_anomalies")
def evaluate_anomalies_task() -> dict:
    return _run(evaluate_anomalies())


@celery_app.task(name="rebuild_baselines")
def rebuild_baselines_task() -> dict:
    return _run(rebuild_baselines())


@celery_app.task(name="run_due_searches")
def run_due_searches_task() -> dict:
    return _run(run_due_searches())


@celery_app.task(name="dispatch_notifications")
def dispatch_notifications_task() -> dict:
    return _run(dispatch_notifications())
