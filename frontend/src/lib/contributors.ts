// Choosing which contributors to put in front of the analyst.
//
// An explanation package carries up to ten dimensions and, on the live Phase A
// spike, 41 contributor rows. Three of them belong above the fold. Which three
// is a decision, and this module is where it is made — once, so the headline
// summary and the "What changed?" panel cannot disagree about what the
// strongest evidence was.
//
// Two rules shape the selection:
//
//   * One contributor per dimension. Ranking purely by contribution share
//     produces "source IP 203.0.113.50, source IP 198.51.100.21, source IP
//     198.51.100.22" — three restatements of one finding, and no answer to
//     what kind of traffic it was.
//
//   * Only dimensions that were actually collected. An UNAVAILABLE dimension
//     was never queried, so it has nothing to contribute and must not appear
//     to have been checked and found clean.

import type { Contributor, ExplanationDimension, ExplanationPackage } from "@/lib/api";
import { contributorsAreShowable, dimensionLabel } from "@/lib/behavior";

/**
 * The order dimensions are offered to the analyst in.
 *
 * Ordered by how much a name tells someone triaging: what kind of traffic it
 * was beats where it went, which beats where it came from. QID and category
 * restate the event name in identifiers rather than words, and source port is
 * almost always high-cardinality noise, so all three rank below the fields
 * that carry meaning on their own.
 */
export const HEADLINE_DIMENSION_ORDER = [
  "event_name",
  "destination_port",
  "source_ip",
  "destination_ip",
  "action",
  "protocol",
  "qid",
  "category",
  "source_port",
  "username",
];

export function dimensionPriority(dimension: string): number {
  const i = HEADLINE_DIMENSION_ORDER.indexOf(dimension);
  return i === -1 ? HEADLINE_DIMENSION_ORDER.length : i;
}

/**
 * How strongly a contributor moved the total, regardless of direction.
 *
 * On a drop the backend reports a negative contribution share, so ranking on
 * the raw value would sort the largest reductions last and headline the
 * smallest. Magnitude is what "largest contributor" means in both directions.
 */
export function contributorStrength(c: Contributor): number {
  const share = c.contribution_share;
  if (share != null && !Number.isNaN(share)) return Math.abs(share);
  const delta = c.absolute_delta;
  if (delta != null && !Number.isNaN(delta)) return Math.abs(delta);
  return 0;
}

/** The single strongest contributor within one dimension, if it has any. */
export function strongestContributor(
  dimension: ExplanationDimension,
): Contributor | null {
  if (!contributorsAreShowable(dimension.availability)) return null;
  let best: Contributor | null = null;
  let bestStrength = -1;
  for (const c of dimension.contributors) {
    const strength = contributorStrength(c);
    // Strictly greater, so an equal-strength later row does not displace an
    // earlier one and the choice stays stable across renders.
    if (strength > bestStrength) {
      best = c;
      bestStrength = strength;
    }
  }
  return best;
}

export interface HeadlineContributor {
  dimension: string;
  dimensionLabel: string;
  contributor: Contributor;
  /** True when this dimension's result was capped at the value limit. */
  truncated: boolean;
}

/**
 * The headline contributors, at most one per dimension.
 *
 * Ordered by dimension priority rather than by strength: the point of the
 * panel is to describe the change from several angles, and a strict
 * strength ordering would put whichever dimension happened to be most
 * concentrated first every time.
 */
export function selectHeadlineContributors(
  packaged: ExplanationPackage | null,
  limit = 3,
): HeadlineContributor[] {
  if (!packaged) return [];

  const picks: HeadlineContributor[] = [];
  for (const dimension of packaged.dimensions) {
    // UNAVAILABLE and FAILED dimensions were never successfully queried.
    if (!contributorsAreShowable(dimension.availability)) continue;
    const contributor = strongestContributor(dimension);
    if (!contributor) continue;
    picks.push({
      dimension: dimension.dimension,
      dimensionLabel: dimensionLabel(dimension.dimension),
      contributor,
      truncated: dimension.availability === "TRUNCATED",
    });
  }

  picks.sort((a, b) => {
    const byPriority = dimensionPriority(a.dimension) - dimensionPriority(b.dimension);
    if (byPriority !== 0) return byPriority;
    return contributorStrength(b.contributor) - contributorStrength(a.contributor);
  });

  return picks.slice(0, limit);
}

/**
 * A contributor's value as an analyst would say it.
 *
 * QRadar returns an identifier and, for some dimensions, a resolved label:
 * protocol 6 is TCP, QID 114500042 is "Firewall - Deny". The label is what
 * means something, so it leads where one exists.
 */
export function contributorDisplayValue(c: Contributor): string {
  return c.label && c.label.trim() ? c.label : c.value;
}

/**
 * The dimension that should be selected when the evidence tab first opens.
 *
 * Highest-priority collected dimension, preferring one collected in full over
 * one that hit the value cap. Falls back to the first dimension of any kind so
 * that a package with nothing collected still shows its own status rather than
 * an empty panel.
 */
export function defaultDimension(
  dimensions: ExplanationDimension[],
): ExplanationDimension | null {
  if (dimensions.length === 0) return null;

  const byPriority = [...dimensions].sort(
    (a, b) => dimensionPriority(a.dimension) - dimensionPriority(b.dimension),
  );

  const available = byPriority.find(
    (d) => d.availability === "AVAILABLE" && d.contributors.length > 0,
  );
  if (available) return available;

  const truncated = byPriority.find(
    (d) => d.availability === "TRUNCATED" && d.contributors.length > 0,
  );
  if (truncated) return truncated;

  return byPriority[0];
}
