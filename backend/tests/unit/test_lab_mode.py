"""LAB_MODE is explicit, bounded, and cannot alter production defaults."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.anomaly.thresholds import ThresholdResolver
from app.core.config import LAB_PROFILE, Environment, Settings
from app.models.enums import Criticality


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, debug=False, **overrides)


def test_production_defaults_are_unchanged_without_lab_mode() -> None:
    configured = settings(lab_mode=False)
    assert configured.collection_interval_seconds == 300
    assert configured.baseline_min_samples == 8
    assert configured.anomaly_open_after_intervals == 2
    assert configured.anomaly_resolve_after_intervals == 3
    assert configured.offense_collection_interval_seconds == 300
    resolver = ThresholdResolver(configured)
    assert resolver.resolve(criticality=Criticality.CRITICAL).open_after == 1
    assert resolver.resolve(criticality=Criticality.LOW).open_after == 3
    assert resolver.resolve(criticality=Criticality.LOW).resolve_after == 4


def test_lab_mode_applies_exact_bounded_profile() -> None:
    configured = settings(lab_mode=True)
    for field_name, expected in LAB_PROFILE.items():
        assert getattr(configured, field_name) == expected
    thresholds = ThresholdResolver(configured).resolve(criticality=Criticality.LOW)
    assert thresholds.open_after == 1
    assert thresholds.resolve_after == 2


def test_lab_mode_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="LAB_MODE is not permitted in production"):
        settings(environment=Environment.PRODUCTION, lab_mode=True)
