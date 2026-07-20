"""Inventory sync against a real database. Skips without TEST_DATABASE_URL."""

from __future__ import annotations

import pytest

from app.providers.mock import MockQRadarProvider
from app.repositories.log_source import LogSourceRepository
from app.services.inventory_sync import InventorySyncService

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
