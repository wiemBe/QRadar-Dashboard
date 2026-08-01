"""Celery task entrypoints for background collection, detection and delivery.

Each task is a thin synchronous wrapper that runs an async orchestration on a
fresh event loop with its own AsyncSession. The orchestration functions are
also importable and awaited directly by the APScheduler MVP fallback and by
tests, so the scheduling mechanism and the work are decoupled.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import compute_fingerprint
from app.alerts.notifiers.registry import build_notifiers
from app.alerts.routing import default_policy
from app.alerts.search_alerts import SearchAlertEvaluator
from app.alerts.service import AlertInput, AlertService
from app.anomaly.baseline import BaselineBuilder
from app.anomaly.engine import EXPLAINABLE_TYPES, AnomalyEngine
from app.collectors.explanation_collector import (
    EXPLANATION_COLLECTOR,
    ExplanationCollector,
)
from app.collectors.log_source_collector import LogSourceCollector
from app.collectors.metric_collector import MetricCollector
from app.collectors.offense_collector import OffenseCollector
from app.collectors.rule_collector import RuleCollector
from app.collectors.rule_metric_collector import RuleMetricCollector
from app.core.config import get_settings
from app.core.database import dispose_engine, get_sessionmaker
from app.models.enums import EvidenceStatus, Severity
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.monitoring import CollectionWatermark
from app.providers.base import QRadarProvider
from app.providers.factory import build_provider, build_provider_for_instance
from app.repositories.offense import OffenseRepository
from app.services.concurrency import InMemoryConcurrencyLimiter
from app.services.detection_coverage import DetectionCoverageEvaluator
from app.services.locks import CollectorAdvisoryLock
from app.services.rule_health import RuleHealthEvaluator
from app.services.search_executor import SearchExecutor
from app.services.search_scheduler import SearchScheduler
from app.workers.celery_app import celery_app

logger = logging.getLogger("app.tasks")


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    async def run_and_dispose() -> T:
        try:
            return await coro
        finally:
            # Every Celery shim owns a short-lived event loop. Close asyncpg
            # connections on that same loop so none leak into the next task.
            await dispose_engine()

    return asyncio.run(run_and_dispose())


# ---------------------------------------------------------------- collection
async def collect_metrics(instance_name: str | None = None) -> dict:
    """Collect log-source metrics independently for every enabled instance."""

    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        report = await MetricCollector(session, provider).collect(instance)
        return {
            "status": "skipped_locked" if report.skipped_locked else "ok",
            "intervals": report.intervals_collected,
            "samples": report.samples_written,
            "watermark_at": report.watermark_at,
            "lag_seconds": report.lag_seconds,
        }

    return await _for_each_instance(run, instance_name)


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
            provider = (
                # audit_session wires the MCP audit sink to AuditLog; it is
                # inert for the REST provider, which audits via telemetry.
                build_provider_for_instance(instance, audit_session=session)
                if needs_provider
                else None
            )
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
            "status": (
                "skipped_locked"
                if report.skipped_locked
                else "failed" if report.partial_failure else "ok"
            ),
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
            "status": (
                "skipped_locked"
                if report.skipped_locked
                else "failed" if report.partial_failure else "ok"
            ),
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
            "status": (
                "skipped_locked"
                if report.skipped_locked
                else "failed" if report.partial_failure else "ok"
            ),
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


async def collect_rule_metrics(instance_name: str | None = None) -> dict:
    """Collect honest lower-bound firing evidence from stored offenses."""

    async def run(
        session: AsyncSession, instance: QRadarInstance, _: QRadarProvider | None
    ) -> dict:
        report = await RuleMetricCollector(session).collect(instance)
        return {
            "status": (
                "skipped_locked"
                if report.skipped_locked
                else "failed" if report.partial_failure else "ok"
            ),
            "offenses_seen": report.offenses_seen,
            "rules_matched": report.rules_matched,
            "metrics_written": report.metrics_written,
            "contributions": report.contributions,
            "unmatched_rule_ids": report.unmatched_rule_ids,
            "completeness": "incomplete",
            "provenance": "offense_contribution",
            "duration_ms": report.duration_ms,
            "error": report.error,
        }

    return await _for_each_instance(run, instance_name, needs_provider=False)


async def detect_stale_offense_collection(instance_name: str | None = None) -> dict:
    """Open/resolve one deduplicated alert per stale offense collector."""

    async def run(
        session: AsyncSession, instance: QRadarInstance, _: QRadarProvider | None
    ) -> dict:
        settings = get_settings()
        started = time.perf_counter()
        async with CollectorAdvisoryLock(
            session, settings, instance.id, "offense_stale_detection"
        ) as acquired:
            if not acquired:
                return {"status": "skipped_locked", "duration_ms": 0}

            now = datetime.now(UTC)
            watermark = await session.scalar(
                select(CollectionWatermark).where(
                    CollectionWatermark.instance_id == instance.id,
                    CollectionWatermark.collector == "offense_snapshot",
                )
            )
            last_run = watermark.last_run_at if watermark is not None else None
            if last_run is not None and last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=UTC)
            age_seconds = (
                int((now - last_run).total_seconds()) if last_run is not None else None
            )
            failures = watermark.consecutive_failures if watermark is not None else 0
            stale = (
                last_run is None
                or age_seconds is None
                or age_seconds > settings.offense_stale_after_seconds
                or failures > 0
            )

            fingerprint = compute_fingerprint(
                source_type="qradar_instance",
                source_id=instance.id,
                condition="offense_collection_stale",
            )
            alerts = AlertService(session)
            dispatcher = NotificationDispatcher(
                session, notifiers={}, policy=default_policy()
            )
            transition = None
            if stale:
                result = await alerts.open_or_update(
                    AlertInput(
                        fingerprint=fingerprint,
                        title=f"Offense collection is stale for {instance.name}",
                        description=(
                            "The read-only offense collector has not completed within "
                            "its configured freshness threshold."
                        ),
                        severity=Severity.HIGH,
                        source_type="qradar_instance",
                        source_id=instance.id,
                        evidence={
                            "last_run_at": last_run.isoformat() if last_run else None,
                            "age_seconds": age_seconds,
                            "threshold_seconds": settings.offense_stale_after_seconds,
                            "consecutive_failures": failures,
                        },
                    )
                )
                transition = result.transition
                if transition is not None:
                    await dispatcher.enqueue(result.alert, transition)
            else:
                resolved = await alerts.resolve_by_fingerprint(
                    fingerprint,
                    actor="system:offense-collection-monitor",
                    reason="Offense collection freshness recovered.",
                )
                if resolved is not None and resolved.transition is not None:
                    transition = resolved.transition
                    await dispatcher.enqueue(resolved.alert, resolved.transition)

            return {
                "status": "ok",
                "stale": stale,
                "age_seconds": age_seconds,
                "consecutive_failures": failures,
                "transition": str(transition) if transition is not None else None,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }

    return await _for_each_instance(run, instance_name, needs_provider=False)


async def calculate_offense_aggregates(instance_name: str | None = None) -> dict:
    """Calculate bounded current-offense aggregates for cache warming/monitoring."""

    async def run(
        session: AsyncSession, instance: QRadarInstance, _: QRadarProvider | None
    ) -> dict:
        settings = get_settings()
        started = time.perf_counter()
        async with CollectorAdvisoryLock(
            session, settings, instance.id, "offense_aggregate"
        ) as acquired:
            if not acquired:
                return {"status": "skipped_locked", "duration_ms": 0}
            aggregates = await OffenseRepository(session).aggregate_metrics(
                instance.id,
                sla_hours=settings.offense_sla_hours,
                critical_magnitude=settings.offense_critical_magnitude,
            )
            return {
                "status": "ok",
                "processed": aggregates["active"],
                "duration_ms": int((time.perf_counter() - started) * 1000),
                **aggregates,
            }

    return await _for_each_instance(run, instance_name, needs_provider=False)


async def evaluate_rule_health(instance_name: str | None = None) -> dict:
    """Classify rule health. Needs no provider — it reads what we already stored."""

    async def run(
        session: AsyncSession, instance: QRadarInstance, _: QRadarProvider | None
    ) -> dict:
        settings = get_settings()
        async with CollectorAdvisoryLock(
            session, settings, instance.id, "rule_health"
        ) as acquired:
            if not acquired:
                return {"status": "skipped_locked", "evaluated": 0}
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
        settings = get_settings()
        async with CollectorAdvisoryLock(
            session, settings, instance.id, "detection_coverage"
        ) as acquired:
            if not acquired:
                return {"status": "skipped_locked", "techniques": 0}
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


# -------------------------------------------------------- anomaly explanation
async def collect_anomaly_explanations(instance_name: str | None = None) -> dict:
    """Build bounded evidence packages for anomalies awaiting explanation.

    Runs separately from detection so an unresponsive appliance delays evidence
    rather than delaying alerts, and holds a per-instance advisory lock so two
    workers cannot both hammer Ariel with the same investigation queries.
    """
    settings = get_settings()
    if not settings.explanation_enabled:
        return {"status": "disabled", "instances": 0, "results": []}

    async def run(
        session: AsyncSession, instance: QRadarInstance, provider: QRadarProvider | None
    ) -> dict:
        assert provider is not None
        async with CollectorAdvisoryLock(
            session, settings, instance.id, EXPLANATION_COLLECTOR
        ) as acquired:
            if not acquired:
                return {"status": "locked", "explained": 0}

            pending = list(
                (
                    await session.scalars(
                        select(LogSourceAnomaly)
                        .join(LogSource, LogSource.id == LogSourceAnomaly.log_source_id)
                        .where(
                            LogSource.instance_id == instance.id,
                            LogSourceAnomaly.evidence_status == EvidenceStatus.PENDING,
                            LogSourceAnomaly.anomaly_type.in_(
                                [t.value for t in EXPLAINABLE_TYPES]
                            ),
                        )
                        # Oldest first: an anomaly whose window is about to age
                        # out of Ariel retention is the one that cannot wait.
                        .order_by(LogSourceAnomaly.detected_at)
                        .limit(settings.explanation_max_per_run)
                    )
                ).all()
            )

            collector = ExplanationCollector(session, provider, settings=settings)
            statuses: dict[str, int] = {}
            for anomaly in pending:
                report = await collector.collect(anomaly)
                key = report.status.value
                statuses[key] = statuses.get(key, 0) + 1
            return {
                "status": "ok",
                "explained": len(pending),
                "by_status": statuses,
            }

    return await _for_each_instance(run, instance_name)


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
def collect_metrics_task(instance_name: str | None = None) -> dict:
    return _run(collect_metrics(instance_name))


@celery_app.task(name="sync_log_sources")
def sync_log_sources_task(instance_name: str | None = None) -> dict:
    return _run(sync_log_sources(instance_name))


@celery_app.task(name="collect_offenses")
def collect_offenses_task(instance_name: str | None = None) -> dict:
    return _run(collect_offenses(instance_name))


@celery_app.task(name="sync_rule_inventory")
def sync_rule_inventory_task(instance_name: str | None = None) -> dict:
    return _run(sync_rule_inventory(instance_name))


@celery_app.task(name="collect_rule_metrics")
def collect_rule_metrics_task(instance_name: str | None = None) -> dict:
    return _run(collect_rule_metrics(instance_name))


@celery_app.task(name="detect_stale_offense_collection")
def detect_stale_offense_collection_task(instance_name: str | None = None) -> dict:
    return _run(detect_stale_offense_collection(instance_name))


@celery_app.task(name="calculate_offense_aggregates")
def calculate_offense_aggregates_task(instance_name: str | None = None) -> dict:
    return _run(calculate_offense_aggregates(instance_name))


@celery_app.task(name="evaluate_rule_health")
def evaluate_rule_health_task(instance_name: str | None = None) -> dict:
    return _run(evaluate_rule_health(instance_name))


@celery_app.task(name="evaluate_detection_coverage")
def evaluate_detection_coverage_task(instance_name: str | None = None) -> dict:
    return _run(evaluate_detection_coverage(instance_name))


@celery_app.task(name="evaluate_anomalies")
def evaluate_anomalies_task() -> dict:
    return _run(evaluate_anomalies())


@celery_app.task(name="collect_anomaly_explanations")
def collect_anomaly_explanations_task(instance_name: str | None = None) -> dict:
    return _run(collect_anomaly_explanations(instance_name))


@celery_app.task(name="rebuild_baselines")
def rebuild_baselines_task() -> dict:
    return _run(rebuild_baselines())


@celery_app.task(name="run_due_searches")
def run_due_searches_task() -> dict:
    return _run(run_due_searches())


@celery_app.task(name="dispatch_notifications")
def dispatch_notifications_task() -> dict:
    return _run(dispatch_notifications())
