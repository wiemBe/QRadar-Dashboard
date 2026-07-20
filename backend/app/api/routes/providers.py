"""Provider capability and telemetry endpoint.

Exists so the REST-versus-MCP choice is *explicit and observable* rather than
something an operator infers from a config file. It reports which backend is
active, what it can do, where AQL execution runs, and — importantly — the MCP
allowlist and the write tools that remain blocked.

Nothing here exposes a credential: no host, no token, no CA path. The response
is limited to capability facts and call counters.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import ProviderDep, SettingsDep
from app.providers.base import ProviderCapability
from app.providers.qradar_mcp import ALLOWED_TOOLS, KNOWN_WRITE_TOOLS
from app.providers.telemetry import PROVIDER_METRICS
from app.schemas.rule import ProviderCapabilityOut

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("/capabilities", response_model=ProviderCapabilityOut)
async def provider_capabilities(
    provider: ProviderDep, settings: SettingsDep
) -> ProviderCapabilityOut:
    supports_aql = provider.supports(ProviderCapability.AQL_EXECUTION)
    return ProviderCapabilityOut(
        provider_kind=str(settings.qradar_provider),
        provider_class=type(provider).__name__,
        capabilities=sorted(str(c) for c in provider.capabilities),
        # AQL never runs over MCP. If the active provider cannot execute AQL,
        # say so plainly rather than implying a fallback exists.
        aql_execution_path="rest/ariel" if supports_aql else "unavailable (MCP is read-only)",
        mcp_enabled=settings.mcp_enabled,
        mcp_allowlisted_tools=sorted(ALLOWED_TOOLS),
        mcp_blocked_write_tools=sorted(KNOWN_WRITE_TOOLS),
        call_metrics=PROVIDER_METRICS.snapshot(),
    )
