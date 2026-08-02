// Turning collected metric buckets into a plottable series, honestly.
//
// Two failure modes this exists to prevent, both of which turn a collection
// problem into an apparent source outage:
//
//   1. Plotting a PARTIAL or MISSING bucket's `average_eps` as an observation.
//      A half-collected interval is an undercount, not a measurement, and drawn
//      on the same line it is indistinguishable from a genuine volume drop.
//   2. Connecting the line across an interval that was never collected at all.
//      A straight segment between two observations asserts that the traffic in
//      between was interpolatable. It was not observed.
//
// Both become `null` points, and the chart is configured with
// `connectNulls: false` so the gap stays visible.

import type { BucketCompleteness, MetricBucket } from "@/lib/api";

export type PointKind = BucketCompleteness | "GAP";

export interface SeriesPoint {
  /** Epoch milliseconds of the bucket start. */
  t: number;
  /** The observed value, or null when this interval was not fully observed. */
  observed: number | null;
  /** Present only so the tooltip can explain why a point is null. */
  kind: PointKind;
  /** The raw reported value for a partially collected bucket, for the tooltip. */
  reported: number | null;
}

/** Gaps are declared when the step exceeds the bucket width by this factor. */
const GAP_FACTOR = 1.5;

/**
 * Build a plottable series from collected buckets.
 *
 * Buckets are assumed ordered by `bucket_start`, which is what the backend
 * returns; they are sorted defensively anyway, because a single out-of-order
 * row would otherwise fabricate a gap and a backwards line segment.
 */
export function buildSeries(buckets: MetricBucket[]): SeriesPoint[] {
  const sorted = [...buckets].sort(
    (a, b) => Date.parse(a.bucket_start) - Date.parse(b.bucket_start),
  );

  const points: SeriesPoint[] = [];
  for (let i = 0; i < sorted.length; i += 1) {
    const b = sorted[i];
    const t = Date.parse(b.bucket_start);
    const complete = b.completeness === "COMPLETE";
    points.push({
      t,
      // Only a whole observation goes on the line.
      observed: complete ? b.average_eps : null,
      kind: b.completeness,
      // Kept separately so the tooltip can state what a partial bucket
      // reported without that number ever reaching the plotted line.
      reported: b.average_eps,
    });

    // A missing row is not represented at all in the data, so the absence has
    // to be inferred from the step between neighbours and made explicit.
    const next = sorted[i + 1];
    if (!next) continue;
    const step = Date.parse(next.bucket_start) - t;
    const width = b.bucket_seconds * 1000;
    if (width > 0 && step > width * GAP_FACTOR) {
      points.push({ t: t + width, observed: null, kind: "GAP", reported: null });
    }
  }
  return points;
}

/** Intervals that were not fully observed, for shading behind the line. */
export function unobservedIntervals(
  buckets: MetricBucket[],
): Array<{ start: number; end: number; kind: PointKind }> {
  const out: Array<{ start: number; end: number; kind: PointKind }> = [];
  const sorted = [...buckets].sort(
    (a, b) => Date.parse(a.bucket_start) - Date.parse(b.bucket_start),
  );
  for (let i = 0; i < sorted.length; i += 1) {
    const b = sorted[i];
    const t = Date.parse(b.bucket_start);
    const width = b.bucket_seconds * 1000;
    if (b.completeness !== "COMPLETE") {
      out.push({ start: t, end: t + width, kind: b.completeness });
    }
    const next = sorted[i + 1];
    if (!next) continue;
    const nextT = Date.parse(next.bucket_start);
    if (width > 0 && nextT - t > width * GAP_FACTOR) {
      out.push({ start: t + width, end: nextT, kind: "GAP" });
    }
  }
  return out;
}

export function kindMeaning(kind: PointKind): string {
  switch (kind) {
    case "COMPLETE":
      return "whole interval observed";
    case "PARTIAL":
      return "partially collected — the reported volume is an undercount, not a measurement";
    case "MISSING":
      return "interval not collected — this is not evidence of silence";
    case "GAP":
      return "no bucket was stored for this interval at all";
    default:
      return "";
  }
}
