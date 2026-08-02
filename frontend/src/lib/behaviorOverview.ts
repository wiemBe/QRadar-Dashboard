// Derived semantics for the behavioral overview.
//
// The overview answers "what needs me now?", which means deciding two things
// the API does not decide for us: which sources count as materially deviating,
// and in what order the things competing for attention should be read.
//
// Both decisions are made here, as pure functions over the API's own values,
// so they are testable and so the page contains no judgement of its own. The
// governing constraint is the same one that governs the rest of Phase A: a
// value that was never measured must never be counted as a measurement.

import type { AnomalyState, AnomalySummary, SourceBehavior } from "@/lib/api";

/**
 * Ratio bounds at which a source is called materially deviating.
 *
 * Symmetric about 1: a source at 2x its expectation and one at half its
 * expectation are equally far from normal, and a drop is not a lesser finding
 * than a spike.
 */
export const HIGH_DEVIATION_RATIO = 2;
export const LOW_DEVIATION_RATIO = 0.5;

/**
 * Whether a source's deviation ratio is a usable measurement at all.
 *
 * Every clause here excludes a case where a number exists but does not mean
 * what a reader would take it to mean:
 *
 *   * INSUFFICIENT_DATA — there is no baseline, so there is no expectation to
 *     deviate from. Counting such a source as "high deviation" would report a
 *     verdict the detector explicitly declined to reach.
 *   * a null observed or expected value — not measured, not zero.
 *   * a null ratio — the backend returns null when expected is zero, because a
 *     ratio against nothing does not exist.
 *   * observed and expected both zero — a quiet source matching a quiet
 *     baseline is the definition of normal, not a deviation.
 *   * no collected bucket — nothing current has been observed, so the ratio
 *     describes no present state.
 */
export function hasMeasurableDeviation(source: SourceBehavior): boolean {
  if (source.state === "INSUFFICIENT_DATA") return false;
  if (source.observed_eps == null || source.expected_eps == null) return false;
  if (source.deviation_ratio == null || Number.isNaN(source.deviation_ratio)) {
    return false;
  }
  if (source.observed_eps === 0 && source.expected_eps === 0) return false;
  if (source.last_bucket_at == null) return false;
  return true;
}

/** A source deviating far enough from its baseline to warrant attention. */
export function isHighDeviation(source: SourceBehavior): boolean {
  if (!hasMeasurableDeviation(source)) return false;
  const ratio = source.deviation_ratio as number;
  return ratio >= HIGH_DEVIATION_RATIO || ratio <= LOW_DEVIATION_RATIO;
}

export function countHighDeviation(sources: SourceBehavior[]): number {
  return sources.filter(isHighDeviation).length;
}

/**
 * How far from normal a ratio is, for ordering only.
 *
 * Folded around 1 so a drop to 0.25x sorts alongside a spike to 4x. A ratio of
 * exactly zero — the source stopped entirely — is the most extreme deviation
 * there is and sorts above every finite one. A null ratio is not a small
 * deviation, it is no measurement, and sorts last.
 */
export function deviationMagnitude(ratio: number | null | undefined): number {
  if (ratio == null || Number.isNaN(ratio)) return 0;
  if (ratio === 0) return Number.POSITIVE_INFINITY;
  if (ratio < 0) return Number.POSITIVE_INFINITY;
  return ratio >= 1 ? ratio : 1 / ratio;
}

const SEVERITY_RANK: Record<string, number> = {
  CRITICAL: 5,
  HIGH: 4,
  MEDIUM: 3,
  LOW: 2,
  INFO: 1,
};

export function severityRank(severity: string | null | undefined): number {
  return severity ? (SEVERITY_RANK[severity] ?? 0) : 0;
}

// --- needs attention -------------------------------------------------------

/**
 * Attention priorities, lowest number read first.
 *
 * Ordered by how much of an analyst's time the row deserves, not by how
 * alarming it looks. A confirmed incident outranks a candidate; a candidate
 * outranks one that is already recovering; a source we cannot judge at all
 * comes last, because it is a gap to close rather than an event to work.
 */
export const PRIORITY = {
  OPEN: 1,
  CANDIDATE: 2,
  RECOVERING: 3,
  NO_EVENTS: 4,
  EVIDENCE_FAILED: 5,
  HIGH_DEVIATION: 6,
  INSUFFICIENT_DATA: 7,
} as const;

export interface AttentionRow {
  key: string;
  sourceId: string;
  sourceName: string;
  /** Lifecycle state of the anomaly, or of the source when there is none. */
  state: AnomalyState;
  /** Why this row is here, in the analyst's words. */
  issue: string;
  observed: number | null;
  expected: number | null;
  deviation: number | null;
  severity: string | null;
  /** Most recent moment this row's subject changed or was observed. */
  at: string | null;
  href: string;
  priority: number;
}

function anomalyPriority(anomaly: AnomalySummary): number {
  // Lifecycle state is read before detector type: an OPEN no-events incident
  // is a confirmed outage, and ranking it below a candidate volume spike
  // because of its detector would bury the worse finding.
  if (anomaly.state === "OPEN") return PRIORITY.OPEN;
  if (anomaly.state === "CANDIDATE") return PRIORITY.CANDIDATE;
  if (anomaly.state === "RECOVERING") return PRIORITY.RECOVERING;
  if (anomaly.anomaly_type === "NO_EVENTS") return PRIORITY.NO_EVENTS;
  if (anomaly.evidence_status === "FAILED") return PRIORITY.EVIDENCE_FAILED;
  return PRIORITY.HIGH_DEVIATION;
}

function anomalyIssue(anomaly: AnomalySummary): string {
  if (anomaly.anomaly_type === "NO_EVENTS") return "No events received";
  if (anomaly.evidence_status === "FAILED") return "Evidence collection failed";
  if (anomaly.anomaly_type === "VOLUME_DROP") return "Volume below baseline";
  if (anomaly.anomaly_type === "VOLUME_SPIKE") return "Volume above baseline";
  return anomaly.anomaly_type;
}

/**
 * The rows competing for the analyst's attention, in the order to read them.
 *
 * Built from the active anomalies first, then topped up with sources that
 * carry no anomaly but still need someone to look: those deviating materially
 * without having tripped a detector, and those with no baseline at all.
 *
 * A source in a NORMAL state with no anomaly never appears. The table is a
 * worklist, and padding it with healthy sources to fill space is what turned
 * the previous overview into a fleet inventory.
 */
export function buildAttentionRows(
  sources: SourceBehavior[],
  anomalies: AnomalySummary[],
): AttentionRow[] {
  const rows: AttentionRow[] = [];
  const claimed = new Set<string>();

  for (const anomaly of anomalies) {
    // RESOLVED and NORMAL anomalies are history, not work.
    if (!["OPEN", "CANDIDATE", "RECOVERING"].includes(anomaly.state)) continue;
    claimed.add(anomaly.log_source_id);
    rows.push({
      key: `anomaly:${anomaly.id}`,
      sourceId: anomaly.log_source_id,
      sourceName: anomaly.log_source_name ?? anomaly.log_source_id,
      state: anomaly.state,
      issue: anomalyIssue(anomaly),
      observed: anomaly.observed_value,
      expected: anomaly.expected_value,
      deviation: anomaly.deviation_ratio,
      severity: anomaly.severity,
      at: anomaly.anomaly_start ?? anomaly.detected_at,
      href: `/anomalies/${anomaly.id}`,
      priority: anomalyPriority(anomaly),
    });
  }

  for (const source of sources) {
    if (claimed.has(source.log_source_id)) continue;

    if (source.state === "INSUFFICIENT_DATA") {
      rows.push({
        key: `source:${source.log_source_id}`,
        sourceId: source.log_source_id,
        sourceName: source.name,
        state: source.state,
        issue: "No adequate baseline",
        observed: source.observed_eps,
        // Not zero and not the observed value: an unbaselined source has no
        // expectation, and rendering one would invent the comparison.
        expected: null,
        deviation: null,
        severity: null,
        at: source.last_event_at,
        href: `/behavior/sources/${source.log_source_id}`,
        priority: PRIORITY.INSUFFICIENT_DATA,
      });
      continue;
    }

    if (isHighDeviation(source)) {
      rows.push({
        key: `source:${source.log_source_id}`,
        sourceId: source.log_source_id,
        sourceName: source.name,
        state: source.state,
        issue: "Deviating from baseline",
        observed: source.observed_eps,
        expected: source.expected_eps,
        deviation: source.deviation_ratio,
        severity: null,
        at: source.last_event_at,
        href: `/behavior/sources/${source.log_source_id}`,
        priority: PRIORITY.HIGH_DEVIATION,
      });
    }
  }

  return rows.sort(compareAttention);
}

/** Total order: priority, then severity, then deviation, then recency. */
export function compareAttention(a: AttentionRow, b: AttentionRow): number {
  if (a.priority !== b.priority) return a.priority - b.priority;
  const sev = severityRank(b.severity) - severityRank(a.severity);
  if (sev !== 0) return sev;
  const dev = deviationMagnitude(b.deviation) - deviationMagnitude(a.deviation);
  if (dev !== 0 && !Number.isNaN(dev)) return dev;
  const at = (b.at ? Date.parse(b.at) : 0) - (a.at ? Date.parse(a.at) : 0);
  if (at !== 0) return at;
  // Names last, so the order is total and the table never reshuffles between
  // renders of identical data.
  return a.sourceName.localeCompare(b.sourceName);
}

// --- health distribution ---------------------------------------------------

export interface HealthGroup {
  key: string;
  label: string;
  count: number;
  tone: "crit" | "warn" | "ok" | "";
}

/**
 * The fleet's states, grouped for a single compact bar.
 *
 * Grouped strictly by the lifecycle state the backend assigned, so the bar
 * reports the detector's verdicts rather than a second opinion formed in the
 * browser. INSUFFICIENT_DATA is its own group and is never folded into
 * "Normal" — the whole point of the group is that those sources have no
 * verdict, and merging them would manufacture one.
 */
export function healthDistribution(sources: SourceBehavior[]): HealthGroup[] {
  const count = (states: string[]) =>
    sources.filter((s) => states.includes(s.state)).length;

  const groups: HealthGroup[] = [
    {
      key: "attention",
      label: "Attention required",
      count: count(["OPEN", "CANDIDATE"]),
      tone: "crit",
    },
    { key: "recovering", label: "Recovering", count: count(["RECOVERING"]), tone: "warn" },
    {
      key: "insufficient",
      label: "Insufficient data",
      count: count(["INSUFFICIENT_DATA"]),
      // Never green and never red: no verdict was reached.
      tone: "",
    },
    { key: "suppressed", label: "Suppressed", count: count(["SUPPRESSED"]), tone: "" },
    { key: "normal", label: "Normal", count: count(["NORMAL", "RESOLVED"]), tone: "ok" },
  ];
  return groups.filter((g) => g.count > 0);
}

/**
 * The distribution as a sentence, for the chart's text equivalent.
 *
 * The silent count comes from the backend summary rather than being inferred
 * from the source list: silence is defined by the collector's own rules about
 * event recency, and re-deriving it here from an EPS of zero would produce a
 * second, quietly different definition.
 */
export function healthSummaryText(
  groups: HealthGroup[],
  total: number,
  silent: number,
): string {
  if (total === 0) return "No monitored log sources.";
  const parts = groups.map((g) => `${g.count} ${g.label.toLowerCase()}`);
  const base = `${total} monitored source${total === 1 ? "" : "s"}: ${parts.join(", ")}.`;
  return silent > 0
    ? `${base} ${silent} ${silent === 1 ? "is" : "are"} currently silent.`
    : base;
}
