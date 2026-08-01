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


class DimensionValueCount(ProviderDTO):
    """One value of one field, and how many events carried it in a window."""

    value: str
    count: int
    #: Friendlier form where the backend supplies one (QID -> event name). Never
    #: synthesized: a missing label stays None rather than becoming the raw
    #: value dressed up as a name.
    label: str | None = None


class DimensionAggregate(ProviderDTO):
    """Bounded aggregate over one explanation dimension for one window.

    ``available=False`` means the field is not populated in this source's DSM
    output. That is materially different from "the field exists and no events
    carried a value", so the two must never collapse into an empty list.
    """

    dimension: str
    available: bool = True
    values: list[DimensionValueCount] = Field(default_factory=list)
    #: Distinct values in the window. May exceed len(values) when the result was
    #: capped, which is why it is measured separately rather than inferred.
    distinct_count: int = 0
    #: Total events in the window, across all values of this dimension.
    total_count: int = 0
    #: The configured value cap was hit, so `values` is a prefix of the truth.
    truncated: bool = False
    #: The AQL that produced this aggregate. Non-secret; recorded as provenance.
    query: str | None = None
    #: Sanitized failure reason when the aggregate could not be produced.
    error: str | None = None


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
    offense_type_name: str | None = None
    offense_source: str | None = None
    source_network: str | None = None
    event_count: int | None = None
    flow_count: int | None = None
    device_count: int | None = None
    # QRadar reports these as counts independently of the (capped) address
    # lists, so a wide offense still reports its true breadth.
    source_count: int | None = None
    destination_count: int | None = None
    start_time: datetime | None = None
    last_updated_time: datetime | None = None
    close_time: datetime | None = None
    closing_reason_id: int | None = None
    closing_reason: str | None = None
    categories: list[str] = Field(default_factory=list)
    source_addresses: list[str] = Field(default_factory=list)
    local_destination_addresses: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    log_source_ids: list[int] = Field(default_factory=list)
    rule_ids: list[int] = Field(default_factory=list)


class AnalyticsRuleDTO(ProviderDTO):
    qradar_id: int
    name: str
    description: str | None = None
    rule_type: str | None = None
    enabled: bool = True
    origin: str | None = None
    is_building_block: bool = False
    owner: str | None = None
    average_capacity: float | None = None
    categories: list[str] = Field(default_factory=list)
    mitre_techniques: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    modified_at: datetime | None = None

    # Whether the rule's response configuration creates/dispatches an offense.
    # A rule that fires but generates no offense is a real and distinct state
    # from one that never fires, and rule health must not conflate them.
    generates_offense: bool | None = None
    response_actions: list[str] = Field(default_factory=list)
    # Building blocks this rule tests. QRadar exposes rule dependencies only
    # partially; anything the provider could not establish authoritatively is
    # reported separately as an inference by the collector, never merged here.
    building_block_ids: list[int] = Field(default_factory=list)
    log_source_type_ids: list[int] = Field(default_factory=list)

    last_triggered_at: datetime | None = None
    event_contribution_count: int | None = None
    offense_contribution_count: int | None = None


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
