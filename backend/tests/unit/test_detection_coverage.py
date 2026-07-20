"""Characterization tests for DetectionCoverageEvaluator.evaluate_technique.

The roll-up is pure, so the whole status lattice is exercised here without a
database; persistence and the API projections live in the integration suite.

The invariant this file defends: COVERED is never awarded for the mere
existence of a rule. A mapped rule that is disabled, starved of telemetry,
silent, or not yet health-assessed does not produce coverage, and each of those
cases resolves to a *different* status so an operator can act on it.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings, get_settings
from app.models.enums import CoverageStatus, MappingSource, RuleHealthStatus
from app.models.rule import AnalyticsRule, TechniqueMapping
from app.services.detection_coverage import DetectionCoverageEvaluator

INSTANCE_ID = uuid.uuid4()


def settings(**overrides) -> Settings:
    return get_settings().model_copy(update=overrides)


def make_rule(
    *,
    qradar_id: int = 1,
    name: str = "Rule",
    enabled: bool = True,
    health: RuleHealthStatus = RuleHealthStatus.HEALTHY,
) -> AnalyticsRule:
    return AnalyticsRule(
        id=uuid.uuid4(),
        instance_id=INSTANCE_ID,
        qradar_id=qradar_id,
        name=name,
        enabled=enabled,
        is_building_block=False,
        health_status=health,
    )


def make_mapping(
    rule: AnalyticsRule,
    *,
    technique_id: str = "T1059",
    technique_name: str | None = "Command and Scripting Interpreter",
    tactic: str | None = "Execution",
    source: MappingSource = MappingSource.EXPLICIT,
    confidence: float = 1.0,
) -> TechniqueMapping:
    return TechniqueMapping(
        id=uuid.uuid4(),
        instance_id=INSTANCE_ID,
        technique_id=technique_id,
        technique_name=technique_name,
        tactic=tactic,
        rule_id=rule.id,
        source=source,
        confidence=confidence,
    )


def evaluate(pairs, *, technique_id: str = "T1059", cfg: Settings | None = None):
    evaluator = DetectionCoverageEvaluator(
        session=None,  # type: ignore[arg-type] - evaluate_technique is pure
        settings=cfg or settings(),
    )
    return evaluator.evaluate_technique(technique_id, pairs)


def pair(rule: AnalyticsRule, **kwargs):
    return (make_mapping(rule, **kwargs), rule)


# ============================================================================
# The core statuses.
# ============================================================================
class TestStatuses:
    def test_covered_when_the_only_rule_is_enabled_and_healthy(self) -> None:
        verdict = evaluate([pair(make_rule(health=RuleHealthStatus.HEALTHY))])
        assert verdict.status == CoverageStatus.COVERED
        assert verdict.coverage_score == 1.0
        assert verdict.firing_rule_count == 1

    def test_noisy_still_counts_as_working_detection(self) -> None:
        """A rule firing too much is a tuning problem, not a coverage gap."""
        verdict = evaluate([pair(make_rule(health=RuleHealthStatus.NOISY))])
        assert verdict.status == CoverageStatus.COVERED

    def test_missing_when_no_rule_is_mapped(self) -> None:
        verdict = evaluate([])
        assert verdict.status == CoverageStatus.MISSING
        assert verdict.coverage_score == 0.0
        assert verdict.mapped_rule_count == 0
        assert "No rule is mapped" in verdict.reason

    def test_missing_when_the_only_mapped_rule_is_disabled(self) -> None:
        """A disabled rule provides no active coverage. The single most
        important assertion in this module."""
        verdict = evaluate([pair(make_rule(enabled=False))])
        assert verdict.status == CoverageStatus.MISSING
        assert verdict.coverage_score == 0.0
        assert verdict.enabled_rule_count == 0
        assert verdict.mapped_rule_count == 1

    def test_disabled_rule_is_never_covered_however_healthy_it_looks(self) -> None:
        """health_status can lag behind an operator disabling the rule."""
        verdict = evaluate(
            [pair(make_rule(enabled=False, health=RuleHealthStatus.HEALTHY))]
        )
        assert verdict.status == CoverageStatus.MISSING
        assert verdict.evidence[0].contributes is False
        assert "disabled" in verdict.evidence[0].reason

    def test_degraded_when_the_only_rule_lacks_telemetry(self) -> None:
        verdict = evaluate(
            [pair(make_rule(health=RuleHealthStatus.DEPENDENCY_DEGRADED))]
        )
        assert verdict.status == CoverageStatus.DEGRADED
        assert verdict.degraded_rule_count == 1
        assert verdict.coverage_score == 0.0

    def test_degraded_when_the_only_rule_is_inactive(self) -> None:
        verdict = evaluate([pair(make_rule(health=RuleHealthStatus.INACTIVE))])
        assert verdict.status == CoverageStatus.DEGRADED

    def test_degraded_when_the_only_rule_has_never_fired(self) -> None:
        verdict = evaluate([pair(make_rule(health=RuleHealthStatus.NEVER_OBSERVED))])
        assert verdict.status == CoverageStatus.DEGRADED

    def test_not_evaluated_when_health_is_insufficient_data(self) -> None:
        """Unknown is not a gap. Reporting MISSING here would send a SOC
        chasing a detection that may well be working."""
        verdict = evaluate([pair(make_rule(health=RuleHealthStatus.INSUFFICIENT_DATA))])
        assert verdict.status == CoverageStatus.NOT_EVALUATED
        assert verdict.status != CoverageStatus.MISSING
        assert verdict.degraded_rule_count == 0

    def test_not_evaluated_when_health_is_unknown(self) -> None:
        verdict = evaluate([pair(make_rule(health=RuleHealthStatus.UNKNOWN))])
        assert verdict.status == CoverageStatus.NOT_EVALUATED

    def test_insufficient_data_does_not_count_as_degraded(self) -> None:
        """The distinction drives the "degraded" operator filter."""
        verdict = evaluate([pair(make_rule(health=RuleHealthStatus.INSUFFICIENT_DATA))])
        assert verdict.degraded_rule_count == 0
        assert verdict.firing_rule_count == 0
        assert verdict.enabled_rule_count == 1


# ============================================================================
# Multiple rules per technique.
# ============================================================================
class TestMultipleRules:
    def test_all_healthy_rules_give_full_coverage(self) -> None:
        verdict = evaluate(
            [
                pair(make_rule(qradar_id=1)),
                pair(make_rule(qradar_id=2)),
                pair(make_rule(qradar_id=3)),
            ]
        )
        assert verdict.status == CoverageStatus.COVERED
        assert verdict.coverage_score == 1.0
        assert verdict.firing_rule_count == 3

    def test_one_healthy_and_one_degraded_is_degraded(self) -> None:
        """Partial detection with a known fault is reported as the fault.

        DEGRADED rather than PARTIAL, because something is actually broken and
        an operator can fix it -- as opposed to merely unmeasured.
        """
        verdict = evaluate(
            [
                pair(make_rule(qradar_id=1, health=RuleHealthStatus.HEALTHY)),
                pair(make_rule(qradar_id=2, health=RuleHealthStatus.INACTIVE)),
            ]
        )
        assert verdict.status == CoverageStatus.DEGRADED
        assert verdict.coverage_score == pytest.approx(0.5)
        assert verdict.firing_rule_count == 1
        assert verdict.degraded_rule_count == 1

    def test_every_rule_degraded_is_degraded_with_zero_score(self) -> None:
        verdict = evaluate(
            [
                pair(make_rule(qradar_id=1, health=RuleHealthStatus.INACTIVE)),
                pair(make_rule(qradar_id=2, health=RuleHealthStatus.NEVER_OBSERVED)),
            ]
        )
        assert verdict.status == CoverageStatus.DEGRADED
        assert verdict.coverage_score == 0.0
        assert verdict.degraded_rule_count == 2

    def test_healthy_plus_unassessed_is_partial(self) -> None:
        """Nothing is known to be broken, but coverage is not fully established."""
        verdict = evaluate(
            [
                pair(make_rule(qradar_id=1, health=RuleHealthStatus.HEALTHY)),
                pair(make_rule(qradar_id=2, health=RuleHealthStatus.INSUFFICIENT_DATA)),
            ]
        )
        assert verdict.status == CoverageStatus.PARTIAL
        assert verdict.coverage_score == pytest.approx(0.5)
        assert verdict.degraded_rule_count == 0

    def test_disabled_rules_are_excluded_from_the_score_denominator(self) -> None:
        """Score reflects enabled rules; a disabled one must not dilute it."""
        verdict = evaluate(
            [
                pair(make_rule(qradar_id=1, health=RuleHealthStatus.HEALTHY)),
                pair(make_rule(qradar_id=2, enabled=False)),
            ]
        )
        assert verdict.status == CoverageStatus.COVERED
        assert verdict.coverage_score == 1.0
        assert verdict.enabled_rule_count == 1
        assert verdict.mapped_rule_count == 2

    def test_all_rules_disabled_is_missing_not_degraded(self) -> None:
        verdict = evaluate(
            [
                pair(make_rule(qradar_id=1, enabled=False)),
                pair(make_rule(qradar_id=2, enabled=False)),
            ]
        )
        assert verdict.status == CoverageStatus.MISSING
        assert "disabled or discounted" in verdict.reason


# ============================================================================
# Mapping provenance and confidence.
# ============================================================================
class TestMappingProvenance:
    def test_explicit_mapping_contributes_at_full_confidence(self) -> None:
        verdict = evaluate([pair(make_rule(), source=MappingSource.EXPLICIT)])
        assert verdict.status == CoverageStatus.COVERED
        assert verdict.confidence == 1.0
        assert verdict.inferred_rule_count == 0
        assert verdict.evidence[0].mapping_source == str(MappingSource.EXPLICIT)

    def test_inferred_mapping_is_counted_and_retained(self) -> None:
        verdict = evaluate(
            [pair(make_rule(), source=MappingSource.INFERRED, confidence=0.8)]
        )
        assert verdict.status == CoverageStatus.COVERED
        assert verdict.inferred_rule_count == 1
        assert verdict.evidence[0].mapping_source == str(MappingSource.INFERRED)

    def test_confidence_propagates_from_the_mapping(self) -> None:
        verdict = evaluate(
            [pair(make_rule(), source=MappingSource.INFERRED, confidence=0.75)]
        )
        assert verdict.confidence == pytest.approx(0.75)

    def test_confidence_is_the_weakest_contributing_mapping(self) -> None:
        """Coverage is only as trustworthy as the shakiest link supporting it."""
        verdict = evaluate(
            [
                pair(make_rule(qradar_id=1), confidence=0.9),
                pair(make_rule(qradar_id=2), source=MappingSource.INFERRED,
                     confidence=0.6),
            ]
        )
        assert verdict.confidence == pytest.approx(0.6)

    def test_low_confidence_mapping_is_recorded_but_not_counted(self) -> None:
        cfg = settings(coverage_min_confidence=0.5)
        verdict = evaluate(
            [pair(make_rule(), source=MappingSource.INFERRED, confidence=0.2)], cfg=cfg
        )
        assert verdict.status == CoverageStatus.MISSING
        assert verdict.enabled_rule_count == 0
        # Retained as evidence so the SOC can review or promote the guess.
        assert len(verdict.evidence) == 1
        assert verdict.evidence[0].contributes is False
        assert "below the" in verdict.evidence[0].reason

    def test_low_confidence_mapping_still_counts_as_inferred(self) -> None:
        cfg = settings(coverage_min_confidence=0.5)
        verdict = evaluate(
            [pair(make_rule(), source=MappingSource.INFERRED, confidence=0.2)], cfg=cfg
        )
        assert verdict.inferred_rule_count == 1

    def test_a_strong_mapping_survives_a_weak_sibling(self) -> None:
        cfg = settings(coverage_min_confidence=0.5)
        verdict = evaluate(
            [
                pair(make_rule(qradar_id=1), confidence=1.0),
                pair(make_rule(qradar_id=2), source=MappingSource.INFERRED,
                     confidence=0.1),
            ],
            cfg=cfg,
        )
        assert verdict.status == CoverageStatus.COVERED
        # The discounted mapping never enters the confidence calculation.
        assert verdict.confidence == 1.0
        assert verdict.enabled_rule_count == 1

    def test_confidence_floor_boundary_is_inclusive(self) -> None:
        cfg = settings(coverage_min_confidence=0.5)
        at_floor = evaluate([pair(make_rule(), confidence=0.5)], cfg=cfg)
        below = evaluate([pair(make_rule(), confidence=0.49)], cfg=cfg)
        assert at_floor.status == CoverageStatus.COVERED
        assert below.status == CoverageStatus.MISSING


# ============================================================================
# Evidence and descriptive metadata.
# ============================================================================
class TestEvidence:
    def test_evidence_is_recorded_for_every_mapped_rule(self) -> None:
        verdict = evaluate(
            [
                pair(make_rule(qradar_id=1, name="Healthy One")),
                pair(make_rule(qradar_id=2, name="Disabled One", enabled=False)),
                pair(make_rule(qradar_id=3, name="Broken One",
                               health=RuleHealthStatus.DEPENDENCY_DEGRADED)),
            ]
        )
        assert len(verdict.evidence) == 3
        assert {e.rule_qradar_id for e in verdict.evidence} == {1, 2, 3}
        assert [e.contributes for e in verdict.evidence] == [True, False, False]

    def test_each_evidence_row_explains_itself(self) -> None:
        verdict = evaluate(
            [
                pair(make_rule(qradar_id=1)),
                pair(make_rule(qradar_id=2, enabled=False)),
                pair(make_rule(qradar_id=3,
                               health=RuleHealthStatus.DEPENDENCY_DEGRADED)),
                pair(make_rule(qradar_id=4,
                               health=RuleHealthStatus.INSUFFICIENT_DATA)),
            ]
        )
        reasons = {e.rule_qradar_id: e.reason for e in verdict.evidence}
        assert "health is acceptable" in reasons[1]
        assert "disabled" in reasons[2]
        assert "telemetry is unavailable" in reasons[3]
        assert "not been established" in reasons[4]

    @pytest.mark.parametrize(
        ("health", "fragment"),
        [
            (RuleHealthStatus.DEPENDENCY_DEGRADED, "telemetry is unavailable"),
            (RuleHealthStatus.NEVER_OBSERVED, "never been observed"),
            (RuleHealthStatus.INACTIVE, "inactivity window"),
        ],
    )
    def test_degraded_reasons_distinguish_the_failure_mode(
        self, health: RuleHealthStatus, fragment: str
    ) -> None:
        verdict = evaluate([pair(make_rule(health=health))])
        assert fragment in verdict.evidence[0].reason

    def test_technique_name_and_tactic_come_from_the_mappings(self) -> None:
        verdict = evaluate([pair(make_rule())])
        assert verdict.technique_name == "Command and Scripting Interpreter"
        assert verdict.tactic == "Execution"

    def test_descriptive_metadata_falls_back_across_mappings(self) -> None:
        """One mapping missing the SOC's descriptive text must not blank it."""
        verdict = evaluate(
            [
                pair(make_rule(qradar_id=1), technique_name=None, tactic=None),
                pair(make_rule(qradar_id=2), technique_name="Named", tactic="Execution"),
            ]
        )
        assert verdict.technique_name == "Named"
        assert verdict.tactic == "Execution"

    def test_technique_id_is_carried_through(self) -> None:
        verdict = evaluate([pair(make_rule(), technique_id="T1078.004")],
                           technique_id="T1078.004")
        assert verdict.technique_id == "T1078.004"

    def test_unmapped_technique_verdict_has_no_evidence(self) -> None:
        verdict = evaluate([], technique_id="T1003")
        assert verdict.evidence == []
        assert verdict.technique_id == "T1003"
        assert verdict.confidence == 1.0  # certain that nothing is mapped

    def test_every_status_carries_a_reason(self) -> None:
        for pairs in (
            [],
            [pair(make_rule())],
            [pair(make_rule(enabled=False))],
            [pair(make_rule(health=RuleHealthStatus.INACTIVE))],
            [pair(make_rule(health=RuleHealthStatus.UNKNOWN))],
        ):
            verdict = evaluate(pairs)
            assert verdict.reason
            assert verdict.reason.rstrip().endswith(".")

    def test_logic_version_comes_from_settings(self) -> None:
        evaluator = DetectionCoverageEvaluator(
            session=None,  # type: ignore[arg-type]
            settings=settings(coverage_logic_version=4),
        )
        assert evaluator.logic_version == 4


# ============================================================================
# The property the whole module exists to guarantee.
# ============================================================================
class TestCoveredIsEarnedNotAssumed:
    @pytest.mark.parametrize(
        "health",
        [
            RuleHealthStatus.DEPENDENCY_DEGRADED,
            RuleHealthStatus.INACTIVE,
            RuleHealthStatus.NEVER_OBSERVED,
            RuleHealthStatus.INSUFFICIENT_DATA,
            RuleHealthStatus.UNKNOWN,
            RuleHealthStatus.DISABLED,
        ],
    )
    def test_a_rule_that_is_not_demonstrably_working_never_yields_covered(
        self, health: RuleHealthStatus
    ) -> None:
        verdict = evaluate([pair(make_rule(health=health))])
        assert verdict.status != CoverageStatus.COVERED

    def test_existence_of_a_mapping_alone_does_not_yield_covered(self) -> None:
        """The mapping is present and high-confidence; the rule is simply
        unassessed. That is NOT_EVALUATED, never COVERED."""
        verdict = evaluate(
            [pair(make_rule(health=RuleHealthStatus.UNKNOWN), confidence=1.0)]
        )
        assert verdict.status == CoverageStatus.NOT_EVALUATED
        assert verdict.coverage_score == 0.0
