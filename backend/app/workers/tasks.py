"""Celery task entrypoints for background collection, detection and delivery.

Each task is a thin synchronous wrapper that runs an async orchestration on a
fresh event loop with its own AsyncSession. The orchestration functions are
also importable and awaited directly by the APScheduler MVP fallback and by
tests, so the scheduling mechanism and the work are decoupled.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.registry import build_notifiers
from app.alerts.routing import default_policy
from app.alerts.search_alerts import SearchAlertEvaluator
from app.anomaly.baseline import BaselineBuilder
from app.anomaly.engine import AnomalyEngine
from app.collectors.log_source_collector import LogSourceCollector
from app.collectors.metric_collector import MetricCollector
from app.collectors.offense_collector import OffenseCollector
from app.collectors.rule_collector import RuleCollector
from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.providers.base import QRadarProvider
from app.providers.factory import build_provider, build_provider_for_instance
from app.services.concurrency import InMemoryConcurrencyLimiter
from app.services.detection_coverage import DetectionCoverageEvaluator
from app.services.rule_health import RuleHealthEvaluator
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


# ------------------------------------------------------- phase 3 collection
async def _for_each_instance(
    run: Callable[[AsyncSession, QRadarInstance, QRadarProvider | None], Awaitable[dict]],
    instance_name: str | None = None,
    *,
    needs_provider: bool = True,
) -> dict:
    """Run `run` against every enabled instance, or one named instance.

    Each instance gets a provider built from its own stored credentials and its
    own transaction, so one unreachable console neither fails nor rolls back
    the others. Provider clients are always closed, including on failure — a
    leaked httpx client holds a TLS connection to the SIEM open.

    `needs_provider=False` skips construction entirely for evaluators that only
    read what collection already stored; there is no reason to open a
    connection to QRadar to classify rows in our own database.
    """
    maker = get_sessionmaker()
    results: list[dict] = []

    async with maker() as session:
        query = select(QRadarInstance).where(QRadarInstance.enabled.is_(True))
        if instance_name:
            query = query.where(QRadarInstance.name == instance_name)
        instances = list((await session.scalars(query)).all())

        if not instances:
            return {"status": "no-instance", "instances": 0, "results": []}

        for instance in instances:
            provider = build_provider_for_instance(instance) if needs_provider else None
            try:
                outcome = await run(session, instance, provider)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                # The exception type only: an upstream message may carry
                # request detail we must not persist or log.
                outcome = {"status": "failed", "error_class": type(exc).__name__}
                logger.warning(
                    "collection run failed",
                    extra={
                        "instance": instance.name,
                        "error_class": type(exc).__name__,
                    },
                )
            finally:
                if provider is not None:
                    await provider.aclose()
            results.append({"instance": instance.name, **outcome})

    return {"instances": len(results), "results": results}


async def sync_log_sources(instance_name: str | None = None) -> dict:
    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        report = await LogSourceCollector(session, provider).sync(instance)
        return {
            "status": "skipped_locked" if report.skipped_locked else "ok",
            "seen": report.log_sources_seen,
            "created": report.created,
            "updated": report.updated,
            "duration_ms": report.duration_ms,
            "error": report.error,
        }

    return await _for_each_instance(run, instance_name)


async def collect_offenses(instance_name: str | None = None) -> dict:
    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        report = await OffenseCollector(session, provider).collect(instance)
        return {
            "status": "skipped_locked" if report.skipped_locked else "ok",
            "seen": report.offenses_seen,
            "written": report.snapshots_written,
            "unchanged": report.unchanged,
            "duration_ms": report.duration_ms,
            "error": report.error,
        }

    return await _for_each_instance(run, instance_name)


async def sync_rule_inventory(instance_name: str | None = None) -> dict:
    """Synchronize analytics rules *and* building blocks.

    One task covers both because `RuleCollector.sync` merges the two endpoints
    into a single locked pass over one table. Splitting them would double the
    upstream fetch and make the two halves contend for the same advisory lock.
    """

    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        report = await RuleCollector(session, provider).sync(instance)
        return {
            "status": "skipped_locked" if report.skipped_locked else "ok",
            "rules_seen": report.rules_seen,
            "building_blocks": report.building_blocks,
            "created": report.created,
            "updated": report.updated,
            "unchanged": report.unchanged,
            "dependencies": report.dependencies_written,
            "transitions": report.transitions_recorded,
            "duration_ms": report.duration_ms,
            "error": report.error,
        }

    return await _for_each_instance(run, instance_name)


async def evaluate_rule_health(instance_name: str | None = None) -> dict:
    """Classify rule health. Needs no provider — it reads what we already stored."""

    async def run(
        session: AsyncSession, instance: QRadarInstance, _: QRadarProvider | None
    ) -> dict:
        report = await RuleHealthEvaluator(session).evaluate_instance(instance)
        return {
            "status": "ok",
            "evaluated": report.evaluated,
            "by_status": {str(k): v for k, v in report.by_status.items()},
        }

    return await _for_each_instance(run, instance_name, needs_provider=False)


async def evaluate_detection_coverage(instance_name: str | None = None) -> dict:
    async def run(
        session: AsyncSession, instance: QRadarInstance, _: QRadarProvider | None
    ) -> dict:
        report = await DetectionCoverageEvaluator(session).evaluate_instance(instance)
        return {
            "status": "ok",
            "techniques": report.techniques_evaluated,
            "by_status": {str(k): v for k, v in report.by_status.items()},
        }

    return await _for_each_instance(run, instance_name, needs_provider=False)


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


@celery_app.task(name="sync_log_sources")
def sync_log_sources_task(instance_name: str | None = None) -> dict:
    return _run(sync_log_sources(instance_name))


@celery_app.task(name="collect_offenses")
def collect_offenses_task(instance_name: str | None = None) -> dict:
    return _run(collect_offenses(instance_name))


@celery_app.task(name="sync_rule_inventory")
def sync_rule_inventory_task(instance_name: str | None = None) -> dict:
    return _run(sync_rule_inventory(instance_name))


@celery_app.task(name="evaluate_rule_health")
def evaluate_rule_health_task(instance_name: str | None = None) -> dict:
    return _run(evaluate_rule_health(instance_name))


@celery_app.task(name="evaluate_detection_coverage")
def evaluate_detection_coverage_task(instance_name: str | None = None) -> dict:
    return _run(evaluate_detection_coverage(instance_name))


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
