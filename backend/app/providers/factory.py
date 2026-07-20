"""Provider factory — selects the backend from configuration.

The rest of the application obtains a `QRadarProvider` from here and never
imports a concrete provider class directly.
"""

from __future__ import annotations

from app.core.config import ProviderKind, Settings, get_settings
from app.models.instance import QRadarInstance
from app.providers.base import QRadarProvider
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from app.providers.qradar_rest import QRadarRestProvider


def build_provider(settings: Settings | None = None) -> QRadarProvider:
    """Build the process-wide default provider from configuration.

    Used where no particular instance is in scope. Collection runs per instance
    and should use `build_provider_for_instance` instead.
    """
    settings = settings or get_settings()

    match settings.qradar_provider:
        case ProviderKind.MOCK:
            return MockQRadarProvider()
        case ProviderKind.REST:
            return QRadarRestProvider(
                base_url=settings.qradar_host,
                sec_token=settings.resolve_qradar_token().get_secret_value(),
                api_version=settings.qradar_api_version,
                verify_ssl=settings.qradar_verify_ssl,
                ca_bundle=settings.qradar_ca_bundle,
                tls_allow_missing_aki=settings.qradar_tls_allow_missing_aki,
            )
        case ProviderKind.MCP:
            return QRadarMCPProvider(
                base_url=settings.mcp_base_url,
                timeout=settings.mcp_timeout_seconds,
            )

    raise ValueError(f"Unknown provider kind: {settings.qradar_provider}")


def build_provider_for_instance(
    instance: QRadarInstance,
    settings: Settings | None = None,
) -> QRadarProvider:
    """Build a provider bound to one registered QRadar instance.

    Connection details come from the stored row — console host, API version, CA
    path and the encrypted SEC token — so a deployment can monitor several
    consoles with different credentials. Global settings supply only
    deployment-wide policy (timeouts, retry budget, TLS strictness) that is not
    a property of any single console.

    The token is decrypted by the `EncryptedString` column type on read; it is
    handed straight to the provider and never returned to a caller.
    """
    settings = settings or get_settings()
    kind = ProviderKind(instance.provider_kind)

    match kind:
        case ProviderKind.MOCK:
            return MockQRadarProvider()
        case ProviderKind.REST:
            token = instance.sec_token
            if not token:
                raise ValueError(
                    f"QRadar instance '{instance.name}' has no stored SEC token; "
                    "re-register it with a token file."
                )
            return QRadarRestProvider(
                base_url=instance.console_host,
                sec_token=token,
                api_version=instance.api_version,
                verify_ssl=instance.verify_ssl,
                ca_bundle=instance.ca_bundle_path,
                tls_allow_missing_aki=settings.qradar_tls_allow_missing_aki,
                max_retries=settings.ariel_max_retries,
                retry_base_seconds=settings.ariel_retry_base_seconds,
                retry_max_seconds=settings.ariel_retry_max_seconds,
            )
        case ProviderKind.MCP:
            return QRadarMCPProvider(
                base_url=instance.mcp_base_url or settings.mcp_base_url,
                timeout=settings.mcp_timeout_seconds,
            )

    raise ValueError(f"Unknown provider kind: {instance.provider_kind}")


# FastAPI dependency. Kept module-level so it can be overridden in tests via
# app.dependency_overrides.
async def get_provider() -> QRadarProvider:
    provider = build_provider()
    try:
        yield provider
    finally:
        await provider.aclose()
