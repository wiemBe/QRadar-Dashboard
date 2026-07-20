"""Resolve anomaly thresholds and hysteresis for a log source.

Resolution order (later overrides earlier):
  1. Global defaults (from Settings).
  2. Per-criticality adjustments (critical sources are more sensitive and open
     faster; low-criticality sources are damped).
  3. Per-source overrides in LogSource.custom_thresholds.

Hysteresis (N intervals to open, M to resolve) is likewise resolvable per
anomaly type and per criticality, so a CRITICAL source's outage opens after one
interval while a noisy LOW source needs several.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings, get_settings
from app.models.enums import AnomalyType, Criticality


@dataclass(frozen=True)
class Thresholds:
    # deviation z-score magnitude to flag volume anomalies
    deviation_z: float = 3.5
    # ratio floors/ceilings
    min_parsed_ratio: float = 0.5           # below -> PARSING_DEGRADATION
    max_unknown_ratio: float = 0.2          # above -> UNKNOWN_EVENT_SPIKE
    # timestamp delay: multiple of the expected interval
    delay_interval_multiple: float = 3.0
    # cardinality drop: fraction of baseline below which we flag
    cardinality_drop_fraction: float = 0.4
    # collection errors above this flag COLLECTION_ERROR
    collection_error_threshold: int = 0
    # repeated payload across >= this many consecutive intervals
    repeated_payload_intervals: int = 3
    # hysteresis
    open_after: int = 2
    resolve_after: int = 3


# Per-criticality multipliers/overrides. CRITICAL opens after a single interval.
_CRITICALITY_OPEN_AFTER = {
    Criticality.CRITICAL: 1,
    Criticality.HIGH: 2,
    Criticality.MEDIUM: 2,
    Criticality.LOW: 3,
}
_CRITICALITY_RESOLVE_AFTER = {
    Criticality.CRITICAL: 2,
    Criticality.HIGH: 3,
    Criticality.MEDIUM: 3,
    Criticality.LOW: 4,
}


@dataclass
class ThresholdResolver:
    settings: Settings = field(default_factory=get_settings)

    def resolve(
        self,
        *,
        criticality: Criticality,
        custom: dict | None = None,
        anomaly_type: AnomalyType | None = None,
    ) -> Thresholds:
        base = Thresholds(
            deviation_z=self.settings.anomaly_deviation_threshold,
            open_after=_CRITICALITY_OPEN_AFTER.get(
                criticality, self.settings.anomaly_open_after_intervals
            ),
            resolve_after=_CRITICALITY_RESOLVE_AFTER.get(
                criticality, self.settings.anomaly_resolve_after_intervals
            ),
        )
        merged = base
        if custom:
            merged = self._apply_overrides(base, custom, anomaly_type)
        return merged

    def _apply_overrides(
        self, base: Thresholds, custom: dict, anomaly_type: AnomalyType | None
    ) -> Thresholds:
        # custom_thresholds shape: {"deviation_z": 4.0, "open_after": 1,
        #   "by_type": {"VOLUME_SPIKE": {"deviation_z": 5.0}}}
        values = {k: getattr(base, k) for k in base.__dataclass_fields__}
        for key in values:
            if key in custom and not isinstance(custom[key], dict):
                values[key] = custom[key]
        by_type = custom.get("by_type", {})
        if anomaly_type is not None and anomaly_type.value in by_type:
            for key, val in by_type[anomaly_type.value].items():
                if key in values:
                    values[key] = val
        return Thresholds(**values)
