"""Anomaly engine: hysteresis open/resolve, dedup, flapping prevention,
maintenance suppression, and the alert lifecycle it drives. DB-gated."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.alerts.fingerprint import anomaly_fingerprint
from app.anomaly.engine import AnomalyEngine
from app.core.config import Settings
from app.models.alert import Alert
from app.models.enums import AlertStatus, AnomalyType, Criticality
from app.models.log_source import LogSourceAnomaly, LogSourceBaseline
from tests.integration.factories import add_metric, make_instance, make_log_source, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings() -> Settings:
    return Settings(encryption_key="x" * 44, anomaly_deviation_threshold=3.5)


async def _reliable_eps_baseline(session, ls, *, weekday=2, hour=10, median=50.0):
    # utc() default 2026-07-15 is a Wednesday -> isoweekday 3. Provide baseline
    # for the hour we feed. weekday param kept explicit for clarity.
    session.add(
        LogSourceBaseline(
            log_source_id=ls.id, metric_name="average_eps", weekday=weekday, hour=hour,
            median=median, mad=2.0, p05=median * 0.9, p95=median * 1.1,
            sample_count=30, is_reliable=True, baseline_version=1, observations=[median],
        )
    )
    await session.flush()


async def _feed(engine, ls, session, *, start, count, eps, sig_prefix="s", step_min=5):
    """Feed `count` consecutive intervals and evaluate each."""
    reports = []
    for i in range(count):
        bucket = start + timedelta(minutes=step_min * i)
        m = await add_metric(
            session, ls, bucket, average_eps=eps, event_count=int(eps * 300),
            payload_signature=f"{sig_prefix}-{i}",
        )
        reports.append(await engine.evaluate_interval(ls, m))
    return reports


async def _open_alert(session, ls, atype: AnomalyType) -> Alert | None:
    return await session.scalar(
        select(Alert).where(
            Alert.dedup_key == anomaly_fingerprint(ls.id, atype.value),
            Alert.status != AlertStatus.RESOLVED,
        )
    )


async def test_opens_after_n_consecutive_anomalies(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, criticality=Criticality.MEDIUM)
    start = utc(h=10)
    weekday = start.isoweekday()
    await _reliable_eps_baseline(db_session, ls, weekday=weekday, hour=10)

    engine = AnomalyEngine(db_session, settings=_settings())
    # MEDIUM opens after 2 consecutive anomalous intervals.
    await _feed(engine, ls, db_session, start=start, count=1, eps=3.0)
    assert await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP) is None  # not yet
    await _feed(engine, ls, db_session,
                start=start + timedelta(minutes=5), count=1, eps=3.0)
    alert = await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP)
    assert alert is not None and alert.status == AlertStatus.OPEN


async def test_dedup_bumps_occurrence_not_new_alert(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    await _feed(engine, ls, db_session, start=start, count=5, eps=3.0)
    alerts = await db_session.scalar(
        select(func.count()).select_from(Alert).where(
            Alert.dedup_key == anomaly_fingerprint(ls.id, AnomalyType.VOLUME_DROP.value)
        )
    )
    assert alerts == 1  # exactly one alert despite 5 anomalous intervals
    alert = await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP)
    assert alert.occurrence_count >= 2


async def test_resolves_after_m_healthy_intervals(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, criticality=Criticality.MEDIUM)
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    await _feed(engine, ls, db_session, start=start, count=2, eps=3.0)
    assert await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP) is not None

    # MEDIUM resolves after 3 consecutive healthy intervals.
    await _feed(engine, ls, db_session,
                start=start + timedelta(minutes=10), count=3, eps=50.0)
    assert await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP) is None
    # The anomaly row is resolved too.
    anomaly = await db_session.scalar(
        select(LogSourceAnomaly).where(
            LogSourceAnomaly.log_source_id == ls.id,
            LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_DROP,
        )
    )
    assert anomaly.resolved_at is not None


async def test_flapping_never_opens(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, criticality=Criticality.MEDIUM)
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    # Alternate anomalous / healthy: never 2 consecutive anomalies -> never opens.
    for i in range(8):
        bucket = start + timedelta(minutes=5 * i)
        eps = 3.0 if i % 2 == 0 else 50.0
        m = await add_metric(db_session, ls, bucket, average_eps=eps,
                             event_count=int(eps * 300), payload_signature=f"f-{i}")
        await engine.evaluate_interval(ls, m)

    assert await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP) is None


async def test_maintenance_suppresses_alert(db_session) -> None:
    from datetime import UTC, datetime

    inst = await make_instance(db_session)
    ls = await make_log_source(
        db_session, inst, maintenance_mode=True,
        maintenance_until=datetime(2027, 1, 1, tzinfo=UTC),
    )
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    await _feed(engine, ls, db_session, start=start, count=5, eps=3.0)
    # No alert during maintenance, even though the condition holds.
    assert await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP) is None


async def test_idempotent_reevaluation(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    m = await add_metric(db_session, ls, start, average_eps=3.0, event_count=900,
                         payload_signature="i-0")
    await engine.evaluate_interval(ls, m)
    # Re-evaluating the same interval must not double-count the hysteresis.
    await engine.evaluate_interval(ls, m)
    await engine.evaluate_interval(ls, m)

    from app.models.log_source import LogSourceDetectorState
    state = await db_session.scalar(
        select(LogSourceDetectorState).where(
            LogSourceDetectorState.log_source_id == ls.id,
            LogSourceDetectorState.anomaly_type == AnomalyType.VOLUME_DROP,
        )
    )
    assert state.consecutive_anomalous == 1  # not 3
