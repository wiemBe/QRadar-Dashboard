// Log-source behavior detail: observed volume against its seasonal baseline.
//
// The chart here is the page's substance, and its correctness condition is
// negative: an interval that was not collected must not be drawn as traffic,
// as zero, or as a line connecting the observations either side of it. All
// three would turn a collector outage into an apparent source outage.

import type { Metadata } from "next";

import { VolumeChart } from "@/components/behavior/VolumeChart";
import {
  ApiError,
  api,
  sevTone,
  type AnomalySummary,
  type LogSourceDetail,
  type MetricBucket,
  type Page,
  type SourceBehavior,
} from "@/lib/api";
import {
  formatMetric,
  formatRatio,
  isUnjudged,
  stateMeaning,
  stateTone,
} from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

export const metadata: Metadata = {
  title: "Source behavior",
};

/** Window of history the chart covers. */
const WINDOW_HOURS = 24;

export default async function SourceBehaviorPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let behavior: SourceBehavior | null = null;
  let status = 0;
  try {
    behavior = await api.sourceBehavior(id);
  } catch (err) {
    status = err instanceof ApiError ? err.status : 0;
  }

  if (!behavior) {
    return (
      <>
        <h2>Source behavior</h2>
        <div className="notice" role="alert">
          {status === 404
            ? "This log source does not exist."
            : status === 403
              ? "You do not have permission to view behavioral analytics."
              : status === 401
                ? "Your session has expired. Sign in again to continue."
                : "Could not load this source. The backend may be unreachable."}
        </div>
      </>
    );
  }

  const until = new Date().toISOString();
  const since = new Date(Date.now() - WINDOW_HOURS * 3600_000).toISOString();

  // Each of these enriches the page; none of them is the page. A failure in
  // one is reported in place rather than replacing everything with an error.
  const [metadataResult, bucketsResult, anomaliesResult] = await Promise.allSettled([
    api.logSource(id),
    api.sourceMetrics(id, { since, until, limit: 2000 }),
    api.anomalies({ log_source_id: id, limit: 20, since, until }),
  ]);

  const meta: LogSourceDetail | null =
    metadataResult.status === "fulfilled" ? metadataResult.value : null;
  const buckets: MetricBucket[] =
    bucketsResult.status === "fulfilled" ? bucketsResult.value : [];
  const anomalyPage: Page<AnomalySummary> | null =
    anomaliesResult.status === "fulfilled" ? anomaliesResult.value : null;
  const anomalies = anomalyPage?.items ?? [];

  const active = anomalies.filter((a) =>
    ["CANDIDATE", "OPEN", "RECOVERING"].includes(a.state),
  );
  const incomplete = buckets.filter((b) => b.completeness !== "COMPLETE");

  return (
    <>
      <h2>{behavior.name}</h2>
      <p className="subtitle">
        <span className={`pill ${stateTone(behavior.state)}`}>{behavior.state}</span>{" "}
        criticality {behavior.criticality}
        {meta?.type_name ? ` · ${meta.type_name}` : ""}
        {meta?.owner ? ` · owner ${meta.owner}` : ""}{" "}
        <a href={`/log-sources/${behavior.log_source_id}`}>inventory detail</a>
      </p>
      <p className="subtitle">{stateMeaning(behavior.state)}</p>

      <div className="grid">
        <div className="card">
          <div className="k">Observed EPS</div>
          <div className="v">{formatMetric(behavior.observed_eps)}</div>
        </div>
        <div className="card">
          <div className="k">Expected EPS</div>
          <div className="v">
            {/* No reliable baseline means no expectation exists. A 0 here would
                be an invented expectation the observed value is judged against. */}
            {isUnjudged(behavior.state) ? "—" : formatMetric(behavior.expected_eps)}
          </div>
        </div>
        <div className="card">
          <div className="k">Deviation</div>
          <div className="v">{formatRatio(behavior.deviation_ratio)}</div>
        </div>
        <div className="card">
          <div className="k">Baseline samples</div>
          <div className="v">{behavior.baseline_sample_count}</div>
        </div>
        <div className="card">
          <div className="k">Baseline completeness</div>
          <div className="v">{(behavior.baseline_completeness * 100).toFixed(0)}%</div>
        </div>
        <div className="card">
          <div className="k">Active anomalies</div>
          <div className={active.length > 0 ? "v crit" : "v"}>{active.length}</div>
        </div>
      </div>

      <table style={{ maxWidth: 760, marginTop: 20 }}>
        <tbody>
          <tr>
            <td className="muted">Expected band</td>
            <td>
              {formatMetric(behavior.expected_low)} – {formatMetric(behavior.expected_high)}
            </td>
            <td className="muted">Last event</td>
            <td>{formatDateTime(behavior.last_event_at)}</td>
          </tr>
          <tr>
            <td className="muted">Last collected bucket</td>
            <td>{formatDateTime(behavior.last_bucket_at)}</td>
            <td className="muted">Collection completeness</td>
            <td>
              {buckets.length === 0 ? (
                <span className="muted">no buckets collected</span>
              ) : (
                `${buckets.length - incomplete.length} of ${buckets.length} intervals fully observed`
              )}
            </td>
          </tr>
        </tbody>
      </table>

      {/* --- volume chart -------------------------------------------------- */}
      <section>
        <h3>Observed volume, last {WINDOW_HOURS} hours</h3>
        {bucketsResult.status === "rejected" ? (
          <div className="notice" role="alert">
            Metric history could not be loaded. This is a failed request, not an
            absence of traffic.
          </div>
        ) : (
          <VolumeChart
            buckets={buckets}
            expected={isUnjudged(behavior.state) ? null : behavior.expected_eps}
            expectedLow={behavior.expected_low}
            expectedHigh={behavior.expected_high}
            anomalies={anomalies
              .filter((a) => a.anomaly_start)
              .map((a) => ({
                start: a.anomaly_start as string,
                end: a.anomaly_end,
                label: a.anomaly_type,
              }))}
            ariaLabel={`Observed EPS for ${behavior.name} against its expected baseline`}
          />
        )}
        {incomplete.length > 0 && (
          <div className="notice">
            {incomplete.length} interval{incomplete.length === 1 ? " was" : "s were"} not
            fully collected in this window and {incomplete.length === 1 ? "is" : "are"} shown
            as a gap rather than as a value. Their volume is an undercount, not a
            measurement, and they never enter the baseline.
          </div>
        )}
      </section>

      {/* --- anomalies ---------------------------------------------------- */}
      <section>
        <h3>Anomalies in this window</h3>
        {anomaliesResult.status === "rejected" ? (
          <div className="notice" role="alert">
            Anomaly history could not be loaded for this source.
          </div>
        ) : anomalies.length === 0 ? (
          <div className="notice">
            {isUnjudged(behavior.state)
              ? "No anomalies — but this source has no adequate baseline, so no detector has judged it. This is not a clean result."
              : `No anomalies detected in the last ${WINDOW_HOURS} hours.`}
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Detector</th>
                <th>State</th>
                <th>Severity</th>
                <th>Observed</th>
                <th>Expected</th>
                <th>Deviation</th>
                <th>Started</th>
                <th>Evidence</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {anomalies.map((a) => (
                <tr key={a.id}>
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
                  <td>{formatDateTime(a.anomaly_start ?? a.detected_at)}</td>
                  <td>{a.evidence_status}</td>
                  <td>
                    <a href={`/anomalies/${a.id}`}>Investigate</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
