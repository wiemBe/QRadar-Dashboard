"""Provider factory — selects the backend from configuration.

The rest of the application obtains a `QRadarProvider` from here and never
imports a concrete provider class directly.
"""

from __future__ import annotations

from app.core.config import ProviderKind, Settings, get_settings
from app.providers.base import QRadarProvider
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from app.providers.qradar_rest import QRadarRestProvider


def build_provider(settings: Settings | None = None) -> QRadarProvider:
    settings = settings or get_settings()

    match settings.qradar_provider:
        case ProviderKind.MOCK:
            return MockQRadarProvider()
        case ProviderKind.REST:
            return QRadarRestProvider(
                base_url=settings.qradar_host,
                sec_token=settings.qradar_sec_token.get_secret_value(),
                api_version=settings.qradar_api_version,
                verify_ssl=settings.qradar_verify_ssl,
                ca_bundle=settings.qradar_ca_bundle,
            )
        case ProviderKind.MCP:
            return QRadarMCPProvider(
                base_url=settings.mcp_base_url,
                timeout=settings.mcp_timeout_seconds,
            )

    raise ValueError(f"Unknown provider kind: {settings.qradar_provider}")


# FastAPI dependency. Kept module-level so it can be overridden in tests via
# app.dependency_overrides.
async def get_provider() -> QRadarProvider:
    provider = build_provider()
    try:
        yield provider
    finally:
        await provider.aclose()
