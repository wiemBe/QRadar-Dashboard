"""MockQRadarProvider behaviour and determinism."""

from __future__ import annotations

import pytest

from app.providers.base import ProviderCapability
from app.providers.mock import MockQRadarProvider

pytestmark = pytest.mark.asyncio


async def test_all_capabilities_supported(mock_provider: MockQRadarProvider) -> None:
    for cap in ProviderCapability:
        assert mock_provider.supports(cap)


async def test_log_sources_are_deterministic() -> None:
    a = await MockQRadarProvider(seed=1337).list_log_sources()
    b = await MockQRadarProvider(seed=1337).list_log_sources()
    assert [s.average_eps for s in a] == [s.average_eps for s in b]
    assert [s.qradar_id for s in a] == [s.qradar_id for s in b]


async def test_different_seed_changes_jitter() -> None:
    a = await MockQRadarProvider(seed=1).list_log_sources()
    b = await MockQRadarProvider(seed=2).list_log_sources()
    assert [s.average_eps for s in a] != [s.average_eps for s in b]


async def test_includes_an_unhealthy_silent_source(mock_provider: MockQRadarProvider) -> None:
    sources = await mock_provider.list_log_sources()
    silent = [s for s in sources if s.enabled and s.average_eps == 0.0]
    assert silent, "mock must include a silent-but-enabled source for the detector"


async def test_offenses_include_unassigned(mock_provider: MockQRadarProvider) -> None:
    offenses = await mock_provider.list_offenses(open_only=True)
    assert all(o.status == "OPEN" for o in offenses)
    assert any(o.assigned_to is None for o in offenses)


async def test_ariel_lifecycle_completes(mock_provider: MockQRadarProvider) -> None:
    aql = "SELECT sourceip, COUNT(*) FROM events GROUP BY sourceip LAST 1 HOURS"
    handle = await mock_provider.create_ariel_search(aql)
    status = await mock_provider.get_ariel_search_status(handle.search_id)
    assert status.is_terminal and status.is_success

    results = await mock_provider.get_ariel_search_results(handle.search_id, max_rows=100)
    assert results.total_count == status.record_count
    assert not results.truncated


async def test_ariel_results_respect_max_rows() -> None:
    # A query that produces several rows, truncated to 1.
    provider = MockQRadarProvider(seed=1337)
    # Find an AQL that yields >1 row deterministically.
    aql = "SELECT a FROM events GROUP BY a"  # digest-driven row count
    handle = await provider.create_ariel_search(aql)
    full = await provider.get_ariel_search_results(handle.search_id, max_rows=100)
    if full.total_count > 1:
        truncated = await provider.get_ariel_search_results(handle.search_id, max_rows=1)
        assert len(truncated.rows) == 1
        assert truncated.truncated is True


async def test_unknown_search_id_returns_error(mock_provider: MockQRadarProvider) -> None:
    status = await mock_provider.get_ariel_search_status("does-not-exist")
    assert status.status == "ERROR"
    assert not status.is_success
