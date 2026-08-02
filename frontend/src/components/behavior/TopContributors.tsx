// "What changed?" — the three strongest pieces of evidence, above the fold.
//
// This replaces reading ten contributor tables to find out that the spike was
// denied SMB traffic from one host. The selection is dimension-diverse by
// construction (see lib/contributors.ts), so the three cards describe the
// change from three angles rather than restating one of them three times.
//
// Nothing here asserts cause. A contribution share says how much of the change
// a value accounts for, which is a measurement; "this caused it" is not.

import type { ExplanationPackage } from "@/lib/api";
import {
  contributorDisplayValue,
  selectHeadlineContributors,
} from "@/lib/contributors";
import { formatCount, formatDelta, formatShare } from "@/lib/behavior";

export function TopContributors({
  packaged,
  anomalyType,
  evidenceStatus,
  onViewAllHref = "#evidence",
}: {
  packaged: ExplanationPackage | null;
  anomalyType: string;
  evidenceStatus: string;
  onViewAllHref?: string;
}) {
  const picks = selectHeadlineContributors(packaged);

  if (picks.length === 0) {
    return (
      <div className="notice">
        {anomalyType === "NO_EVENTS"
          ? "No contributor comparison is available for a zero-event interval. There were no events to compare against the baseline, so nothing here has been ruled out."
          : evidenceStatus === "PENDING"
            ? "Contributor evidence is still being collected. This is a backlog, not a finding."
            : evidenceStatus === "FAILED"
              ? "Contributor evidence collection failed, so no contributors are available. The detection metrics and lifecycle below are unaffected."
              : "No contributor evidence is available for this anomaly. No field has been examined, so nothing has been ruled out."}
      </div>
    );
  }

  // A reduction is described as a reduction. The backend reports a negative
  // contribution share on a drop, and a card headed "contribution" showing a
  // negative delta reads as an increase that went wrong.
  const isDrop = anomalyType === "VOLUME_DROP";

  return (
    <>
      <ul className="contributor-cards">
        {picks.map((pick) => {
          const c = pick.contributor;
          return (
            <li key={`${pick.dimension}:${c.value}`} className="card contributor-card">
              <div className="k">{pick.dimensionLabel}</div>
              <p className="contributor-value">{contributorDisplayValue(c)}</p>
              <p className="contributor-change num">
                {formatCount(c.baseline_count)} → {formatCount(c.anomaly_count)}
              </p>
              <p className="contributor-stats">
                <span className={`num ${isDrop ? "crit-text" : ""}`}>
                  {formatDelta(c.absolute_delta)}
                </span>
                <span className="muted"> events · </span>
                <span className="num">
                  {/* Magnitude: the share is signed, and "-95.9% contribution"
                      is not how an analyst describes a reduction. */}
                  {formatShare(
                    c.contribution_share == null
                      ? null
                      : Math.abs(c.contribution_share),
                  )}
                </span>
                <span className="muted">
                  {isDrop ? " of the reduction" : " of the increase"}
                </span>
              </p>
              {pick.truncated && (
                <p className="metric-note">
                  From a result capped at the value limit — the top of the list,
                  not the whole of it.
                </p>
              )}
            </li>
          );
        })}
      </ul>

      <p className="subtitle">
        <a href={onViewAllHref}>View all evidence</a>
      </p>
    </>
  );
}
