"""Provider data-transfer objects.

Every provider returns these normalized shapes, never raw QRadar payloads. This
is the contract that lets REST, MCP and Mock be genuinely interchangeable: a
service consuming a `LogSourceDTO` cannot tell, and must not care, which backend
produced it.

These are transport DTOs, deliberately distinct from both the SQLAlchemy models
(persistence) and the API schemas (presentation). Keeping the three layers
separate is what stops QRadar's field naming from leaking end to end.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProviderDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class InstanceInfoDTO(ProviderDTO):
    version: str | None = None
    build: str | None = None
    reachable: bool = True
    raw_about: dict = Field(default_factory=dict)


class LogSourceDTO(ProviderDTO):
    qradar_id: int
    name: str
    description: str | None = None
    type_id: int | None = None
    type_name: str | None = None
    protocol_type_id: int | None = None
    enabled: bool = True
    status: str | None = None
    credibility: int | None = None
    target_event_collector_id: int | None = None
    average_eps: float | None = None
    last_event_time: datetime | None = None


class LogSourceTypeDTO(ProviderDTO):
    type_id: int
    name: str


class LogSourceMetricSample(ProviderDTO):
    """One log source's observed metrics over a single collection interval.

    Field names mirror LogSourceMetric so the collector maps them directly. The
    REST provider derives these from AQL aggregates over the interval; the mock
    synthesizes them deterministically.
    """

    qradar_id: int
    bucket_start: datetime
    bucket_seconds: int = 300
    event_count: int = 0
    average_eps: float = 0.0
    peak_eps: float = 0.0
    last_event_at: datetime | None = None
    event_delay_seconds: float | None = None
    unknown_event_count: int = 0
    stored_event_count: int = 0
    parsed_username_ratio: float | None = None
    parsed_source_ip_ratio: float | None = None
    distinct_qid_count: int = 0
    distinct_username_count: int = 0
    distinct_source_ip_count: int = 0
    collection_error_count: int = 0
    # Hash of a representative payload, for REPEATED_PAYLOAD detection.
    payload_signature: str | None = None


class OffenseDTO(ProviderDTO):
    qradar_id: int
    description: str | None = None
    status: str | None = None
    magnitude: int | None = None
    severity: int | None = None
    credibility: int | None = None
    relevance: int | None = None
    assigned_to: str | None = None
    offense_type: int | None = None
    offense_source: str | None = None
    event_count: int | None = None
    flow_count: int | None = None
    device_count: int | None = None
    start_time: datetime | None = None
    last_updated_time: datetime | None = None
    closing_reason_id: int | None = None
    categories: list[str] = Field(default_factory=list)
    source_addresses: list[str] = Field(default_factory=list)
    local_destination_addresses: list[str] = Field(default_factory=list)
    rule_ids: list[int] = Field(default_factory=list)


class AnalyticsRuleDTO(ProviderDTO):
    qradar_id: int
    name: str
    rule_type: str | None = None
    enabled: bool = True
    origin: str | None = None
    is_building_block: bool = False
    owner: str | None = None
    average_capacity: float | None = None
    categories: list[str] = Field(default_factory=list)
    mitre_techniques: list[str] = Field(default_factory=list)
    modified_at: datetime | None = None


class ArielSearchHandleDTO(ProviderDTO):
    """Handle returned when an Ariel search is created."""

    search_id: str
    status: str
    progress: int = 0


class ArielSearchStatusDTO(ProviderDTO):
    search_id: str
    status: str  # WAIT | EXECUTE | SORTING | COMPLETED | CANCELED | ERROR
    progress: int = 0
    record_count: int | None = None
    error_messages: list[str] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in {"COMPLETED", "CANCELED", "ERROR"}

    @property
    def is_success(self) -> bool:
        return self.status == "COMPLETED"


class ArielSearchResultsDTO(ProviderDTO):
    search_id: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)
    total_count: int = 0
    truncated: bool = False
