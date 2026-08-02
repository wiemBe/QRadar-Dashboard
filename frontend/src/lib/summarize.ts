// The one-line incident summary.
//
// Deterministic, and built only from values the API already returned. No model
// is involved: the summary appears at the top of an investigation page, and an
// analyst must be able to trust that it is a restatement of the evidence
// rather than a plausible-sounding paraphrase of it.
//
// Two things it never does:
//
//   * Claim causation. "The largest observed contributors were X and Y" is
//     supported by the contribution shares. "Caused by X" is not, and no
//     amount of contribution share makes it so.
//
//   * Speak for a dimension that was not collected. Only AVAILABLE and
//     TRUNCATED dimensions reach the sentence; an UNAVAILABLE one contributes
//     nothing, because nothing about it was observed.

import type { AnomalyDetail } from "@/lib/api";
import { formatMetric } from "@/lib/behavior";
import {
  contributorDisplayValue,
  selectHeadlineContributors,
  type HeadlineContributor,
} from "@/lib/contributors";

export interface IncidentSummary {
  /** The summary sentence. Always present. */
  text: string;
  /** A stated limitation on the evidence behind it, or null. */
  caveat: string | null;
}

/**
 * How each dimension's value reads inside a sentence.
 *
 * "destination port 445" rather than "Destination port: 445" — the summary is
 * prose, and a label-colon-value pair reads as a form field.
 */
function phrase(pick: HeadlineContributor): string {
  const value = contributorDisplayValue(pick.contributor);
  switch (pick.dimension) {
    case "event_name":
    case "qid":
      return `${value} traffic`;
    case "destination_port":
      return `destination port ${value}`;
    case "source_port":
      return `source port ${value}`;
    case "source_ip":
      return `source IP ${value}`;
    case "destination_ip":
      return `destination IP ${value}`;
    case "action":
      return `action ${value}`;
    case "protocol":
      return `protocol ${value}`;
    case "category":
      return `category ${value}`;
    case "username":
      return `user ${value}`;
    default:
      return `${pick.dimensionLabel.toLowerCase()} ${value}`;
  }
}

/** "a, b and c" — an Oxford-comma-free list, because this is one sentence. */
function joinPhrases(parts: string[]): string {
  if (parts.length === 1) return parts[0];
  if (parts.length === 2) return `${parts[0]} and ${parts[1]}`;
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** The volume clause: what the detector measured, in both directions. */
function volumeClause(anomaly: AnomalyDetail): string {
  const observed = formatMetric(anomaly.observed_value);
  const expected = formatMetric(anomaly.expected_value);

  if (anomaly.anomaly_type === "NO_EVENTS") {
    // No observed value to state: the finding is the absence of one.
    return anomaly.expected_value != null
      ? `No events were observed for a source that normally produces approximately ${expected} EPS.`
      : "No events were observed for this source.";
  }

  // A missing measurement is never described as a change from or to a number.
  if (anomaly.observed_value == null || anomaly.expected_value == null) {
    return "Event volume deviated from its expected baseline.";
  }

  const direction =
    anomaly.anomaly_type === "VOLUME_DROP" ||
    anomaly.observed_value < anomaly.expected_value
      ? "decreased"
      : "increased";

  return `Event volume ${direction} from an expected ${expected} EPS to ${observed} EPS.`;
}

/** The evidence clause: what the collected dimensions showed, if anything. */
function contributorClause(
  anomaly: AnomalyDetail,
  picks: HeadlineContributor[],
): string {
  if (picks.length === 0) {
    switch (anomaly.evidence_status) {
      case "PENDING":
        return "Contributor evidence is still being collected.";
      case "FAILED":
        return "Contributor evidence collection failed, so no contributors are available.";
      case "NOT_REQUESTED":
        return "No contributor evidence has been requested for this anomaly.";
      default:
        return "Contributor evidence is not available.";
    }
  }

  const parts = picks.map(phrase);
  // A drop's contributors are reductions. Calling them "contributors" without
  // saying so reads as though those values increased.
  const isDrop =
    anomaly.anomaly_type === "VOLUME_DROP" ||
    (anomaly.observed_value != null &&
      anomaly.expected_value != null &&
      anomaly.observed_value < anomaly.expected_value);

  if (isDrop) {
    return picks.length === 1
      ? `The largest observed reduction was in ${parts[0]}.`
      : `The largest observed reductions were in ${joinPhrases(parts)}.`;
  }
  return picks.length === 1
    ? `The largest observed contributor was ${parts[0]}.`
    : `The largest observed contributors were ${joinPhrases(parts)}.`;
}

/** The stated limitation on the evidence, where one applies. */
function caveatFor(anomaly: AnomalyDetail, picks: HeadlineContributor[]): string | null {
  if (anomaly.evidence_status === "PARTIAL") {
    return "Some QRadar fields were unavailable or truncated.";
  }
  // Even on an otherwise complete package, a headline drawn from a capped
  // dimension is the top of a list rather than the whole of it.
  if (picks.some((p) => p.truncated)) {
    return "Some results were limited to the top values.";
  }
  return null;
}

/**
 * The deterministic summary for one anomaly.
 *
 * Same input, same sentence, every time — it is a pure function of the API
 * response and holds no clock, no randomness and no model.
 */
export function summarizeAnomaly(anomaly: AnomalyDetail): IncidentSummary {
  const picks = selectHeadlineContributors(anomaly.explanation_package);
  return {
    text: `${volumeClause(anomaly)} ${contributorClause(anomaly, picks)}`,
    caveat: caveatFor(anomaly, picks),
  };
}

/**
 * A text description of the timeline, for readers who cannot see the chart.
 *
 * A chart with an aria-label says only that a chart is present. This states
 * what it shows: the range observed, what was expected, when the anomaly ran,
 * how it ended, and how much of the window was not collected — the last being
 * the fact a sighted reader gets from the shaded gaps.
 */
export function summarizeTimeline({
  observed,
  expected,
  anomalyStart,
  anomalyEnd,
  state,
  incompleteBuckets,
  totalBuckets,
}: {
  observed: (number | null)[];
  expected: number | null;
  anomalyStart: string | null;
  anomalyEnd: string | null;
  state: string;
  incompleteBuckets: number;
  totalBuckets: number;
}): string {
  const measured = observed.filter((v): v is number => v != null);

  if (measured.length === 0) {
    return "No volume was observed in this window, so the chart has no series to describe.";
  }

  const min = Math.min(...measured);
  const max = Math.max(...measured);
  const parts: string[] = [
    `Observed volume ranged from ${formatMetric(min)} to ${formatMetric(max)} EPS across ${totalBuckets} interval${totalBuckets === 1 ? "" : "s"}.`,
  ];

  if (expected != null) {
    parts.push(`The expected baseline was ${formatMetric(expected)} EPS.`);
  }

  if (anomalyStart) {
    parts.push(
      anomalyEnd
        ? `The anomalous interval ran from ${anomalyStart} to ${anomalyEnd}, and the anomaly is now ${state}.`
        : `The anomalous interval began at ${anomalyStart} and has not ended; the anomaly is ${state}.`,
    );
  }

  if (incompleteBuckets > 0) {
    // The gaps are the point: they are absent collection, not observed silence.
    parts.push(
      `${incompleteBuckets} interval${incompleteBuckets === 1 ? " was" : "s were"} not fully collected and ${incompleteBuckets === 1 ? "is" : "are"} shown as a gap rather than as zero traffic.`,
    );
  }

  return parts.join(" ");
}
