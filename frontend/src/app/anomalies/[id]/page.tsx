// Anomaly investigation detail.
//
// The page answers one question: what changed during the anomalous interval?
// Everything on it is server-rendered from the backend's own verdict — the UI
// never recomputes detection logic, and never fills a missing measurement with
// a plausible number.

import type { Metadata } from "next";

import { ContributorTable } from "@/components/behavior/ContributorTable";
import { DetectionSummary } from "@/components/behavior/DetectionSummary";
import { DimensionSummary } from "@/components/behavior/DimensionSummary";
import { EvidenceStatusPanel } from "@/components/behavior/EvidenceStatusPanel";
import { LifecycleHistory } from "@/components/behavior/LifecycleHistory";
import { QueryProvenance } from "@/components/behavior/QueryProvenance";
import { VolumeChart } from "@/components/behavior/VolumeChart";
import { ApiError, api, sevTone, type AnomalyDetail, type MetricBucket } from "@/lib/api";
import {
  formatDuration,
  formatMetric,
  formatRatio,
  stateMeaning,
  stateTone,
} from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

export const metadata: Metadata = {
  title: "Anomaly investigation",
};

/** How much surrounding context the timeline shows around the anomaly. */
const CONTEXT_HOURS = 6;

export default async function AnomalyDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let anomaly: AnomalyDetail | null = null;
  let status = 0;
  try {
    anomaly = await api.anomaly(id);
  } catch (err) {
    status = err instanceof ApiError ? err.status : 0;
  }

  if (!anomaly) {
    return (
      <>
        <h2>Anomaly investigation</h2>
        <div className="notice" role="alert">
          {status === 404
            ? "This anomaly does not exist."
            : status === 403
              ? "You do not have permission to view anomaly evidence."
              : status === 401
                ? "Your session has expired. Sign in again to continue."
                : "Could not load this anomaly. The backend may be unreachable."}
        </div>
      </>
    );
  }

  // The timeline is context around the incident, so the window is anchored to
  // the anomaly rather than to now — an anomaly from last week must still
  // render its own interval.
  const anchor = Date.parse(anomaly.anomaly_start ?? anomaly.detected_at);
  const since = new Date(anchor - CONTEXT_HOURS * 3600_000).toISOString();
  const until = new Date(
    Math.max(
      Date.parse(anomaly.anomaly_end ?? anomaly.resolved_at ?? anomaly.detected_at),
      anchor,
    ) +
      CONTEXT_HOURS * 3600_000,
  ).toISOString();

  // A failed metric fetch must not take down the whole investigation: the
  // detection summary, lifecycle and evidence are the substance of the page.
  let buckets: MetricBucket[] = [];
  let bucketsFailed = false;
  try {
    buckets = await api.sourceMetrics(anomaly.log_source_id, { since, until, limit: 2000 });
  } catch {
    bucketsFailed = true;
  }

  const pkg = anomaly.explanation_package;
  const detection = anomaly.detection;

  return (
    <>
      <h2>
        {anomaly.log_source_name ?? "Unknown source"} · {anomaly.anomaly_type}
      </h2>
      <p className="subtitle">
        <span className={`pill ${stateTone(anomaly.state)}`}>{anomaly.state}</span>{" "}
        <span className={`pill ${sevTone(anomaly.severity)}`}>{anomaly.severity}</span>{" "}
        {anomaly.suppressed && <span className="pill warn">suppressed</span>}{" "}
        <a href={`/behavior/sources/${anomaly.log_source_id}`}>view source behavior</a>
      </p>
      <p className="subtitle">{stateMeaning(anomaly.state)}</p>

      {anomaly.explanation && <p>{anomaly.explanation}</p>}

      {/* --- header facts ------------------------------------------------ */}
      <div className="grid">
        <div className="card">
          <div className="k">Observed</div>
          <div className="v">{formatMetric(anomaly.observed_value)}</div>
        </div>
        <div className="card">
          <div className="k">Expected</div>
          <div className="v">{formatMetric(anomaly.expected_value)}</div>
        </div>
        <div className="card">
          <div className="k">Deviation</div>
          <div className="v">{formatRatio(anomaly.deviation_ratio)}</div>
        </div>
        <div className="card">
          <div className="k">Absolute delta</div>
          <div className="v">{formatMetric(anomaly.absolute_delta, 0)}</div>
        </div>
        <div className="card">
          <div className="k">Robust z</div>
          <div className="v">{formatMetric(anomaly.robust_z)}</div>
        </div>
        <div className="card">
          <div className="k">Confidence</div>
          <div className="v">{formatMetric(anomaly.confidence)}</div>
        </div>
        <div className="card">
          <div className="k">Duration</div>
          <div className="v">{formatDuration(anomaly.duration_seconds)}</div>
        </div>
        <div className="card">
          <div className="k">Evidence</div>
          <div className="v">{anomaly.evidence_status}</div>
        </div>
      </div>

      <table style={{ maxWidth: 760, marginTop: 20 }}>
        <tbody>
          <tr>
            <td className="muted">Anomaly start</td>
            <td>{formatDateTime(anomaly.anomaly_start)}</td>
            <td className="muted">Anomaly end</td>
            {/* Still running: no end exists, and inventing one would report a
                closed incident that is still open. */}
            <td>
              {anomaly.anomaly_end ? (
                formatDateTime(anomaly.anomaly_end)
              ) : (
                <span className="muted">still running</span>
              )}
            </td>
          </tr>
          <tr>
            <td className="muted">Detected</td>
            <td>{formatDateTime(anomaly.detected_at)}</td>
            <td className="muted">Opened</td>
            <td>{formatDateTime(anomaly.opened_at)}</td>
          </tr>
          <tr>
            <td className="muted">Resolved</td>
            <td>{formatDateTime(anomaly.resolved_at)}</td>
            <td className="muted">Consecutive abnormal buckets</td>
            <td>{anomaly.consecutive_buckets}</td>
          </tr>
          <tr>
            <td className="muted">Baseline version</td>
            <td>
              {anomaly.baseline_version != null ? `v${anomaly.baseline_version}` : "—"}
            </td>
            <td className="muted">Detection policy version</td>
            <td>v{anomaly.policy_version}</td>
          </tr>
        </tbody>
      </table>

      {/* --- 1. detection summary ---------------------------------------- */}
      <DetectionSummary anomaly={anomaly} />

      {/* --- 2. timeline -------------------------------------------------- */}
      <section>
        <h3>Timeline</h3>
        {bucketsFailed ? (
          <div className="notice" role="alert">
            Metric history could not be loaded. The rest of this investigation is
            unaffected, but the timeline below is absent rather than empty.
          </div>
        ) : (
          <VolumeChart
            buckets={buckets}
            expected={detection?.expected_eps ?? anomaly.expected_value}
            expectedLow={detection?.expected_low ?? null}
            expectedHigh={detection?.expected_high ?? null}
            anomalies={
              anomaly.anomaly_start
                ? [{ start: anomaly.anomaly_start, end: anomaly.anomaly_end }]
                : []
            }
            transitions={anomaly.transitions.map((t) => ({
              at: t.occurred_at,
              label: t.to_state,
            }))}
            ariaLabel="Observed volume, expected baseline and the anomalous interval"
          />
        )}
      </section>

      {/* --- 3. lifecycle ------------------------------------------------- */}
      <LifecycleHistory
        transitions={anomaly.transitions}
        policyVersion={anomaly.policy_version}
      />

      {/* --- 4. evidence completeness ------------------------------------ */}
      <EvidenceStatusPanel status={anomaly.evidence_status} packaged={pkg} />

      {/* --- 5 & 6. contributors and dimension coverage ------------------- */}
      {pkg && pkg.dimensions.length > 0 ? (
        <>
          <DimensionSummary dimensions={pkg.dimensions} />
          <section>
            <h3>Contributor dimensions</h3>
            <p className="subtitle">
              One section per dimension the policy requested. Dimensions that
              could not be collected are shown here too — a hidden one would read
              as having been checked and found clean.
            </p>
            {pkg.dimensions.map((d) => (
              <ContributorTable key={d.dimension} dimension={d} />
            ))}
          </section>
        </>
      ) : (
        <section>
          <h3>Contributor dimensions</h3>
          <div className="notice">
            No dimension analysis is stored for this anomaly. No field has been
            examined, so nothing here has been ruled out.
          </div>
        </section>
      )}

      {/* --- 7. provenance ------------------------------------------------ */}
      {pkg && <QueryProvenance packaged={pkg} />}
    </>
  );
}
