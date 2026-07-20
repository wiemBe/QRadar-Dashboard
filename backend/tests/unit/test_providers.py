"""Provider capability contracts, MCP allowlist, factory selection, TLS refusal."""

from __future__ import annotations

import pytest

from app.core.config import ProviderKind, Settings
from app.providers.base import CapabilityNotSupportedError, ProviderCapability
from app.providers.factory import build_provider
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import (
    MCPToolNotAllowedError,
    QRadarMCPProvider,
)
from app.providers.qradar_rest import QRadarRestProvider


def test_factory_returns_mock_for_mock_kind() -> None:
    s = Settings(qradar_provider=ProviderKind.MOCK, encryption_key="x" * 44)
    assert isinstance(build_provider(s), MockQRadarProvider)


def test_mcp_provider_lacks_aql_execution() -> None:
    m = QRadarMCPProvider(base_url="http://qradar-mcp:5000")
    assert not m.supports(ProviderCapability.AQL_EXECUTION)
    assert m.supports(ProviderCapability.INVENTORY)


def test_mcp_allowlist_blocks_write_tools() -> None:
    m = QRadarMCPProvider(base_url="http://qradar-mcp:5000")
    for write_tool in ("update_offense", "create_ariel_search", "delete_reference_set",
                       "add_to_reference_set"):
        with pytest.raises(MCPToolNotAllowedError):
            m._guard(write_tool)


def test_mcp_allowlist_permits_read_tools() -> None:
    m = QRadarMCPProvider(base_url="http://qradar-mcp:5000")
    for read_tool in ("list_log_sources", "get_offense", "list_rules", "get_system_info"):
        m._guard(read_tool)  # must not raise


@pytest.mark.asyncio
async def test_aql_on_incapable_provider_raises() -> None:
    m = QRadarMCPProvider(base_url="http://qradar-mcp:5000")
    with pytest.raises(CapabilityNotSupportedError):
        await m.create_ariel_search("SELECT * FROM events")


def test_rest_provider_refuses_disabled_tls() -> None:
    with pytest.raises(ValueError, match="verify_ssl"):
        QRadarRestProvider(
            base_url="https://qradar.example",
            sec_token="tok",
            verify_ssl=False,
        )


def test_rest_provider_requires_https() -> None:
    with pytest.raises(ValueError, match="https"):
        QRadarRestProvider(base_url="http://qradar.example", sec_token="tok")
