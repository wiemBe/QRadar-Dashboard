"""Characterization tests for RuleHealthEvaluator.classify.

`classify` is pure -- it takes a rule plus pre-loaded dependency state and
returns a verdict -- so the whole decision tree is testable without a database.
The DB-backed paths (`evaluate_instance`, snapshot persistence, flap damping)
are covered in tests/integration/test_rule_inventory_health.py.

The central assertion in this file is the observation-completeness rule:

    An enabled, silent rule is only NEVER_OBSERVED when a completed metric
    collection proves the silence. Absent that proof it is INSUFFICIENT_DATA.

Without it the platform would report its own collector gap as a detection gap,
which is precisely the failure mode this product exists to prevent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings, get_settings
from app.models.enums import MappingSource, RuleDependencyKind, RuleHealthStatus
from app.models.rule import AnalyticsRule, RuleDependency
from app.services.rule_health import RuleHealthEvaluator

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def settings(**overrides) -> Settings:
    """A Settings copy with rule-health knobs overridden."""
    return get_settings().model_copy(update=overrides)


def make_rule(
    *,
    enabled: bool = True,
    age_days: float | None = 90.0,
    last_fired_at: datetime | None = None,
    expected_daily_firings: float | None = None,
    health_status: RuleHealthStatus = RuleHealthStatus.UNKNOWN,
    **kwargs,
) -> AnalyticsRule:
    """A detached AnalyticsRule; nothing here needs a session."""
    created = None if age_days is None else NOW - timedelta(days=age_days)
    return AnalyticsRule(
        id=uuid.uuid4(),
        instance_id=uuid.uuid4(),
        qradar_id=kwargs.pop("qradar_id", 100),
        name=kwargs.pop("name", "Suspicious Auth Rule"),
        enabled=enabled,
        is_building_block=False,
        qradar_created_at=created,
        last_fired_at=last_fired_at,
        expected_daily_firings=expected_daily_firings,
        health_status=health_status,
        **kwargs,
    )


def dependency(
    kind: RuleDependencyKind,
    target_ref: str,
    *,
    confidence: float = 1.0,
    target_name: str | None = None,
    source: MappingSource = MappingSource.EXPLICIT,
) -> RuleDependency:
    return RuleDependency(
        id=uuid.uuid4(),
        rule_id=uuid.uuid4(),
        kind=kind,
        target_ref=target_ref,
        target_name=target_name,
        source=source,
        confidence=confidence,
    )


def classify(
    rule: AnalyticsRule,
    *,
    trigger_count: int = 0,
    offense_count: int = 0,
    dependencies: list[RuleDependency] | None = None,
    building_block_enabled: dict[str, bool] | None = None,
    log_source_type_healthy: dict[str, bool] | None = None,
    observation_complete: bool = True,
    window_days: int = 30,
    cfg: Settings | None = None,
):
    """Invoke classify with the DB-derived inputs supplied directly.

    `observation_complete` defaults to True here so that tests which are about
    some *other* branch are not implicitly testing the completeness gate; the
    tests that care about it set it explicitly.
    """
    evaluator = RuleHealthEvaluator(
        session=None,  # type: ignore[arg-type] - classify touches no session
        settings=cfg or settings(),
        clock=lambda: NOW,
    )
    return evaluator.classify(
        rule,
        now=NOW,
        window_start=NOW - timedelta(days=window_days),
        trigger_count=trigger_count,
        offense_count=offense_count,
        dependencies=dependencies or [],
        building_block_enabled=building_block_enabled or {},
        log_source_type_healthy=log_source_type_healthy or {},
        observation_complete=observation_complete,
    )


# ============================================================================
# The observation-completeness requirement.
# ============================================================================
class TestObservationCompleteness:
    """No successful RuleMetric collection exists yet in this deployment.

    Every test here guards the same invariant from a different angle: a zero
    trigger count is only evidence when something actually looked.
    """

    def test_silent_rule_is_insufficient_data_without_a_completed_collection(
        self,
    ) -> None:
        verdict = classify(
            make_rule(age_days=120, last_fired_at=None),
            trigger_count=0,
            observation_complete=False,
        )
        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert verdict.status != RuleHealthStatus.NEVER_OBSERVED

    def test_the_reason_names_the_missing_collection_not_the_rule(self) -> None:
        """An operator must be able to tell "we didn't look" from "it never fired"."""
        verdict = classify(
            make_rule(age_days=120), trigger_count=0, observation_complete=False
        )
        assert "no completed rule-metric collection" in verdict.reason.lower()
        assert "not evidence" in verdict.reason.lower()

    def test_confidence_is_low_when_nothing_was_measured(self) -> None:
        verdict = classify(
            make_rule(age_days=120), trigger_count=0, observation_complete=False
        )
        assert verdict.confidence == pytest.approx(0.2)

    def test_evidence_records_the_incompleteness(self) -> None:
        """The snapshot must carry why, so a stored verdict re-justifies itself."""
        verdict = classify(
            make_rule(age_days=120), trigger_count=0, observation_complete=False
        )
        assert verdict.evidence["observation_complete"] is False

    def test_never_observed_requires_a_completed_collection(self) -> None:
        verdict = classify(
            make_rule(age_days=120, last_fired_at=None),
            trigger_count=0,
            observation_complete=True,
        )
        assert verdict.status == RuleHealthStatus.NEVER_OBSERVED
        assert verdict.evidence["observation_complete"] is True

    def test_the_gate_defaults_to_closed(self) -> None:
        """A caller that forgets the flag must not get NEVER_OBSERVED by default.

        `classify` is called from more than one place; defaulting the parameter
        to True would make an omission silently produce the unsafe verdict.
        """
        evaluator = RuleHealthEvaluator(
            session=None,  # type: ignore[arg-type]
            settings=settings(),
            clock=lambda: NOW,
        )
        verdict = evaluator.classify(
            make_rule(age_days=120),
            now=NOW,
            window_start=NOW - timedelta(days=30),
            trigger_count=0,
            offense_count=0,
            dependencies=[],
            building_block_enabled={},
            log_source_type_healthy={},
            # observation_complete deliberately omitted
        )
        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA

    def test_completeness_does_not_affect_a_rule_that_has_fired(self) -> None:
        """The gate covers silence only; observed firings speak for themselves."""
        verdict = classify(
            make_rule(age_days=120, last_fired_at=NOW - timedelta(days=1)),
            trigger_count=40,
            observation_complete=False,
        )
        assert verdict.status == RuleHealthStatus.HEALTHY

    def test_completeness_does_not_override_a_disabled_rule(self) -> None:
        verdict = classify(
            make_rule(enabled=False, age_days=120),
            trigger_count=0,
            observation_complete=False,
        )
        assert verdict.status == RuleHealthStatus.DISABLED

    def test_completeness_does_not_override_degraded_dependencies(self) -> None:
        """Dependency failure is a stronger, better-evidenced explanation."""
        verdict = classify(
            make_rule(age_days=120),
            trigger_count=0,
            observation_complete=False,
            dependencies=[dependency(RuleDependencyKind.BUILDING_BLOCK, "500")],
            building_block_enabled={"500": False},
        )
        assert verdict.status == RuleHealthStatus.DEPENDENCY_DEGRADED


# ============================================================================
# Every classification the enum declares.
# ============================================================================
class TestClassifications:
    def test_disabled(self) -> None:
        verdict = classify(make_rule(enabled=False))
        assert verdict.status == RuleHealthStatus.DISABLED
        assert verdict.enabled is False
        assert "disabled in QRadar" in verdict.reason

    def test_disabled_wins_over_every_other_signal(self) -> None:
        """Intentionally off is a state, not a fault -- it is checked first."""
        verdict = classify(
            make_rule(enabled=False, age_days=0.5),
            trigger_count=100_000,
            dependencies=[dependency(RuleDependencyKind.LOG_SOURCE_TYPE, "12")],
            log_source_type_healthy={"12": False},
        )
        assert verdict.status == RuleHealthStatus.DISABLED

    def test_healthy(self) -> None:
        verdict = classify(
            make_rule(age_days=120, last_fired_at=NOW - timedelta(hours=6)),
            trigger_count=60,
            offense_count=4,
        )
        assert verdict.status == RuleHealthStatus.HEALTHY
        assert verdict.trigger_count == 60
        assert verdict.offense_contribution_count == 4

    def test_healthy_even_when_it_creates_no_offenses(self) -> None:
        """A rule that fires but never raises an offence is still detecting."""
        verdict = classify(
            make_rule(age_days=120, last_fired_at=NOW - timedelta(hours=1)),
            trigger_count=30,
            offense_count=0,
        )
        assert verdict.status == RuleHealthStatus.HEALTHY

    def test_never_observed(self) -> None:
        verdict = classify(
            make_rule(age_days=200, last_fired_at=None),
            trigger_count=0,
            observation_complete=True,
        )
        assert verdict.status == RuleHealthStatus.NEVER_OBSERVED
        assert verdict.evidence["age_days"] == 200.0

    def test_inactive(self) -> None:
        verdict = classify(
            make_rule(age_days=200, last_fired_at=NOW - timedelta(days=45)),
            trigger_count=0,
            cfg=settings(rule_health_inactivity_days=14),
        )
        assert verdict.status == RuleHealthStatus.INACTIVE
        assert verdict.evidence["quiet_days"] == pytest.approx(45.0)

    def test_inactivity_threshold_is_exclusive(self) -> None:
        """Exactly at the boundary is still healthy; past it is inactive."""
        cfg = settings(rule_health_inactivity_days=14)
        at_boundary = classify(
            make_rule(age_days=200, last_fired_at=NOW - timedelta(days=14)),
            trigger_count=1,
            cfg=cfg,
        )
        past_boundary = classify(
            make_rule(age_days=200, last_fired_at=NOW - timedelta(days=14, seconds=1)),
            trigger_count=1,
            cfg=cfg,
        )
        assert at_boundary.status == RuleHealthStatus.HEALTHY
        assert past_boundary.status == RuleHealthStatus.INACTIVE

    def test_noisy_uses_the_global_default_when_no_expectation_is_set(self) -> None:
        cfg = settings(rule_health_noisy_daily_firings=100.0)
        verdict = classify(
            make_rule(age_days=200, last_fired_at=NOW, expected_daily_firings=None),
            # 30-day window, 6000 firings => 200/day, above the 100 default.
            trigger_count=6000,
            cfg=cfg,
        )
        assert verdict.status == RuleHealthStatus.NOISY
        assert verdict.evidence["firings_per_day"] == pytest.approx(200.0)
        assert verdict.evidence["threshold"] == 100.0

    def test_noisy_uses_three_times_the_per_rule_expectation(self) -> None:
        """A rule with a declared baseline is judged against that, not the global."""
        verdict = classify(
            make_rule(age_days=200, last_fired_at=NOW, expected_daily_firings=10.0),
            trigger_count=1000,  # ~33/day against a 30.0 threshold
        )
        assert verdict.status == RuleHealthStatus.NOISY
        assert verdict.evidence["threshold"] == pytest.approx(30.0)

    def test_at_the_expected_rate_is_healthy_not_noisy(self) -> None:
        verdict = classify(
            make_rule(age_days=200, last_fired_at=NOW, expected_daily_firings=10.0),
            trigger_count=300,  # exactly 10/day, well under 3x
        )
        assert verdict.status == RuleHealthStatus.HEALTHY

    def test_zero_expectation_falls_back_to_the_global_threshold(self) -> None:
        """expected=0 must not produce a 0.0 threshold that flags every rule."""
        cfg = settings(rule_health_noisy_daily_firings=500.0)
        verdict = classify(
            make_rule(age_days=200, last_fired_at=NOW, expected_daily_firings=0.0),
            trigger_count=60,  # 2/day
            cfg=cfg,
        )
        assert verdict.status == RuleHealthStatus.HEALTHY

    def test_dependency_degraded_on_disabled_building_block(self) -> None:
        verdict = classify(
            make_rule(age_days=200),
            trigger_count=0,
            dependencies=[
                dependency(
                    RuleDependencyKind.BUILDING_BLOCK, "77", target_name="BB: Admin Hosts"
                )
            ],
            building_block_enabled={"77": False},
        )
        assert verdict.status == RuleHealthStatus.DEPENDENCY_DEGRADED
        assert verdict.building_blocks_healthy is False
        assert "BB: Admin Hosts is disabled" in verdict.missing_dependencies
        assert "not evidence about the rule itself" in verdict.reason

    def test_dependency_degraded_on_unhealthy_log_source(self) -> None:
        verdict = classify(
            make_rule(age_days=200),
            dependencies=[
                dependency(
                    RuleDependencyKind.LOG_SOURCE_TYPE, "12", target_name="Linux OS"
                )
            ],
            log_source_type_healthy={"12": False},
        )
        assert verdict.status == RuleHealthStatus.DEPENDENCY_DEGRADED
        assert verdict.required_log_sources_healthy is False
        assert "Linux OS is not delivering events" in verdict.missing_dependencies

    def test_missing_building_block_defaults_to_healthy(self) -> None:
        """An unknown dependency is not assumed broken; we lack evidence, not health.

        Treating "not in the map" as disabled would degrade every rule whose
        building block has not yet been synced.
        """
        verdict = classify(
            make_rule(age_days=200, last_fired_at=NOW),
            trigger_count=10,
            dependencies=[dependency(RuleDependencyKind.BUILDING_BLOCK, "unsynced")],
            building_block_enabled={},
        )
        assert verdict.status == RuleHealthStatus.HEALTHY
        assert verdict.building_blocks_healthy is True

    def test_multiple_missing_dependencies_are_all_reported(self) -> None:
        verdict = classify(
            make_rule(age_days=200),
            dependencies=[
                dependency(RuleDependencyKind.BUILDING_BLOCK, "1", target_name="BB One"),
                dependency(RuleDependencyKind.BUILDING_BLOCK, "2", target_name="BB Two"),
                dependency(RuleDependencyKind.LOG_SOURCE, "9", target_name="Firewall"),
            ],
            building_block_enabled={"1": False, "2": False},
            log_source_type_healthy={"9": False},
        )
        assert verdict.status == RuleHealthStatus.DEPENDENCY_DEGRADED
        assert len(verdict.missing_dependencies) == 3
        assert verdict.building_blocks_healthy is False
        assert verdict.required_log_sources_healthy is False

    def test_reason_truncates_to_five_dependencies_but_evidence_keeps_all(
        self,
    ) -> None:
        deps = [
            dependency(RuleDependencyKind.BUILDING_BLOCK, str(i), target_name=f"BB {i}")
            for i in range(8)
        ]
        verdict = classify(
            make_rule(age_days=200),
            dependencies=deps,
            building_block_enabled={str(i): False for i in range(8)},
        )
        assert len(verdict.missing_dependencies) == 8
        assert verdict.reason.count(";") == 4  # five items, four separators

    def test_low_confidence_inferred_dependency_is_ignored(self) -> None:
        """Degrading a healthy rule on a weak guess would be worse than silence."""
        cfg = settings(coverage_min_confidence=0.5)
        verdict = classify(
            make_rule(age_days=200, last_fired_at=NOW),
            trigger_count=10,
            dependencies=[
                dependency(
                    RuleDependencyKind.BUILDING_BLOCK,
                    "77",
                    confidence=0.3,
                    source=MappingSource.INFERRED,
                )
            ],
            building_block_enabled={"77": False},
            cfg=cfg,
        )
        assert verdict.status == RuleHealthStatus.HEALTHY
        # Never even considered, so no per-kind summary is reported.
        assert verdict.building_blocks_healthy is None

    def test_inferred_dependency_at_the_floor_is_honoured(self) -> None:
        cfg = settings(coverage_min_confidence=0.5)
        verdict = classify(
            make_rule(age_days=200),
            dependencies=[
                dependency(
                    RuleDependencyKind.BUILDING_BLOCK,
                    "77",
                    confidence=0.5,
                    source=MappingSource.INFERRED,
                )
            ],
            building_block_enabled={"77": False},
            cfg=cfg,
        )
        assert verdict.status == RuleHealthStatus.DEPENDENCY_DEGRADED

    def test_insufficient_data_inside_the_grace_period(self) -> None:
        cfg = settings(rule_health_grace_period_days=7)
        verdict = classify(make_rule(age_days=3.0), cfg=cfg)
        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert "grace period" in verdict.reason
        assert verdict.confidence == pytest.approx(0.6)

    def test_insufficient_data_below_the_minimum_history(self) -> None:
        cfg = settings(rule_health_grace_period_days=1, rule_health_min_history_days=10)
        verdict = classify(make_rule(age_days=5.0), cfg=cfg)
        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert "Not enough observed history" in verdict.reason
        assert verdict.confidence == pytest.approx(0.5)

    def test_insufficient_data_when_the_age_is_unknown(self) -> None:
        """No creation date and no first-seen date means no basis to judge."""
        rule = make_rule(age_days=None)
        rule.first_seen_at = None
        verdict = classify(rule)
        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert "age is unknown" in verdict.reason
        assert verdict.confidence == pytest.approx(0.3)

    def test_first_seen_at_is_the_age_fallback(self) -> None:
        """A rule QRadar gives no creation date for is aged from our first sight."""
        rule = make_rule(age_days=None, last_fired_at=NOW)
        rule.first_seen_at = NOW - timedelta(days=60)
        verdict = classify(rule, trigger_count=5)
        assert verdict.status == RuleHealthStatus.HEALTHY

    def test_qradar_created_at_takes_precedence_over_first_seen(self) -> None:
        rule = make_rule(age_days=2.0)
        rule.first_seen_at = NOW - timedelta(days=400)
        verdict = classify(rule, cfg=settings(rule_health_grace_period_days=7))
        # Judged as 2 days old, not 400: the appliance's own date wins.
        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert "grace period" in verdict.reason

    def test_naive_timestamps_are_treated_as_utc(self) -> None:
        """Postgres can hand back naive datetimes; they must not crash classify."""
        rule = make_rule(age_days=None)
        rule.qradar_created_at = (NOW - timedelta(days=100)).replace(tzinfo=None)
        rule.last_fired_at = (NOW - timedelta(days=1)).replace(tzinfo=None)
        verdict = classify(rule, trigger_count=10)
        assert verdict.status == RuleHealthStatus.HEALTHY

    def test_future_creation_date_clamps_age_to_zero(self) -> None:
        """Clock skew on the appliance must not produce a negative age."""
        verdict = classify(
            make_rule(age_days=-5.0), cfg=settings(rule_health_grace_period_days=7)
        )
        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert verdict.evidence["age_days"] == 0.0


# ============================================================================
# Precedence between branches.
# ============================================================================
class TestPrecedence:
    def test_dependency_check_precedes_the_noisy_check(self) -> None:
        """Telemetry starvation explains behaviour better than a firing rate."""
        verdict = classify(
            make_rule(age_days=200, last_fired_at=NOW),
            trigger_count=100_000,
            dependencies=[dependency(RuleDependencyKind.BUILDING_BLOCK, "1")],
            building_block_enabled={"1": False},
        )
        assert verdict.status == RuleHealthStatus.DEPENDENCY_DEGRADED

    def test_grace_period_precedes_the_dependency_check(self) -> None:
        verdict = classify(
            make_rule(age_days=1.0),
            dependencies=[dependency(RuleDependencyKind.BUILDING_BLOCK, "1")],
            building_block_enabled={"1": False},
            cfg=settings(rule_health_grace_period_days=7),
        )
        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA

    def test_noisy_precedes_inactive_for_a_rule_that_fired_long_ago(self) -> None:
        """A burst inside the window outweighs a stale last_fired_at."""
        verdict = classify(
            make_rule(age_days=200, last_fired_at=NOW - timedelta(days=100)),
            trigger_count=100_000,
            cfg=settings(rule_health_noisy_daily_firings=10.0),
        )
        assert verdict.status == RuleHealthStatus.NOISY


# ============================================================================
# The evidence every verdict carries.
# ============================================================================
class TestVerdictEvidence:
    def test_every_verdict_carries_the_evaluation_window(self) -> None:
        verdict = classify(make_rule(age_days=200, last_fired_at=NOW), trigger_count=1)
        assert verdict.window_end == NOW
        assert verdict.window_start == NOW - timedelta(days=30)

    def test_every_verdict_echoes_the_observation_inputs(self) -> None:
        rule = make_rule(
            age_days=200, last_fired_at=NOW - timedelta(days=2), expected_daily_firings=5.0
        )
        verdict = classify(rule, trigger_count=17, offense_count=3)
        assert verdict.trigger_count == 17
        assert verdict.offense_contribution_count == 3
        assert verdict.expected_daily_firings == 5.0
        assert verdict.last_triggered_at == rule.last_fired_at
        assert verdict.enabled is True

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"enabled": False}, RuleHealthStatus.DISABLED),
            ({"age_days": 1.0}, RuleHealthStatus.INSUFFICIENT_DATA),
            ({"age_days": 200, "last_fired_at": NOW}, RuleHealthStatus.HEALTHY),
        ],
    )
    def test_every_branch_produces_a_human_readable_reason(
        self, kwargs: dict, expected: RuleHealthStatus
    ) -> None:
        verdict = classify(make_rule(**kwargs), trigger_count=1)
        assert verdict.status == expected
        assert verdict.reason
        assert verdict.reason[0].isupper()
        assert verdict.reason.rstrip().endswith(".")
        assert len(verdict.reason) > 20

    def test_confidence_is_full_for_well_evidenced_verdicts(self) -> None:
        verdict = classify(make_rule(age_days=200, last_fired_at=NOW), trigger_count=5)
        assert verdict.confidence == 1.0

    def test_dependency_degraded_is_slightly_below_full_confidence(self) -> None:
        """Dependency inference is good but not certain."""
        verdict = classify(
            make_rule(age_days=200),
            dependencies=[dependency(RuleDependencyKind.BUILDING_BLOCK, "1")],
            building_block_enabled={"1": False},
        )
        assert verdict.confidence == pytest.approx(0.9)

    def test_logic_version_comes_from_settings(self) -> None:
        evaluator = RuleHealthEvaluator(
            session=None,  # type: ignore[arg-type]
            settings=settings(rule_health_logic_version=7),
        )
        assert evaluator.logic_version == 7
