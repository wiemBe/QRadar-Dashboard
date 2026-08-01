"""Contributor analysis: what changed between two windows.

Pure arithmetic over two sets of dimension aggregates. No I/O and no ORM, so
every ranking and percentage rule is unit-testable against exact inputs; the
collector owns fetching and persistence.

The output answers "what changed during the anomalous interval?" — not "why".
Nothing here infers causation, and nothing here invents a number that was not
measured. A value absent from a window contributes a `None` percentage rather
than a fabricated one, and a dimension the DSM does not populate is reported
UNAVAILABLE rather than as a measured zero.

Counts from the two windows are compared on a **per-second rate basis** when the
windows differ in length. Comparing a 10-minute anomaly against a 30-minute
baseline by raw count would show a "drop" in every single dimension.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import DimensionAvailability
from app.providers.dto import DimensionAggregate


@dataclass
class ContributorDelta:
    """One (dimension, value) pair compared across the two windows."""

    dimension: str
    value: str
    label: str | None
    baseline_count: int
    anomaly_count: int
    absolute_delta: int
    #: (anomaly - baseline) / baseline. None when the value is new: a value with
    #: no baseline has no percentage change, and reporting one would invent it.
    percent_delta: float | None
    anomaly_share: float | None
    baseline_share: float | None
    #: Signed share of the window-over-window change attributable to this value.
    contribution_share: float | None
    baseline_rank: int | None
    anomaly_rank: int | None
    rank: int = 0
    is_new: bool = False
    is_disappeared: bool = False


@dataclass
class DimensionComparison:
    """Per-dimension result: availability, cardinality, and ranked contributors."""

    dimension: str
    availability: DimensionAvailability
    detail: str | None = None
    baseline_distinct_count: int | None = None
    anomaly_distinct_count: int | None = None
    cardinality_ratio: float | None = None
    new_value_count: int = 0
    disappeared_value_count: int = 0
    baseline_top_share: float | None = None
    anomaly_top_share: float | None = None
    truncated: bool = False
    contributors: list[ContributorDelta] = field(default_factory=list)


def _share(count: float, total: float) -> float | None:
    """count / total, or None when the total is zero.

    None means "no share is defined", which is different from a share of zero
    and must not be rendered as one.
    """
    if total <= 0:
        return None
    return count / total


def compare_dimension(
    baseline: DimensionAggregate,
    anomaly: DimensionAggregate,
    *,
    scale: float = 1.0,
    top_n: int = 20,
) -> DimensionComparison:
    """Compare one dimension across the baseline and anomaly windows.

    `scale` normalizes baseline counts onto the anomaly window's duration
    (anomaly_seconds / baseline_seconds). Without it, a longer baseline window
    makes every value look like it collapsed.
    """
    # Availability first. A field the DSM does not populate is a permanent
    # property of the source; a query failure is transient. Both block the
    # comparison, but an operator needs to tell them apart.
    if anomaly.error and not anomaly.available:
        return DimensionComparison(
            dimension=anomaly.dimension,
            availability=DimensionAvailability.UNAVAILABLE,
            detail=anomaly.error,
        )
    if anomaly.error:
        return DimensionComparison(
            dimension=anomaly.dimension,
            availability=DimensionAvailability.FAILED,
            detail=anomaly.error,
        )
    if not anomaly.available:
        return DimensionComparison(
            dimension=anomaly.dimension,
            availability=DimensionAvailability.UNAVAILABLE,
            detail="field is not populated for this log source",
        )

    base_counts = {v.value: v.count * scale for v in baseline.values}
    anom_counts = {v.value: float(v.count) for v in anomaly.values}
    labels = {v.value: v.label for v in (*baseline.values, *anomaly.values) if v.label}

    base_rank = {v.value: i + 1 for i, v in enumerate(baseline.values)}
    anom_rank = {v.value: i + 1 for i, v in enumerate(anomaly.values)}

    base_total = sum(base_counts.values())
    anom_total = sum(anom_counts.values())
    # Total movement, used as the denominator for contribution share. Summing
    # |per-value delta| rather than |total delta| keeps the shares meaningful
    # when some values rose while others fell.
    total_movement = sum(
        abs(anom_counts.get(v, 0.0) - base_counts.get(v, 0.0))
        for v in set(base_counts) | set(anom_counts)
    )

    deltas: list[ContributorDelta] = []
    for value in set(base_counts) | set(anom_counts):
        base = base_counts.get(value, 0.0)
        anom = anom_counts.get(value, 0.0)
        delta = anom - base
        is_new = value not in base_counts
        is_gone = value not in anom_counts
        deltas.append(
            ContributorDelta(
                dimension=anomaly.dimension,
                value=value,
                label=labels.get(value),
                baseline_count=round(base),
                anomaly_count=round(anom),
                absolute_delta=round(delta),
                # A new value has no baseline to be a percentage of.
                percent_delta=(delta / base) if base > 0 else None,
                anomaly_share=_share(anom, anom_total),
                baseline_share=_share(base, base_total),
                contribution_share=_share(delta, total_movement),
                baseline_rank=base_rank.get(value),
                anomaly_rank=anom_rank.get(value),
                is_new=is_new,
                is_disappeared=is_gone,
            )
        )

    # Rank by absolute movement: the largest change is the most interesting
    # regardless of direction. Ties break on value for a stable ordering.
    deltas.sort(key=lambda d: (-abs(d.absolute_delta), d.value))
    for position, delta_row in enumerate(deltas[:top_n], start=1):
        delta_row.rank = position

    return DimensionComparison(
        dimension=anomaly.dimension,
        availability=(
            DimensionAvailability.TRUNCATED
            if (anomaly.truncated or baseline.truncated)
            else DimensionAvailability.AVAILABLE
        ),
        baseline_distinct_count=baseline.distinct_count,
        anomaly_distinct_count=anomaly.distinct_count,
        # None, not infinity, when the baseline saw nothing: "8.4x" and "from
        # nothing" are different findings and must read differently.
        cardinality_ratio=(
            anomaly.distinct_count / baseline.distinct_count
            if baseline.distinct_count > 0
            else None
        ),
        new_value_count=sum(1 for d in deltas if d.is_new),
        disappeared_value_count=sum(1 for d in deltas if d.is_disappeared),
        baseline_top_share=_share(max(base_counts.values(), default=0.0), base_total),
        anomaly_top_share=_share(max(anom_counts.values(), default=0.0), anom_total),
        truncated=anomaly.truncated or baseline.truncated,
        contributors=deltas[:top_n],
    )


def compare_all(
    baseline: list[DimensionAggregate],
    anomaly: list[DimensionAggregate],
    *,
    baseline_seconds: float,
    anomaly_seconds: float,
    top_n: int = 20,
) -> list[DimensionComparison]:
    """Compare every dimension present in the anomaly-window result set."""
    # Rate-normalize the baseline onto the anomaly window's length.
    scale = (anomaly_seconds / baseline_seconds) if baseline_seconds > 0 else 1.0
    by_dimension = {agg.dimension: agg for agg in baseline}
    out: list[DimensionComparison] = []
    for agg in anomaly:
        base = by_dimension.get(agg.dimension) or DimensionAggregate(
            dimension=agg.dimension, available=agg.available
        )
        out.append(compare_dimension(base, agg, scale=scale, top_n=top_n))
    return out
