"""Detection-coverage API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CoverageStatus, MappingSource
from app.schemas.offense import _Sanitized


class CoverageEvidenceRule(BaseModel):
    rule_qradar_id: int
    rule_name: str
    enabled: bool
    health_status: str
    mapping_source: str
    mapping_confidence: float
    contributes: bool
    reason: str


class TechniqueCoverage(_Sanitized):
    instance_id: uuid.UUID
    technique_id: str
    technique_name: str | None = None
    tactic: str | None = None
    status: CoverageStatus
    coverage_score: float
    confidence: float
    mapped_rule_count: int
    enabled_rule_count: int
    firing_rule_count: int
    inferred_rule_count: int
    degraded_rule_count: int
    last_evaluated_at: datetime | None = None
    logic_version: int
    reason: str | None = None


class TechniqueCoverageDetail(TechniqueCoverage):
    # The full rule -> health -> dependency chain behind the verdict, so an
    # operator can see why a technique is degraded without re-deriving it.
    evidence: dict = Field(default_factory=dict)
    notes: str | None = None


class CoveragePage(BaseModel):
    items: list[TechniqueCoverage]
    total: int
    limit: int
    offset: int


class CoverageSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    captured_at: datetime
    status: CoverageStatus
    coverage_score: float
    confidence: float
    mapped_rule_count: int
    enabled_rule_count: int
    firing_rule_count: int
    degraded_rule_count: int
    logic_version: int
    reason: str | None = None


class CoverageSummary(BaseModel):
    generated_at: datetime
    total_techniques: int
    by_status: dict[str, int]
    covered_ratio: float
    average_coverage_score: float
    # Explicit vs inferred mapping counts. Reported alongside the headline
    # numbers so a coverage figure resting mostly on inference is visible as
    # such rather than reading as curated fact.
    mapping_provenance: dict[str, int]


class TechniqueMappingIn(BaseModel):
    """SOC-owned mapping input.

    MITRE identifiers and descriptions are supplied by the SOC; the platform
    imports nothing from an external MITRE source.
    """

    technique_id: str = Field(min_length=2, max_length=32)
    technique_name: str | None = Field(default=None, max_length=255)
    tactic: str | None = Field(default=None, max_length=128)
    rule_id: uuid.UUID
    source: MappingSource = MappingSource.EXPLICIT
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    provenance: str | None = Field(default=None, max_length=255)


class TechniqueMappingOut(_Sanitized):
    id: uuid.UUID
    technique_id: str
    technique_name: str | None = None
    tactic: str | None = None
    rule_id: uuid.UUID
    source: MappingSource
    confidence: float
    provenance: str | None = None
