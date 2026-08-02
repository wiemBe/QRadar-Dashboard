"""Inventory sync against a real database. Skips without TEST_DATABASE_URL."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.enums import AnomalyState, AnomalyType, EvidenceStatus, Severity
from app.models.log_source import LogSourceAnomaly
from app.providers.mock import MockQRadarProvider
from app.repositories.log_source import LogSourceRepository
from app.services.inventory_sync import InventorySyncService

from .factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_sync_creates_then_updates(db_session) -> None:
    provider = MockQRadarProvider(seed=1337)
    svc = InventorySyncService(db_session, provider)
    instance = await svc.ensure_default_instance()

    first = await svc.sync(instance)
    assert first.created > 0
    assert first.updated == 0
    assert first.log_sources_seen == first.created

    # Second sync is idempotent: nothing new created, everything updated.
    second = await svc.sync(instance)
    assert second.created == 0
    assert second.updated == first.created


async def test_sync_preserves_soc_metadata(db_session) -> None:
    provider = MockQRadarProvider(seed=1337)
    svc = InventorySyncService(db_session, provider)
    instance = await svc.ensure_default_instance()
    await svc.sync(instance)

    repo = LogSourceRepository(db_session)
    sources = await repo.list()
    target = sources[0]
    target.owner = "soc-owner"
    target.criticality = "CRITICAL"
    target.maintenance_mode = True
    await db_session.flush()

    # A re-sync must not clobber operator-owned fields.
    await svc.sync(instance)
    await db_session.refresh(target)
    assert target.owner == "soc-owner"
    assert str(target.criticality) == "CRITICAL"
    assert target.maintenance_mode is True


async def test_sync_sanitizes_source_names(db_session) -> None:
    class XssProvider(MockQRadarProvider):
        async def list_log_sources(self):
            sources = await super().list_log_sources()
            first = sources[0].model_copy(update={"name": "<script>evil</script>fw-01"})
            return [first, *sources[1:]]

    svc = InventorySyncService(db_session, XssProvider())
    instance = await svc.ensure_default_instance()
    await svc.sync(instance)

    repo = LogSourceRepository(db_session)
    names = [s.name for s in await repo.list()]
    assert all("<script>" not in n for n in names)


class TestActiveAnomalyPredicate:
    """An incident is active by lifecycle state, never by `resolved_at IS NULL`.

    A CANDIDATE that returns to normal before opening never opened, so it is
    never resolved and keeps `resolved_at` NULL permanently. Counting that row
    as active reports a live incident that no longer exists -- observed against
    the lab appliance on 2026-08-02, where an idle gap between scenarios left
    exactly such a row on a source with no live incident.
    """

    @staticmethod
    def _anomaly(src, state: AnomalyState, **kwargs) -> LogSourceAnomaly:
        return LogSourceAnomaly(
            log_source_id=src.id,
            anomaly_type=AnomalyType.VOLUME_DROP,
            severity=Severity.MEDIUM,
            state=state,
            detected_at=datetime(2026, 8, 2, 20, 18, tzinfo=UTC),
            anomaly_start=datetime(2026, 8, 2, 20, 18, tzinfo=UTC),
            consecutive_buckets=1,
            policy_version=1,
            evidence_status=EvidenceStatus.NOT_REQUESTED,
            **kwargs,
        )

    @pytest.mark.asyncio
    async def test_unopened_candidate_returned_to_normal_is_not_active(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        src = await make_log_source(db_session, instance, qradar_id=9001, name="idle")
        # state NORMAL, resolved_at NULL: it never opened, so it never resolved.
        db_session.add(self._anomaly(src, AnomalyState.NORMAL))
        await db_session.flush()

        repo = LogSourceRepository(db_session)

        assert await repo.open_anomaly_counts() == {}
        assert await repo.open_anomalies_for(src.id) == []
        assert await repo.anomalous_source_count() == 0

    @pytest.mark.asyncio
    async def test_live_states_are_active(self, db_session) -> None:
        instance = await make_instance(db_session)
        repo = LogSourceRepository(db_session)

        for offset, state in enumerate(
            (AnomalyState.CANDIDATE, AnomalyState.OPEN, AnomalyState.RECOVERING)
        ):
            src = await make_log_source(
                db_session, instance, qradar_id=9100 + offset, name=f"live-{offset}"
            )
            db_session.add(self._anomaly(src, state))
            await db_session.flush()

            assert await repo.open_anomaly_counts() == {src.id: 1}, state
            assert len(await repo.open_anomalies_for(src.id)) == 1, state
            assert await repo.anomalous_source_count() == 1, state

            await db_session.delete(
                (await repo.open_anomalies_for(src.id))[0]
            )
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_resolved_incident_is_not_active(self, db_session) -> None:
        instance = await make_instance(db_session)
        src = await make_log_source(db_session, instance, qradar_id=9200, name="done")
        db_session.add(
            self._anomaly(
                src,
                AnomalyState.RESOLVED,
                resolved_at=datetime(2026, 8, 2, 20, 25, tzinfo=UTC),
            )
        )
        await db_session.flush()

        repo = LogSourceRepository(db_session)
        assert await repo.open_anomaly_counts() == {}
        assert await repo.anomalous_source_count() == 0
