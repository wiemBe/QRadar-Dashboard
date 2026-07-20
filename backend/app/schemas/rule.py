"""Analytics rule, rule-health and provider-capability API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MappingSource, RuleDependencyKind, RuleHealthStatus
from app.schemas.offense import _Sanitized


class RuleDependencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: RuleDependencyKind
    target_ref: str
    target_name: str | None = None
    # Always exposed. A consumer must be able to tell a curated dependency from
    # one the platform guessed, without having to ask.
    source: MappingSource
    confidence: float
    provenance: str | None = None


class RuleSummary(_Sanitized):
    id: uuid.UUID
    qradar_id: int
    name: str
    enabled: bool
    is_building_block: bool
    rule_type: str | None = None
    origin: str | None = None
    owner: str | None = None
    health_status: RuleHealthStatus
    health_evaluated_at: datetime | None = None
    last_fired_at: datetime | None = None
    offense_contribution_count: int = 0
    event_contribution_count: int = 0
    mitre_techniques: list[str] = Field(default_factory=list)


class RuleDetail(RuleSummary):
    description: str | None = None
    categories: list[str] = Field(default_factory=list)
    average_capacity: float | None = None
    generates_offense: bool | None = None
    response_actions: list[str] = Field(default_factory=list)
    qradar_created_at: datetime | None = None
    qradar_modified_at: datetime | None = None
    first_seen_at: datetime | None = None
    soc_notes: str | None = None
    expected_daily_firings: float | None = None
    health_monitoring_enabled: bool = True
    dependencies: list[RuleDependencyOut] = Field(default_factory=list)


class RuleHealthSnapshotOut(_Sanitized):
    evaluated_at: datetime
    status: RuleHealthStatus
    logic_version: int
    confidence: float
    reason: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    last_triggered_at: datetime | None = None
    trigger_count: int = 0
    offense_contribution_count: int = 0
    expected_daily_firings: float | None = None
    enabled: bool = True
    building_blocks_healthy: bool | None = None
    required_log_sources_healthy: bool | None = None
    missing_dependencies: list[str] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)


class RuleStateTransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    observed_at: datetime
    previous_enabled: bool
    current_enabled: bool


class RulePage(BaseModel):
    items: list[RuleSummary]
    total: int
    limit: int
    offset: int


class RuleHealthSummaryEntry(BaseModel):
    status: str
    count: int


class RuleUpdate(BaseModel):
    """SOC-owned fields only.

    The QRadar-mirrored columns are deliberately absent: this platform is
    read-only against QRadar, so there is no field here that could imply a
    write back to the SIEM.
    """

    owner: str | None = None
    mitre_techniques: list[str] | None = None
    soc_notes: str | None = None
    expected_daily_firings: float | None = None
    health_monitoring_enabled: bool | None = None


# --------------------------------------------------------------- capabilities
class ProviderCapabilityOut(BaseModel):
    """What the configured backend can do, and how it is behaving.

    Exposed so the REST-versus-MCP choice is explicit and observable rather than
    something an operator has to infer from configuration files.
    """

    provider_kind: str
    provider_class: str
    capabilities: list[str]
    aql_execution_path: str
    mcp_enabled: bool
    mcp_allowlisted_tools: list[str]
    mcp_blocked_write_tools: list[str]
    call_metrics: list[dict]
