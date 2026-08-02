// Anomaly list: every detected deviation, filterable and server-paginated.
//
// Deliberately distinguishes three outcomes that a naive list collapses into
// one blank table: no anomalies exist, no anomalies match these filters, and
// the request failed. Only the first is good news.

import type { Metadata } from "next";

import { AnomalyFilters } from "@/components/behavior/AnomalyFilters";
import { Pagination } from "@/components/Pagination";
import {
  ApiError,
  api,
  sevTone,
  type AnomalySummary,
  type Page,
  type SourceBehavior,
} from "@/lib/api";
import {
  PAGE_SIZE,
  paginationParams,
  parseAnomalyQuery,
} from "@/lib/anomalyQuery";
import {
  evidenceTone,
  formatDuration,
  formatMetric,
  formatRatio,
  stateTone,
} from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

export const metadata: Metadata = {
  title: "Anomalies",
};

export default async function AnomaliesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const q = parseAnomalyQuery(await searchParams);

  let page: Page<AnomalySummary> | null = null;
  let sources: SourceBehavior[] = [];
  let error: string | null = null;

  try {
    // The source list only populates the filter dropdown, so its failure must
    // not blank the table; it is fetched alongside but tolerated separately.
    const [listed, behaviors] = await Promise.all([
      api.anomalies({
        limit: PAGE_SIZE,
        offset: q.offset,
        log_source_id: q.log_source_id || undefined,
        instance_id: q.instance_id || undefined,
        anomaly_type: q.anomaly_type || undefined,
        state: q.state || undefined,
        severity: q.severity || undefined,
        evidence_status: q.evidence_status || undefined,
        since: q.since || undefined,
        until: q.until || undefined,
      }),
      api.sourceBehaviors().catch(() => [] as SourceBehavior[]),
    ]);
    page = listed;
    sources = behaviors;
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? "You do not have permission to view anomalies."
        : err instanceof ApiError && err.status === 401
          ? "Your session has expired. Sign in again to continue."
          : "Anomalies could not be loaded. The backend may be unreachable.";
  }

  const filtered = Boolean(
    q.log_source_id ||
      q.instance_id ||
      q.anomaly_type ||
      q.state ||
      q.severity ||
      q.evidence_status ||
      q.since ||
      q.until,
  );
  const items = page?.items ?? [];

  return (
    <>
      <h2>Anomalies</h2>
      <p className="subtitle">
        Every detected behavioral deviation, with the observed and expected
        values that produced the verdict. Open one to see what changed during the
        anomalous interval.
      </p>

      <AnomalyFilters
        values={{
          log_source_id: q.log_source_id,
          instance_id: q.instance_id,
          anomaly_type: q.anomaly_type,
          state: q.state,
          severity: q.severity,
          evidence_status: q.evidence_status,
          since: q.since,
          until: q.until,
        }}
        sources={sources}
      />

      {q.rangeNote && <div className="notice">{q.rangeNote}</div>}

      {error ? (
        <div className="notice" role="alert">
          {error}
        </div>
      ) : items.length === 0 ? (
        <div className="notice">
          {filtered
            ? "No anomalies match these filters. This is a filtered view, not a statement about the fleet."
            : "No anomalies have been detected. Sources without an adequate baseline are not being judged — check the behavioral overview for how many."}
        </div>
      ) : (
        <>
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
                <th>Started</th>
                <th>Duration</th>
                <th>Evidence</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
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
                  <td>{formatDateTime(a.anomaly_start ?? a.detected_at)}</td>
                  {/* Null while still running. Not "0s", and not "now minus
                      start", which would grow on every refresh. */}
                  <td>
                    {a.duration_seconds != null ? (
                      formatDuration(a.duration_seconds)
                    ) : (
                      <span className="muted">running</span>
                    )}
                  </td>
                  <td>
                    <span className={`pill ${evidenceTone(a.evidence_status)}`}>
                      {a.evidence_status}
                    </span>
                  </td>
                  <td>
                    <a href={`/anomalies/${a.id}`}>Investigate</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <Pagination
            total={page?.total ?? 0}
            limit={PAGE_SIZE}
            offset={q.offset}
            basePath="/anomalies"
            params={paginationParams(q)}
          />
        </>
      )}
    </>
  );
}
