// Derived semantics for the source behavior page.
//
// Three judgements the API does not make for us — how far back to look, how
// trustworthy the baseline is, and whether collection is keeping up — made
// here as pure functions so they are testable and so the page contains no
// reasoning of its own.
//
// The constraint that governs all three is the one that governs Phase A: a
// value that was never measured must never be presented as a measurement, and
// a collection problem must never be presented as source behavior.

import type { AnomalySummary, BaselineCell, MetricBucket, SourceBehavior } from "@/lib/api";
import { formatMetric } from "@/lib/behavior";

// --- time range ------------------------------------------------------------

/**
 * Selectable windows.
 *
 * All four are safe against the metrics endpoint, which caps `limit` at 5000:
 * 24 hours of 60-second buckets is 1440 rows, well inside the cap. Nothing
 * here issues an unbounded request — `since` is always computed and sent.
 */
export const RANGE_HOURS = [1, 6, 12, 24] as const;
export type RangeHours = (typeof RANGE_HOURS)[number];

export const DEFAULT_RANGE: RangeHours = 6;

/** The row cap sent with a metric request. Bounded, never omitted. */
export const METRIC_LIMIT = 2000;

export function parseRange(value: string | undefined): RangeHours {
  const n = Number.parseInt(value ?? "", 10);
  return (RANGE_HOURS as readonly number[]).includes(n)
    ? (n as RangeHours)
    : DEFAULT_RANGE;
}

export function rangeLabel(hours: RangeHours): string {
  return hours === 1 ? "1 hour" : `${hours} hours`;
}

// --- baseline quality ------------------------------------------------------

export type BaselineStatus =
  | "RELIABLE"
  | "STILL_LEARNING"
  | "DEGENERATE"
  | "INCOMPLETE";

export interface BaselineQuality {
  status: BaselineStatus;
  label: string;
  tone: "ok" | "warn" | "";
  /** One sentence an analyst can act on. */
  explanation: string;
  /** The seasonal cell this source is currently judged against, if identifiable. */
  cell: BaselineCell | null;
}

/**
 * The baseline cell the current expectation came from.
 *
 * Identified by matching the expected value against each cell's median rather
 * than by computing the current weekday and hour in the browser. The backend
 * owns the seasonal-cell convention, and re-deriving it here from a local
 * clock would silently disagree with it across timezones and week boundaries.
 */
export function currentCell(
  cells: BaselineCell[],
  expectedEps: number | null | undefined,
): BaselineCell | null {
  if (expectedEps == null) return null;
  const match = cells.find((c) => Math.abs(c.median - expectedEps) < 1e-6);
  return match ?? null;
}

export function baselineQuality(
  source: SourceBehavior,
  cells: BaselineCell[],
): BaselineQuality {
  const cell = currentCell(cells, source.expected_eps);
  const samples = source.baseline_sample_count;

  // No expectation at all: the detector has declined to judge this source, and
  // nothing about the cells below changes that.
  if (source.state === "INSUFFICIENT_DATA" || source.expected_eps == null) {
    return {
      status: "STILL_LEARNING",
      label: "Still learning",
      tone: "",
      explanation:
        samples > 0
          ? `Only ${samples} complete sample${samples === 1 ? "" : "s"} for this weekday and hour so far — not enough to judge this source against. It is not being assessed, which is not the same as being healthy.`
          : "No complete samples for this weekday and hour yet. This source is not being assessed, which is not the same as being healthy.",
      cell,
    };
  }

  // MAD of zero is a property of a steady low-volume source, not a fault, and
  // is stated as such: the verdict still rests on the deterministic bound.
  if (cell && cell.mad === 0) {
    return {
      status: "DEGENERATE",
      label: "Zero variability",
      tone: "warn",
      explanation: `Baseline variability is zero — more than half of the ${cell.sample_count} samples for this weekday and hour were identical, which is normal for a steady low-volume source. The robust z-score is therefore an artefact and is not used; material deviations fall back to the deterministic expected-band test, which is sound but weaker evidence.`,
      cell,
    };
  }

  if (cell && !cell.is_reliable) {
    return {
      status: "INCOMPLETE",
      label: "Incomplete",
      tone: "warn",
      explanation: `The baseline for this weekday and hour is built from ${cell.sample_count} sample${cell.sample_count === 1 ? "" : "s"} at ${(cell.completeness * 100).toFixed(0)}% collection completeness, and the backend has not marked it reliable. Verdicts against it are weaker than they appear.`,
      cell,
    };
  }

  return {
    status: "RELIABLE",
    label: "Reliable",
    tone: "ok",
    explanation: cell
      ? `Baseline is based on ${cell.sample_count} complete sample${cell.sample_count === 1 ? "" : "s"} for this weekday and hour.`
      : `Baseline is based on ${samples} complete sample${samples === 1 ? "" : "s"} for this seasonal cell.`,
    cell,
  };
}

// --- collection health -----------------------------------------------------

export type CollectionStatus = "CURRENT" | "DELAYED" | "PARTIAL" | "NONE";

export interface CollectionHealth {
  status: CollectionStatus;
  label: string;
  tone: "ok" | "warn" | "crit" | "";
  explanation: string;
  /** Buckets present in the window but not fully observed. */
  incomplete: number;
  /** Buckets that exist for the window. */
  total: number;
  /** Buckets the window should contain at this bucket width. */
  expected: number;
  /** Intervals with no stored bucket at all. */
  absent: number;
  /** Age of the newest collected bucket, in seconds. Null when none exists. */
  lagSeconds: number | null;
}

/** Beyond this many bucket widths behind, collection is called delayed. */
const DELAY_FACTOR = 3;

/**
 * How healthy collection is, from the buckets themselves.
 *
 * Deliberately says nothing about whether the *source* is quiet. A collector
 * that has stopped and a source that has stopped produce the same empty chart,
 * and conflating them is the failure this whole section exists to prevent.
 *
 * There is no FAILED status: the API exposes no collection-failure counter,
 * and inventing one from an absence of buckets would report a fault we have
 * not observed.
 */
export function collectionHealth(
  buckets: MetricBucket[],
  lastBucketAt: string | null,
  /** Seconds the chart covers, so absent intervals can be counted. */
  windowSeconds: number,
  now: number = Date.now(),
): CollectionHealth {
  const total = buckets.length;
  const incomplete = buckets.filter((b) => b.completeness !== "COMPLETE").length;

  // An interval with no stored bucket is as uncollected as one stored with
  // PARTIAL completeness, and the chart shades both. Counting only the rows
  // that exist would report "115 of 115 fully observed" over a window the
  // chart draws as mostly gaps — two true statements that mislead together.
  const width = buckets[buckets.length - 1]?.bucket_seconds ?? 60;
  const expected = width > 0 ? Math.round(windowSeconds / width) : total;
  const absent = Math.max(0, expected - total);

  const lastAt = lastBucketAt ? Date.parse(lastBucketAt) : null;
  const lagSeconds =
    lastAt != null && !Number.isNaN(lastAt) ? Math.max(0, (now - lastAt) / 1000) : null;

  if (total === 0 && lagSeconds == null) {
    return {
      status: "NONE",
      label: "No recent collection",
      tone: "crit",
      explanation:
        "No metric buckets have been collected for this source. This is an absence of collection, not an observation that the source is silent.",
      incomplete,
      total,
      expected,
      absent,
      lagSeconds,
    };
  }

  if (lagSeconds != null && lagSeconds > width * DELAY_FACTOR) {
    return {
      status: "DELAYED",
      label: "Delayed",
      tone: "warn",
      explanation: `The newest collected interval is ${Math.round(lagSeconds / 60)} minutes old, more than ${DELAY_FACTOR} collection intervals behind. Recent volume for this source is not yet known — this says nothing about whether it is sending events.`,
      incomplete,
      total,
      expected,
      absent,
      lagSeconds,
    };
  }

  const uncollected = incomplete + absent;
  if (uncollected > 0) {
    const bits: string[] = [];
    if (absent > 0) bits.push(`${absent} interval${absent === 1 ? " has" : "s have"} no stored bucket at all`);
    if (incomplete > 0)
      bits.push(`${incomplete} ${incomplete === 1 ? "was" : "were"} only partly collected`);
    return {
      status: "PARTIAL",
      label: "Partial",
      tone: "warn",
      explanation: `Of the ${expected} intervals this window covers, ${bits.join(" and ")}. They are drawn as gaps rather than as zero, and never enter a baseline — their absence says nothing about whether the source was sending events.`,
      incomplete,
      total,
      expected,
      absent,
      lagSeconds,
    };
  }

  return {
    status: "CURRENT",
    label: "Current",
    tone: "ok",
    explanation: `All ${total} intervals in this window were fully collected.`,
    incomplete,
    total,
    expected,
    absent,
    lagSeconds,
  };
}

// --- anomaly partitioning --------------------------------------------------

/**
 * States that mean an incident is still live.
 *
 * Derived from the lifecycle state, never from `resolved_at IS NULL`. A NORMAL
 * row with no resolved timestamp is not an active incident, and treating the
 * absence of a timestamp as evidence of activity is the exact defect the
 * backend fixed in ec5d352.
 */
export const ACTIVE_STATES = ["CANDIDATE", "OPEN", "RECOVERING"] as const;

export function isActiveAnomaly(anomaly: AnomalySummary): boolean {
  return (ACTIVE_STATES as readonly string[]).includes(anomaly.state);
}

const ACTIVE_ORDER: Record<string, number> = {
  OPEN: 0,
  CANDIDATE: 1,
  RECOVERING: 2,
};

export function partitionAnomalies(anomalies: AnomalySummary[]): {
  active: AnomalySummary[];
  recent: AnomalySummary[];
} {
  const active = anomalies
    .filter(isActiveAnomaly)
    .sort(
      (a, b) =>
        (ACTIVE_ORDER[a.state] ?? 9) - (ACTIVE_ORDER[b.state] ?? 9) ||
        Date.parse(b.anomaly_start ?? b.detected_at) -
          Date.parse(a.anomaly_start ?? a.detected_at),
    );

  const recent = anomalies
    .filter((a) => !isActiveAnomaly(a))
    .sort(
      (a, b) =>
        Date.parse(b.resolved_at ?? b.anomaly_end ?? b.detected_at) -
        Date.parse(a.resolved_at ?? a.anomaly_end ?? a.detected_at),
    );

  return { active, recent };
}

// --- chart summary ---------------------------------------------------------

/**
 * The source timeline as prose, for readers who cannot see the chart.
 *
 * States the range covered, the current and expected values, the extremes, and
 * — critically — how much of the window was not collected, which is the fact a
 * sighted reader takes from the shaded gaps.
 */
export function summarizeSourceTimeline({
  hours,
  observed,
  currentEps,
  expectedEps,
  missing,
  partial,
  anomalyCount,
}: {
  hours: number;
  observed: (number | null)[];
  currentEps: number | null;
  expectedEps: number | null;
  missing: number;
  partial: number;
  anomalyCount: number;
}): string {
  const parts: string[] = [`Observed volume over the last ${rangeLabel(hours as RangeHours)}.`];

  const measured = observed.filter((v): v is number => v != null);
  if (measured.length === 0) {
    parts.push(
      "No interval in this window was fully collected, so there is no observed series to describe.",
    );
  } else {
    parts.push(
      `Values ranged from ${formatMetric(Math.min(...measured))} to ${formatMetric(Math.max(...measured))} EPS across ${measured.length} fully collected interval${measured.length === 1 ? "" : "s"}.`,
    );
  }

  parts.push(
    currentEps != null
      ? `The most recent observation is ${formatMetric(currentEps)} EPS.`
      : "The most recent interval has no trustworthy observation.",
  );

  parts.push(
    expectedEps != null
      ? `The expected baseline is ${formatMetric(expectedEps)} EPS.`
      : "No baseline expectation exists yet for this source; it is still learning.",
  );

  if (missing > 0 || partial > 0) {
    const bits: string[] = [];
    if (missing > 0) bits.push(`${missing} interval${missing === 1 ? "" : "s"} not collected`);
    if (partial > 0)
      bits.push(`${partial} interval${partial === 1 ? "" : "s"} only partly collected`);
    parts.push(
      `${bits.join(" and ")} — drawn as gaps rather than as zero traffic, because they were not observed.`,
    );
  }

  parts.push(
    anomalyCount === 0
      ? "No anomalies were detected in this window."
      : `${anomalyCount} anomal${anomalyCount === 1 ? "y was" : "ies were"} detected in this window.`,
  );

  return parts.join(" ");
}
