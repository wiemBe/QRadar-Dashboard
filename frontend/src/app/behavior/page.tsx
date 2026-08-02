// Behavioral overview: an operational landing page.
//
// The question this page answers is "what needs me now?", and the previous
// version answered it badly — not by omitting anything, but by saying
// everything at once. Ten equally weighted counters, then three tables, the
// last of which listed all 59 monitored sources, 55 of them quiet and normal.
// The rows that required action were outnumbered roughly fourteen to one by
// rows that did not, and sat below them.
//
// So: four counters, a worklist, what changed recently, and a proportion. The
// inventory moved to /behavior/sources, where an analyst goes deliberately.
//
// The counter that still matters most is `insufficient_data_sources`. A source
// with no adequate baseline is not being judged at all, and a dashboard
// reporting "0 anomalies" over a fleet of unbaselined sources is reporting the
// absence of detection as the absence of problems. It keeps a primary card,
// its own neutral tone — never green — and an explicit sentence.

import type { Metadata } from "next";
import Link from "next/link";

import { HealthDistribution } from "@/components/behavior/HealthDistribution";
import { StatCard } from "@/components/StatCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import {
  ApiError,
  api,
  sevTone,
  type AnomalySummary,
  type BehaviorSummary,
  type SourceBehavior,
} from "@/lib/api";
import {
  buildAttentionRows,
  countHighDeviation,
  healthDistribution,
  healthSummaryText,
} from "@/lib/behaviorOverview";
import { formatMetric, formatRatio, stateTone } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

export const metadata: Metadata = {
  title: "Overview",
};

/** How many rows the worklist and the recent list show before linking on. */
const ATTENTION_LIMIT = 8;
const RECENT_LIMIT = 6;

export default async function BehaviorPage() {
  let summary: BehaviorSummary | null = null;
  let sources: SourceBehavior[] = [];
  let active: AnomalySummary[] = [];
  let error: string | null = null;

  try {
    // The active-anomaly list supplies the detector type, severity and
    // evidence status the worklist ranks on; its failure must not blank the
    // page, so it is tolerated separately.
    const [summaryResult, sourcesResult, activeResult] = await Promise.all([
      api.behaviorSummary(),
      api.sourceBehaviors(),
      api.anomalies({ active_only: true, limit: 50 }).catch(() => null),
    ]);
    summary = summaryResult;
    sources = sourcesResult;
    active = activeResult?.items ?? [];
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? "You do not have permission to view behavioral analytics."
        : err instanceof ApiError && err.status === 401
          ? "Your session has expired. Sign in again to continue."
          : "Behavioral analytics could not be loaded. The backend may be unreachable.";
  }

  if (error || !summary) {
    return (
      <>
        <PageHeader title="Behavioral overview" />
        <div className="notice" role="alert">
          {error ?? "Behavioral analytics could not be loaded."}
        </div>
      </>
    );
  }

  const unjudged = summary.insufficient_data_sources;
  const highDeviation = countHighDeviation(sources);
  const attention = buildAttentionRows(sources, active);
  const groups = healthDistribution(sources);
  const recent = summary.recently_resolved.slice(0, RECENT_LIMIT);

  return (
    <>
      <PageHeader
        title="Behavioral overview"
        description="What every monitored log source is doing, against what it normally does at this weekday and hour."
      />

      {/* --- level 1: the four counters that decide whether to keep reading -- */}
      <div className="grid-4">
        <StatCard
          label="Open anomalies"
          value={summary.open_anomalies}
          tone={summary.open_anomalies > 0 ? "crit" : undefined}
        />
        <StatCard
          label="Silent sources"
          value={summary.silent_sources}
          tone={summary.silent_sources > 0 ? "crit" : undefined}
        />
        <StatCard
          label="High deviation"
          value={highDeviation}
          tone={highDeviation > 0 ? "warn" : undefined}
          note="Sources at or beyond 2x, or at or below half, their expected volume."
        />
        {/* Neutral tone, never green: this is an observability gap, and
            colouring it as healthy is the failure mode this card exists to
            prevent. */}
        <StatCard
          label="Insufficient data"
          value={unjudged}
          note="Not being judged — a zero above does not cover these."
        />
      </div>

      {/* --- level 2: the secondary counts, as one compact strip ------------ */}
      <dl className="status-strip">
        <div>
          <dt>Spikes</dt>
          <dd className="num">{summary.spikes}</dd>
        </div>
        <div>
          <dt>Drops</dt>
          <dd className="num">{summary.drops}</dd>
        </div>
        <div>
          <dt>Candidates</dt>
          <dd className="num">{summary.candidates}</dd>
        </div>
        <div>
          <dt>Recovering</dt>
          <dd className="num">{summary.recovering}</dd>
        </div>
        <div>
          <dt>Recently resolved</dt>
          <dd className="num">{summary.recently_resolved.length}</dd>
        </div>
        <div>
          <dt>Evidence pending</dt>
          <dd className="num">{summary.evidence_pending}</dd>
        </div>
        <div>
          <dt>Evidence failed</dt>
          <dd className={summary.evidence_failed > 0 ? "num crit-text" : "num"}>
            {summary.evidence_failed}
          </dd>
        </div>
      </dl>

      {unjudged > 0 && (
        <p className="banner">
          <span>
            <strong>
              {unjudged} of {summary.monitored_sources} monitored source
              {unjudged === 1 ? "" : "s"} {unjudged === 1 ? "has" : "have"} no
              adequate baseline for the current seasonal cell.
            </strong>{" "}
            {unjudged === 1 ? "It is" : "They are"} not being judged, so no
            anomaly count above covers {unjudged === 1 ? "it" : "them"}. A zero
            here is the absence of a verdict, not a clean bill of health.
          </span>
        </p>
      )}

      {/* --- needs attention ------------------------------------------------ */}
      <section>
        <h2 className="section-title">Needs attention</h2>
        {attention.length === 0 ? (
          <div className="notice">
            Nothing is currently anomalous, deviating materially from its
            baseline, or waiting on a baseline. Sources that are simply normal
            are not listed here.
          </div>
        ) : (
          <>
            <TableScroll label="Sources needing attention">
              <table>
                <caption className="sr-only">
                  Sources needing attention, ordered by lifecycle state, then
                  severity, then deviation, then recency.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Source</th>
                    <th scope="col">State</th>
                    <th scope="col">Issue</th>
                    <th scope="col">Observed / expected</th>
                    <th scope="col">Deviation</th>
                    <th scope="col">Started</th>
                    <th scope="col">
                      <span className="sr-only">Detail</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {attention.slice(0, ATTENTION_LIMIT).map((row) => (
                    <tr key={row.key}>
                      <td>
                        <a href={`/behavior/sources/${row.sourceId}`}>
                          {row.sourceName}
                        </a>
                      </td>
                      <td>
                        <span className={`pill ${stateTone(row.state)}`}>
                          {row.state}
                        </span>
                      </td>
                      <td>
                        {row.issue}
                        {row.severity && (
                          <>
                            {" "}
                            <span className={`pill pill-quiet ${sevTone(row.severity)}`}>
                              {row.severity}
                            </span>
                          </>
                        )}
                      </td>
                      <td className="num">
                        {formatMetric(row.observed)}
                        {" / "}
                        {/* An unbaselined source has no expectation; an em
                            dash says so rather than inventing a zero. */}
                        {row.expected == null ? (
                          <span className="muted">—</span>
                        ) : (
                          formatMetric(row.expected)
                        )}
                      </td>
                      <td className="num">{formatRatio(row.deviation)}</td>
                      <td className="num">{formatDateTime(row.at)}</td>
                      <td>
                        <a href={row.href}>
                          {row.href.startsWith("/anomalies") ? "Investigate" : "Detail"}
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
            {attention.length > ATTENTION_LIMIT && (
              <p className="subtitle">
                Showing the {ATTENTION_LIMIT} highest-priority of{" "}
                {attention.length}.{" "}
                <Link href="/anomalies?active_only=true">See all active anomalies</Link>.
              </p>
            )}
          </>
        )}
      </section>

      {/* --- recently resolved ---------------------------------------------- */}
      <section>
        <h2 className="section-title">Recently resolved</h2>
        {recent.length === 0 ? (
          <div className="notice">No anomalies have been resolved yet.</div>
        ) : (
          <TableScroll label="Recently resolved anomalies">
            <table>
              <caption className="sr-only">
                Anomalies that closed after sustained normal behavior.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Source</th>
                  <th scope="col">Detector</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Resolved</th>
                  <th scope="col">
                    <span className="sr-only">Detail</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {recent.map((a) => (
                  <tr key={a.id}>
                    <td>{a.log_source_name ?? a.log_source_id}</td>
                    <td>{a.anomaly_type}</td>
                    <td>
                      <span className={`pill pill-quiet ${sevTone(a.severity)}`}>
                        {a.severity}
                      </span>
                    </td>
                    <td className="num">{formatDateTime(a.resolved_at)}</td>
                    <td>
                      <a href={`/anomalies/${a.id}`}>Investigate</a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </section>

      {/* --- fleet proportion ------------------------------------------------ */}
      <section>
        <h2 className="section-title">Source health</h2>
        <HealthDistribution
          groups={groups}
          total={sources.length}
          summary={healthSummaryText(groups, sources.length, summary.silent_sources)}
        />
        <p className="subtitle">
          <Link href="/behavior/sources">View all {sources.length} sources</Link>
        </p>
      </section>
    </>
  );
}
