"""QRadarRestProvider — direct QRadar REST + Ariel APIs.

Owns deterministic background collection and the full scheduled-AQL lifecycle.
Fully fleshed out in Phase 3; the class, capabilities and TLS-hardened client
construction land now so the factory and configuration wiring are testable.

Security posture baked in here:
  * TLS verification is always on. `verify_ssl=False` is refused rather than
    honoured — a privileged SIEM token must never cross an unverified channel.
  * The SEC token is attached per-request from the encrypted store and never
    logged.
"""

from __future__ import annotations

import ssl

import httpx

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


class QRadarRestProvider(QRadarProvider):
    capabilities = frozenset(
        {
            ProviderCapability.INVENTORY,
            ProviderCapability.OFFENSES,
            ProviderCapability.AQL_EXECUTION,
            ProviderCapability.CONFIG_SNAPSHOTS,
        }
    )

    def __init__(
        self,
        *,
        base_url: str,
        sec_token: str,
        api_version: str = "20.0",
        verify_ssl: bool = True,
        ca_bundle: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not verify_ssl:
            # Hard failure, not a warning. See config._production_hardening.
            raise ValueError(
                "QRadarRestProvider refuses verify_ssl=False; TLS verification is mandatory."
            )
        if not base_url.startswith("https://"):
            raise ValueError("QRadar base_url must be https://")

        verify: ssl.SSLContext | str | bool = ca_bundle if ca_bundle else True
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api",
            headers={
                "SEC": sec_token,
                "Version": api_version,
                "Accept": "application/json",
            },
            verify=verify,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # Phase 3 implements these against the endpoints documented in the
    # capability matrix. Declared now so the interface contract is complete.
    async def get_instance_info(self) -> InstanceInfoDTO:  # pragma: no cover - Phase 3
        raise NotImplementedError("QRadarRestProvider is implemented in Phase 3")

    async def list_log_sources(self) -> list[LogSourceDTO]:  # pragma: no cover - Phase 3
        raise NotImplementedError("QRadarRestProvider is implemented in Phase 3")

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:  # pragma: no cover
        raise NotImplementedError("QRadarRestProvider is implemented in Phase 3")

    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:  # pragma: no cover
        raise NotImplementedError("QRadarRestProvider is implemented in Phase 3")

    async def list_rules(self) -> list[AnalyticsRuleDTO]:  # pragma: no cover - Phase 3
        raise NotImplementedError("QRadarRestProvider is implemented in Phase 3")

    async def list_offenses(  # pragma: no cover - Phase 3
        self, *, open_only: bool = True
    ) -> list[OffenseDTO]:
        raise NotImplementedError("QRadarRestProvider is implemented in Phase 3")
