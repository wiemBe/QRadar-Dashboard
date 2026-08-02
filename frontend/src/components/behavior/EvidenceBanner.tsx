// One statement of how complete the evidence is.
//
// The previous page said this three times — a KPI card, a status panel and a
// pill per dimension — with the full explanatory paragraph repeated above
// every table. Said once, calmly, at the point where it changes how the
// evidence below should be read.
//
// Four of the six statuses produce a page with no contributors, which looks
// identical to "we looked and nothing stood out". Only this banner
// distinguishes them, so it renders for every status except COMPLETE, where
// there is nothing to qualify.

import type { EvidenceStatus } from "@/lib/api";
import { evidenceTone } from "@/lib/behavior";

const MESSAGES: Record<EvidenceStatus, string | null> = {
  COMPLETE: null,
  PARTIAL:
    "Some QRadar fields were unavailable or truncated. The available evidence is shown below; the dimensions that were not collected have not been checked, and no conclusion follows from the absence of contributors there.",
  UNAVAILABLE:
    "This log source's DSM exposes none of the requested fields for this interval, so no contributor analysis was possible. This is a property of the source, not a transient error.",
  FAILED:
    "Contributor evidence collection failed. Detection metrics and lifecycle data remain available, and this says nothing about what happened during the anomaly — it is worth retrying.",
  PENDING:
    "Contributor evidence is still being collected. The analysis below is not yet available: this is a backlog, not a finding.",
  NOT_REQUESTED:
    "No evidence collection has been requested for this anomaly. Nothing has been looked at.",
};

export function EvidenceBanner({
  status,
  error,
  truncatedDimensions = [],
}: {
  status: EvidenceStatus;
  /** Sanitized collector error, if the backend recorded one. */
  error?: string | null;
  /** Labels of dimensions capped at the value limit. */
  truncatedDimensions?: string[];
}) {
  const message = MESSAGES[status];
  const tone = evidenceTone(status);

  return (
    <>
      {message && (
        <p className={`banner ${tone}`} role={status === "FAILED" ? "alert" : undefined}>
          <span>
            {/* The status word itself, so the banner is not colour-only. */}
            <strong>{status}.</strong> {message}
          </span>
        </p>
      )}

      {truncatedDimensions.length > 0 && (
        <p className="banner warn">
          <span>
            <strong>Truncated.</strong> Some results were limited to the top
            values ({truncatedDimensions.join(", ")}). New, disappeared and
            cardinality counts cannot be determined under a cap — a value can
            look new simply because it fell below the cap in the other window —
            so those counts are withheld rather than reported.
          </span>
        </p>
      )}

      {error && (
        // Backend-sanitized: never a provider response body, never headers.
        // React escapes it again on the way into the DOM.
        <p className="banner crit" role="alert">
          <span>
            <strong>Collection error.</strong> {error}
          </span>
        </p>
      )}
    </>
  );
}
