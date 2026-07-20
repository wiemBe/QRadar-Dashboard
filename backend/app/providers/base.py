"""Provider abstraction.

Three interchangeable backends implement `QRadarProvider`:

  * QRadarRestProvider — deterministic background collection and scheduled AQL
    execution. Owns the full Ariel search lifecycle.
  * QRadarMCPProvider  — inventory retrieval only, over the GET-only IBM MCP
    service, for future AI-agent investigations. Declines AQL execution.
  * MockQRadarProvider — deterministic in-memory data for development and tests.

The application depends on this interface and on `ProviderCapability`, never on
a concrete provider. Code asks "can this backend run AQL?" rather than "is this
REST?", so capability differences are handled explicitly instead of by isinstance
checks scattered through services.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum

from app.providers.dto import (
    AnalyticsRuleDTO,
    ArielSearchHandleDTO,
    ArielSearchResultsDTO,
    ArielSearchStatusDTO,
    InstanceInfoDTO,
    LogSourceDTO,
    LogSourceMetricSample,
    LogSourceTypeDTO,
    OffenseDTO,
)


class ProviderCapability(StrEnum):
    """What a given backend can actually do.

    MCP intentionally lacks AQL_EXECUTION: per the capability matrix, running
    Ariel searches is a POST surface we keep off the LLM-facing path and drive
    only through REST.
    """

    INVENTORY = "INVENTORY"          # log sources, rules, instance info
    OFFENSES = "OFFENSES"
    AQL_EXECUTION = "AQL_EXECUTION"  # create/poll/fetch Ariel searches
    CONFIG_SNAPSHOTS = "CONFIG_SNAPSHOTS"


class ProviderError(RuntimeError):
    """Base class for all provider failures."""


class ProviderUnavailableError(ProviderError):
    """Transient connectivity/timeout failure — safe to retry."""


class ProviderAuthError(ProviderError):
    """Authentication/authorization failure — not retryable."""


class CapabilityNotSupportedError(ProviderError):
    """The backend does not implement the requested capability."""


class QRadarProvider(ABC):
    """Read-only interface to a QRadar deployment.

    No method mutates QRadar state. There is intentionally no create/update/
    delete surface on this interface: the platform operates read-only, and the
    write tools the MCP server exposes are disabled by policy.
    """

    #: Concrete providers declare what they support.
    capabilities: frozenset[ProviderCapability] = frozenset()

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    def require(self, capability: ProviderCapability) -> None:
        if not self.supports(capability):
            raise CapabilityNotSupportedError(
                f"{type(self).__name__} does not support {capability}"
            )

    # -- lifecycle ----------------------------------------------------------
    async def aclose(self) -> None:
        """Release any held resources (HTTP clients). Default no-op."""

    # -- health / inventory -------------------------------------------------
    @abstractmethod
    async def get_instance_info(self) -> InstanceInfoDTO:
        ...

    @abstractmethod
    async def list_log_sources(self) -> list[LogSourceDTO]:
        ...

    @abstractmethod
    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        ...

    @abstractmethod
    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:
        ...

    async def get_log_source_metrics(
        self, window_start: datetime, window_end: datetime
    ) -> list[LogSourceMetricSample]:
        """Per-log-source metric samples for one collection interval.

        Requires INVENTORY. The REST provider derives these from AQL aggregates
        (Phase 3); the mock synthesizes them deterministically.
        """
        self.require(ProviderCapability.INVENTORY)
        raise NotImplementedError

    @abstractmethod
    async def list_rules(self) -> list[AnalyticsRuleDTO]:
        ...

    async def list_building_blocks(self) -> list[AnalyticsRuleDTO]:
        """Building blocks, where the backend exposes them separately.

        QRadar returns building blocks inside `list_rules` as well; a backend
        with no distinct endpoint may return an empty list rather than fail.
        """
        self.require(ProviderCapability.INVENTORY)
        return []

    async def get_rule(self, qradar_id: int) -> AnalyticsRuleDTO | None:
        self.require(ProviderCapability.INVENTORY)
        raise NotImplementedError

    async def validate_connection(self) -> InstanceInfoDTO:
        """Prove credentials, TLS and reachability with one cheap call."""
        return await self.get_instance_info()

    # -- offenses -----------------------------------------------------------
    @abstractmethod
    async def list_offenses(
        self,
        *,
        open_only: bool = True,
        updated_since: datetime | None = None,
        max_pages: int | None = None,
    ) -> list[OffenseDTO]:
        """Offenses, optionally narrowed to those updated since a watermark.

        `updated_since` is what makes collection incremental; a backend that
        cannot filter server-side must still honour it by filtering locally, so
        callers can rely on the result never predating the watermark.
        """

    async def get_offense(self, qradar_id: int) -> OffenseDTO | None:
        self.require(ProviderCapability.OFFENSES)
        raise NotImplementedError

    async def list_offense_types(self) -> dict[int, str]:
        self.require(ProviderCapability.OFFENSES)
        return {}

    async def list_offense_closing_reasons(self) -> dict[int, str]:
        self.require(ProviderCapability.OFFENSES)
        return {}

    # -- AQL / Ariel (guarded by AQL_EXECUTION capability) ------------------
    async def create_ariel_search(self, aql: str) -> ArielSearchHandleDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def get_ariel_search_results(
        self, search_id: str, *, max_rows: int
    ) -> ArielSearchResultsDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError

    async def cancel_ariel_search(self, search_id: str) -> None:
        self.require(ProviderCapability.AQL_EXECUTION)
        raise NotImplementedError
