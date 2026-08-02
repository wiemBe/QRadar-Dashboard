// Anomaly investigation — the product's flagship page.
//
// It answers one question: what changed, when, and what evidence supports it?
// Everything on it is server-rendered from the backend's own verdict. The UI
// never recomputes detection logic and never fills a missing measurement with
// a plausible number.
//
// The information order is deliberate and is the whole redesign. Previously
// the first viewport held eight equally weighted metric cards — including the
// robust z-score — then a versions table, then a fourteen-row threshold table,
// and the timeline appeared below all of it. Ten contributor tables and a
// twenty-row provenance dump with expanded AQL followed: 26 tables and 125
// column headers in one scroll.
//
// Now: who and what, the deterministic summary, four metrics, the timeline,
// the three strongest contributors, and everything else behind three tabs.

import type { Metadata } from "next";

import { EvidenceBanner } from "@/components/behavior/EvidenceBanner";
import { DimensionExplorer } from "@/components/behavior/DimensionExplorer";
import { IncidentHeader } from "@/components/behavior/IncidentHeader";
import { LifecycleTimeline } from "@/components/behavior/LifecycleTimeline";
import { TechnicalDetails } from "@/components/behavior/TechnicalDetails";
import { TopContributors } from "@/components/behavior/TopContributors";
import { VolumeChart } from "@/components/behavior/VolumeChart";
import { AnomalyTabs } from "@/components/behavior/AnomalyTabs";
import { ApiError, api, type AnomalyDetail, type MetricBucket } from "@/lib/api";
import {
  dimensionLabel,
  formatDuration,
  formatMetric,
  formatRatio,
  hasConfidenceLimitation,
  stateMeaning,
} from "@/lib/behavior";
import { summarizeAnomaly, summarizeTimeline } from "@/lib/summarize";
import { buildSeries } from "@/lib/timeseries";

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
        <h1 className="page-title">Anomaly investigation</h1>
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
  // summary, the metrics, the evidence and the lifecycle are the substance.
  let buckets: MetricBucket[] = [];
  let bucketsFailed = false;
  try {
    buckets = await api.sourceMetrics(anomaly.log_source_id, { since, until, limit: 2000 });
  } catch {
    bucketsFailed = true;
  }

  const pkg = anomaly.explanation_package;
  const detection = anomaly.detection;
  const summary = summarizeAnomaly(anomaly);
  const degenerate = hasConfidenceLimitation(detection?.robust_score_status);

  const points = buildSeries(buckets);
  const incomplete = buckets.filter((b) => b.completeness !== "COMPLETE").length;
  const chartSummary = summarizeTimeline({
    observed: points.map((p) => p.observed),
    expected: detection?.expected_eps ?? anomaly.expected_value,
    anomalyStart: anomaly.anomaly_start,
    anomalyEnd: anomaly.anomaly_end,
    state: anomaly.state,
    incompleteBuckets: incomplete,
    totalBuckets: buckets.length,
  });

  const truncated = (pkg?.dimensions ?? [])
    .filter((d) => d.availability === "TRUNCATED")
    .map((d) => dimensionLabel(d.dimension));

  return (
    <>
      {/* --- A. compact incident header ---------------------------------- */}
      <IncidentHeader anomaly={anomaly} />

      {/* --- B. deterministic summary ------------------------------------ */}
      <p className="incident-summary">{summary.text}</p>
      {summary.caveat && <p className="subtitle">{summary.caveat}</p>}
      <p className="subtitle">{stateMeaning(anomaly.state)}</p>

      {/* --- C. four primary metrics ------------------------------------- */}
      <div className="grid-4">
        <div className="card">
          <div className="k">Observed</div>
          <div className="v">{formatMetric(anomaly.observed_value)}</div>
          <p className="metric-note">EPS during the anomalous interval</p>
        </div>
        <div className="card">
          <div className="k">Expected</div>
          <div className="v">{formatMetric(anomaly.expected_value)}</div>
          <p className="metric-note">Seasonal baseline for this weekday and hour</p>
        </div>
        <div className="card">
          <div className="k">Deviation</div>
          <div className="v">{formatRatio(anomaly.deviation_ratio)}</div>
          <p className="metric-note">Observed against expected</p>
        </div>
        <div className="card">
          <div className="k">Duration</div>
          <div className="v">{formatDuration(anomaly.duration_seconds)}</div>
          <p className="metric-note">
            {anomaly.anomaly_end ? "Interval length" : "Still running"}
          </p>
        </div>
      </div>

      {/* Confidence is secondary, and carries its limitation at the point it
          is read rather than in a paragraph further down the page. */}
      <p className="confidence-line">
        <span className="muted">Confidence </span>
        <span className="num">{formatMetric(anomaly.confidence)}</span>
        {degenerate && (
          <span className="muted">
            {" · "}Confidence limited because baseline variability is zero. See
            technical details.
          </span>
        )}
      </p>

      {/* --- D. the timeline --------------------------------------------- */}
      <section>
        <h2 className="section-title">Timeline</h2>
        {bucketsFailed ? (
          <div className="notice" role="alert">
            Metric history could not be loaded. The rest of this investigation
            is unaffected, but the timeline is absent rather than empty.
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
            textSummary={chartSummary}
          />
        )}
      </section>

      {/* --- E. what changed --------------------------------------------- */}
      <section>
        <h2 className="section-title">What changed</h2>
        <p className="subtitle">
          The strongest contributors, one per dimension. These are the values
          that moved most during the interval — a measured share of the change,
          not a statement of cause.
        </p>
        <TopContributors
          packaged={pkg}
          anomalyType={anomaly.anomaly_type}
          evidenceStatus={anomaly.evidence_status}
        />
      </section>

      {/* --- F. evidence completeness, stated once ------------------------ */}
      <EvidenceBanner
        status={anomaly.evidence_status}
        error={pkg?.error ?? null}
        truncatedDimensions={truncated}
      />

      {/* --- G. everything else, behind tabs ------------------------------ */}
      <section id="evidence">
        <h2 className="section-title">Investigation detail</h2>
        <AnomalyTabs
          evidence={<DimensionExplorer dimensions={pkg?.dimensions ?? []} />}
          lifecycle={
            <LifecycleTimeline
              transitions={anomaly.transitions}
              policyVersion={anomaly.policy_version}
            />
          }
          technical={<TechnicalDetails anomaly={anomaly} packaged={pkg} />}
        />
      </section>
    </>
  );
}
