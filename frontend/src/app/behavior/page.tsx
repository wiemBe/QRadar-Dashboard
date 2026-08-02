// Behavioral overview: the fleet's current observed-vs-expected posture.
//
// The counter that matters most here is `insufficient_data_sources`. A source
// with no adequate baseline is not being judged at all, and a dashboard that
// reports "0 anomalies" over a fleet of unbaselined sources is reporting the
// absence of detection as the absence of problems. It is therefore given its
// own card, its own tone, and an explicit sentence.

import type { Metadata } from "next";

import { StatCard } from "@/components/StatCard";
import {
  ApiError,
  api,
  sevTone,
  type BehaviorSummary,
  type SourceBehavior,
} from "@/lib/api";
import {
  formatMetric,
  formatRatio,
  isUnjudged,
  stateTone,
} from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

export const metadata: Metadata = {
  title: "Behavioral overview",
};

export default async function BehaviorPage() {
  let summary: BehaviorSummary | null = null;
  let sources: SourceBehavior[] = [];
  let error: string | null = null;

  try {
    [summary, sources] = await Promise.all([
      api.behaviorSummary(),
      api.sourceBehaviors(),
    ]);
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
        <h2>Behavioral overview</h2>
        <div className="notice" role="alert">
          {error ?? "Behavioral analytics could not be loaded."}
        </div>
      </>
    );
  }

  const unjudged = summary.insufficient_data_sources;

  return (
    <>
      <h2>Behavioral overview</h2>
      <p className="subtitle">
        What every monitored log source is doing, against what it normally does
        at this weekday and hour.
      </p>

      <div className="grid">
        <StatCard
          label="Open anomalies"
          value={summary.open_anomalies}
          tone={summary.open_anomalies > 0 ? "crit" : undefined}
        />
        <StatCard label="Volume spikes" value={summary.spikes} />
        <StatCard label="Volume drops" value={summary.drops} />
        <StatCard
          label="Silent sources"
          value={summary.silent_sources}
          tone={summary.silent_sources > 0 ? "crit" : undefined}
        />
        <StatCard label="Candidates" value={summary.candidates} />
        <StatCard label="Recovering" value={summary.recovering} />
        {/* Neutral tone, never green: this is an observability gap, and
            colouring it as healthy is the failure mode this card exists to
            prevent. */}
        <StatCard label="Not yet baselined" value={unjudged} />
        <StatCard label="Monitored sources" value={summary.monitored_sources} />
        <StatCard
          label="Evidence pending"
          value={summary.evidence_pending}
          tone={summary.evidence_pending > 0 ? "warn" : undefined}
        />
        <StatCard
          label="Evidence failed"
          value={summary.evidence_failed}
          tone={summary.evidence_failed > 0 ? "crit" : undefined}
        />
      </div>

      {unjudged > 0 && (
        <div className="notice">
          <strong>
            {unjudged} of {summary.monitored_sources} monitored source
            {unjudged === 1 ? "" : "s"} {unjudged === 1 ? "has" : "have"} no
            adequate baseline for the current seasonal cell.
          </strong>{" "}
          {unjudged === 1 ? "It is" : "They are"} not being judged, so no anomaly
          count above covers {unjudged === 1 ? "it" : "them"}. A zero here is the
          absence of a verdict, not a clean bill of health.
        </div>
      )}

      {/* --- highest deviation ------------------------------------------- */}
      <h3>Highest deviation</h3>
      {summary.highest_deviation.length === 0 ? (
        <div className="notice">
          No active anomaly has a measurable deviation ratio right now.
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Detector</th>
              <th>State</th>
              <th>Severity</th>
              <th>Observed</th>
              <th>Expected</th>
              <th>Deviation</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {summary.highest_deviation.map((a) => (
              <tr key={a.id}>
                <td>
                  <a href={`/behavior/sources/${a.log_source_id}`}>
                    {a.log_source_name ?? a.log_source_id}
                  </a>
                </td>
                <td>{a.anomaly_type}</td>
                <td>
                  <span className={`pill ${stateTone(a.state)}`}>{a.state}</span>
                </td>
                <td>
                  <span className={`pill ${sevTone(a.severity)}`}>{a.severity}</span>
                </td>
                <td>{formatMetric(a.observed_value)}</td>
                <td>{formatMetric(a.expected_value)}</td>
                <td>{formatRatio(a.deviation_ratio)}</td>
                <td>
                  <a href={`/anomalies/${a.id}`}>Investigate</a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* --- recently resolved ------------------------------------------- */}
      <h3>Recently resolved</h3>
      {summary.recently_resolved.length === 0 ? (
        <div className="notice">No anomalies have been resolved yet.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Detector</th>
              <th>Severity</th>
              <th>Resolved</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {summary.recently_resolved.map((a) => (
              <tr key={a.id}>
                <td>{a.log_source_name ?? a.log_source_id}</td>
                <td>{a.anomaly_type}</td>
                <td>
                  <span className={`pill ${sevTone(a.severity)}`}>{a.severity}</span>
                </td>
                <td>{formatDateTime(a.resolved_at)}</td>
                <td>
                  <a href={`/anomalies/${a.id}`}>Investigate</a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* --- current source behavior -------------------------------------- */}
      <h3>Source behavior</h3>
      {sources.length === 0 ? (
        <div className="notice">
          No monitored log sources. Enable monitoring on a source to start
          collecting its volume baseline.
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>State</th>
              <th>Observed EPS</th>
              <th>Expected EPS</th>
              <th>Deviation</th>
              <th>Baseline samples</th>
              <th>Baseline completeness</th>
              <th>Last event</th>
              <th>Active anomalies</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.log_source_id}>
                <td>
                  <a href={`/behavior/sources/${s.log_source_id}`}>{s.name}</a>
                </td>
                <td>
                  <span className={`pill ${stateTone(s.state)}`}>{s.state}</span>
                </td>
                <td>{formatMetric(s.observed_eps)}</td>
                {/* An unbaselined source has no expectation at all. Rendering a
                    0 here would invent one and make the observed value look
                    like a spike against it. */}
                <td>
                  {isUnjudged(s.state) ? (
                    <span className="muted">still learning</span>
                  ) : (
                    formatMetric(s.expected_eps)
                  )}
                </td>
                <td>{formatRatio(s.deviation_ratio)}</td>
                <td>{s.baseline_sample_count}</td>
                <td>{(s.baseline_completeness * 100).toFixed(0)}%</td>
                <td>{formatDateTime(s.last_event_at)}</td>
                <td>
                  {s.open_anomaly_count > 0 ? (
                    <a href={`/anomalies?log_source_id=${s.log_source_id}&active_only=true`}>
                      {s.open_anomaly_count}
                    </a>
                  ) : (
                    <span className="muted">0</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
