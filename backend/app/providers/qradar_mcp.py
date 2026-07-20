"""QRadarMCPProvider — inventory retrieval over the IBM qradar-mcp service.

Deliberately restricted:

  * Capabilities exclude AQL_EXECUTION. Running Ariel searches is a POST surface
    (`create_ariel_search`) that we keep off the MCP path entirely; the REST
    provider owns query execution. See docs/mcp-capability-matrix.md section 1.2.

  * A hardcoded read-only allowlist (`_ALLOWED_TOOLS`) is checked before every
    call, independent of what the MCP server advertises. Even if the mounted
    feature-toggle file were misconfigured to re-enable writes, this provider
    would still refuse to invoke a write tool. This is defence in depth layer 5
    from the capability matrix.

Fully implemented in Phase 3. The allowlist and capability declaration are the
security-relevant parts and land now.
"""

from __future__ import annotations

from app.providers.base import (
    ProviderCapability,
    QRadarProvider,
)
from app.providers.dto import (
    AnalyticsRuleDTO,
    InstanceInfoDTO,
    LogSourceDTO,
    LogSourceTypeDTO,
    OffenseDTO,
)

# Read-only GET tools only. Any tool not on this list cannot be invoked through
# this provider, full stop. Mirrors the "Primary/High" read rows of the matrix.
_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "get_system_info",
        "list_servers",
        "list_log_sources",
        "get_log_source",
        "list_log_source_types",
        "list_rules",
        "get_rule",
        "list_building_blocks",
        "list_offenses",
        "get_offense",
        "get_offense_notes",
        "list_offense_types",
        "list_offense_closing_reasons",
        "list_saved_searches",
        "get_saved_search",
        "list_users",
        "list_user_roles",
        "list_qid_records",
        "list_dsm_event_mappings",
    }
)


class MCPToolNotAllowedError(RuntimeError):
    """Raised if any code path attempts an MCP tool outside the allowlist."""


class QRadarMCPProvider(QRadarProvider):
    # Note: no AQL_EXECUTION. This is the whole point of the split.
    capabilities = frozenset(
        {
            ProviderCapability.INVENTORY,
            ProviderCapability.OFFENSES,
        }
    )

    def __init__(self, *, base_url: str, timeout: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _guard(self, tool_name: str) -> None:
        if tool_name not in _ALLOWED_TOOLS:
            raise MCPToolNotAllowedError(
                f"MCP tool {tool_name!r} is not on the read-only allowlist"
            )

    # Phase 3 implements the MCP JSON-RPC/SSE client behind these. Each concrete
    # method will call self._guard(tool_name) before dispatching.
    async def get_instance_info(self) -> InstanceInfoDTO:  # pragma: no cover - Phase 3
        raise NotImplementedError("QRadarMCPProvider is implemented in Phase 3")

    async def list_log_sources(self) -> list[LogSourceDTO]:  # pragma: no cover - Phase 3
        raise NotImplementedError("QRadarMCPProvider is implemented in Phase 3")

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:  # pragma: no cover
        raise NotImplementedError("QRadarMCPProvider is implemented in Phase 3")

    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:  # pragma: no cover
        raise NotImplementedError("QRadarMCPProvider is implemented in Phase 3")

    async def list_rules(self) -> list[AnalyticsRuleDTO]:  # pragma: no cover - Phase 3
        raise NotImplementedError("QRadarMCPProvider is implemented in Phase 3")

    async def list_offenses(  # pragma: no cover - Phase 3
        self, *, open_only: bool = True
    ) -> list[OffenseDTO]:
        raise NotImplementedError("QRadarMCPProvider is implemented in Phase 3")
