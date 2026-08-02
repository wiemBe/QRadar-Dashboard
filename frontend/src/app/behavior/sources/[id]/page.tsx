// Log-source behavior detail.
//
// The page answers six questions in order: is this source behaving normally
// now, what is its observed against expected volume, when did it last send an
// event, has it produced anomalies recently, is its baseline trustworthy, and
// is collection healthy. Technical detail comes last and starts collapsed.
//
// The chart's correctness condition is negative, and it is the reason this
// page exists in the shape it does: an interval that was not collected must
// not be drawn as traffic, as zero, or as a line connecting the observations
// either side of it. All three would turn a collector outage into an apparent
// source outage. A COMPLETE bucket reporting zero is the opposite case — a
// real measurement of silence — and must be drawn as zero, not as a gap.

import type { Metadata } from "next";

import { RangeSelector } from "@/components/behavior/RangeSelector";
import { SourceAdvanced } from "@/components/behavior/SourceAdvanced";
import { SourceAnomalies } from "@/components/behavior/SourceAnomalies";
import { SourceHeader } from "@/components/behavior/SourceHeader";
import { SourceQuality } from "@/components/behavior/SourceQuality";
import { VolumeChart } from "@/components/behavior/VolumeChart";
import {
  ApiError,
  api,
  type AnomalySummary,
  type BaselineCell,
  type LogSourceDetail,
  type MetricBucket,
  type Page,
  type SourceBehavior,
} from "@/lib/api";
import {
  formatMetric,
  formatRatio,
  hasConfidenceLimitation,
  isUnjudged,
  stateMeaning,
} from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";
import {
  METRIC_LIMIT,
  baselineQuality,
  collectionHealth,
  parseRange,
  partitionAnomalies,
  rangeLabel,
  summarizeSourceTimeline,
} from "@/lib/sourceDetail";
import { buildSeries } from "@/lib/timeseries";

export const metadata: Metadata = {
  title: "Source behavior",
};

export default async function SourceBehaviorPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { id } = await params;
  const range = parseRange((await searchParams).range);

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
        <h1 className="page-title">Source behavior</h1>
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
  const since = new Date(Date.now() - range * 3600_000).toISOString();

  // Each of these enriches the page; none of them is the page. A failure in
  // one is reported in place rather than replacing everything with an error.
  const [metaResult, bucketsResult, anomaliesResult, cellsResult] =
    await Promise.allSettled([
      api.logSource(id),
      api.sourceMetrics(id, { since, until, limit: METRIC_LIMIT }),
      api.anomalies({ log_source_id: id, limit: 20, since, until }),
      api.sourceBaselines(id),
    ]);

  const meta: LogSourceDetail | null =
    metaResult.status === "fulfilled" ? metaResult.value : null;
  const buckets: MetricBucket[] =
    bucketsResult.status === "fulfilled" ? bucketsResult.value : [];
  const anomalyPage: Page<AnomalySummary> | null =
    anomaliesResult.status === "fulfilled" ? anomaliesResult.value : null;
  const anomalies = anomalyPage?.items ?? [];
  const cells: BaselineCell[] =
    cellsResult.status === "fulfilled" ? cellsResult.value : [];

  const { active, recent } = partitionAnomalies(anomalies);
  const quality = baselineQuality(behavior, cells);
  const health = collectionHealth(buckets, behavior.last_bucket_at, range * 3600);
  const unjudged = isUnjudged(behavior.state) || behavior.expected_eps == null;

  const points = buildSeries(buckets);
  const missing = buckets.filter((b) => b.completeness === "MISSING").length;
  const partial = buckets.filter((b) => b.completeness === "PARTIAL").length;

  const chartSummary = summarizeSourceTimeline({
    hours: range,
    observed: points.map((p) => p.observed),
    currentEps: behavior.observed_eps,
    expectedEps: behavior.expected_eps,
    missing,
    partial,
    anomalyCount: anomalies.length,
  });

  // Secondary conditions worth stating next to the numbers they qualify.
  const notes: string[] = [];
  if (unjudged) notes.push("Baseline still learning");
  if (quality.status === "DEGENERATE") notes.push("Baseline variability is zero");
  if (health.status !== "CURRENT") notes.push(`Collection ${health.label.toLowerCase()}`);
  if (behavior.observed_eps === 0) notes.push("Source is currently silent");
  if (meta?.monitoring_enabled === false) notes.push("Monitoring disabled");
  if (meta?.maintenance_mode) notes.push("Maintenance mode active");

  return (
    <>
      {/* --- 1. compact header ------------------------------------------- */}
      <SourceHeader behavior={behavior} meta={meta} />
      <p className="subtitle">{stateMeaning(behavior.state)}</p>

      {/* --- 2. four primary metrics -------------------------------------- */}
      <div className="grid-4">
        <div className="card">
          <div className="k">Observed EPS</div>
          {/* A measured zero is 0.00; only an absent measurement is an em
              dash. formatMetric enforces the distinction. */}
          <div className="v">{formatMetric(behavior.observed_eps)}</div>
          <p className="metric-note">Most recent collected interval</p>
        </div>
        <div className="card">
          <div className="k">Expected EPS</div>
          <div className="v">
            {/* No reliable baseline means no expectation exists. A 0 here
                would be an invented expectation the observed value is then
                judged against. */}
            {unjudged ? (
              <span className="metric-learning">Still learning</span>
            ) : (
              formatMetric(behavior.expected_eps)
            )}
          </div>
          <p className="metric-note">Seasonal baseline for this weekday and hour</p>
        </div>
        <div className="card">
          <div className="k">Deviation</div>
          {/* Not computed when there is no expectation to deviate from. */}
          <div className="v">
            {unjudged ? <span className="muted">—</span> : formatRatio(behavior.deviation_ratio)}
          </div>
          <p className="metric-note">Observed against expected</p>
        </div>
        <div className="card">
          <div className="k">Last event</div>
          <div className="v metric-timestamp">
            {behavior.last_event_at ? (
              <time dateTime={behavior.last_event_at}>
                {formatDateTime(behavior.last_event_at)}
              </time>
            ) : (
              <span className="muted">—</span>
            )}
          </div>
          <p className="metric-note">
            {behavior.last_event_at ? "Most recent event observed" : "No event observed"}
          </p>
        </div>
      </div>

      {notes.length > 0 && (
        <p className="confidence-line">
          <span className="muted">{notes.join(" · ")}</span>
        </p>
      )}

      {/* --- 3. the chart -------------------------------------------------- */}
      <section>
        <div className="row section-head">
          <h2 className="section-title">Observed volume</h2>
          <RangeSelector sourceId={behavior.log_source_id} active={range} />
        </div>
        {bucketsResult.status === "rejected" ? (
          <div className="notice" role="alert">
            Metric history could not be loaded. This is a failed request, not an
            absence of traffic.
          </div>
        ) : (
          <VolumeChart
            buckets={buckets}
            expected={unjudged ? null : behavior.expected_eps}
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
            textSummary={chartSummary}
          />
        )}
      </section>

      {/* --- 4. anomalies, above the technical detail ---------------------- */}
      <section>
        <h2 className="section-title">Anomalies</h2>
        <SourceAnomalies
          active={active}
          recent={recent}
          failed={anomaliesResult.status === "rejected"}
          windowLabel={rangeLabel(range)}
          unjudged={unjudged}
        />
      </section>

      {/* --- 5 & 6. baseline quality and collection health ----------------- */}
      <section>
        <h2 className="section-title">Data quality</h2>
        <SourceQuality
          baseline={quality}
          collection={health}
          expectedLow={behavior.expected_low}
          expectedHigh={behavior.expected_high}
          lastBucketAt={behavior.last_bucket_at}
          qradarStatus={meta?.qradar_status ?? null}
        />
      </section>

      {/* --- 7. advanced, all collapsed ------------------------------------ */}
      <section>
        <h2 className="section-title">Technical detail</h2>
        <SourceAdvanced
          behavior={behavior}
          meta={meta}
          buckets={buckets}
          cells={cells}
          cellsFailed={cellsResult.status === "rejected"}
        />
      </section>
    </>
  );
}
