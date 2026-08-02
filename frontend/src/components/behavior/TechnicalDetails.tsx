// Everything that makes the verdict checkable rather than asserted.
//
// All of it was previously above the fold: eight KPI cards including robust z,
// a versions table, a fourteen-row threshold table, and a twenty-row
// provenance table with full AQL expanded inline. None of it is deleted — it
// is grouped into five disclosures, all collapsed, so an analyst auditing the
// verdict finds it in one place and an analyst triaging never has to scroll
// past it.
//
// The provenance section renders query *structure* only. The backend never
// writes a SEC token, a request header, a credential or a raw event payload
// into this document, and this component reads only the named fields below, so
// a future addition to the provenance blob cannot leak through it.

import { CodePanel } from "@/components/ui/CodePanel";
import { Disclosure } from "@/components/ui/Disclosure";
import type { AnomalyDetail, ExplanationPackage } from "@/lib/api";
import {
  dimensionLabel,
  formatCount,
  formatMetric,
  formatRatio,
  hasConfidenceLimitation,
  robustScoreMeaning,
} from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className="num">{value}</dd>
    </div>
  );
}

export function TechnicalDetails({
  anomaly,
  packaged,
}: {
  anomaly: AnomalyDetail;
  packaged: ExplanationPackage | null;
}) {
  const d = anomaly.detection;
  const degenerate = hasConfidenceLimitation(d?.robust_score_status);
  const provenance = packaged?.query_provenance ?? {};
  const queries = Array.isArray(provenance.queries) ? provenance.queries : [];

  return (
    <div className="stack-6">
      {/* 1 — detection thresholds ---------------------------------------- */}
      <Disclosure summary="Detection thresholds">
        {d ? (
          <dl className="dimension-meta">
            <Row label="Expected EPS" value={formatMetric(d.expected_eps ?? anomaly.expected_value)} />
            <Row label="Observed EPS" value={formatMetric(d.observed_eps ?? anomaly.observed_value)} />
            <Row
              label="Expected range"
              value={`${formatMetric(d.expected_low)} – ${formatMetric(d.expected_high)}`}
            />
            <Row
              label="Deviation ratio"
              value={
                <>
                  {formatRatio(d.ratio ?? anomaly.deviation_ratio)}
                  {/* A spike out of a zero baseline has no ratio at all.
                      Without this note the em dash reads as "unchanged". */}
                  {d.ratio_basis === "expected_zero" && (
                    <span className="muted"> (baseline expected no traffic)</span>
                  )}
                </>
              }
            />
            <Row label="Expected events / bucket" value={formatMetric(d.expected_events, 0)} />
            <Row label="Observed events / bucket" value={formatMetric(d.observed_events, 0)} />
            <Row
              label="Absolute delta"
              value={formatMetric(d.absolute_delta_events ?? anomaly.absolute_delta, 0)}
            />
            <Row
              label="Bucket width"
              value={d.bucket_seconds != null ? `${d.bucket_seconds}s` : "—"}
            />
            <Row label="Robust z threshold" value={`±${formatMetric(d.threshold)}`} />
            <Row
              label="Consecutive abnormal buckets"
              value={formatCount(anomaly.consecutive_buckets)}
            />
          </dl>
        ) : (
          <div className="notice">
            This anomaly recorded no structured detector evidence, so the
            thresholds behind its verdict cannot be shown.
          </div>
        )}
      </Disclosure>

      {/* 2 — baseline and score ------------------------------------------ */}
      <Disclosure
        summary="Baseline and score details"
        note={d?.robust_score_status ?? undefined}
      >
        {d ? (
          <>
            <dl className="dimension-meta">
              <Row label="Robust z observed" value={formatMetric(d.robust_z ?? anomaly.robust_z)} />
              <Row label="MAD status" value={d.robust_score_status ?? "NOT RECORDED"} />
              <Row label="Confidence" value={formatMetric(anomaly.confidence)} />
              <Row label="Baseline samples" value={formatCount(d.baseline_sample_count)} />
              <Row
                label="Baseline completeness"
                value={
                  d.baseline_completeness != null
                    ? `${(d.baseline_completeness * 100).toFixed(0)}%`
                    : "—"
                }
              />
              <Row
                label="Baseline version"
                value={anomaly.baseline_version != null ? `v${anomaly.baseline_version}` : "—"}
              />
            </dl>
            {d.robust_score_status && (
              <p className="subtitle">{robustScoreMeaning(d.robust_score_status)}</p>
            )}
            {degenerate && d.fallback_bound != null && (
              <p className="subtitle">
                Deterministic fallback bound applied:{" "}
                {formatMetric(d.fallback_bound)} EPS.
              </p>
            )}
          </>
        ) : (
          <div className="notice">No detector evidence was recorded.</div>
        )}
      </Disclosure>

      {/* 3 — evidence provenance ------------------------------------------ */}
      <Disclosure summary="Evidence provenance">
        {packaged ? (
          <dl className="dimension-meta">
            <Row label="Comparison strategy" value={packaged.comparison_strategy} />
            <Row
              label="Anomaly window"
              value={`${formatDateTime(packaged.anomaly_window_start)} → ${formatDateTime(packaged.anomaly_window_end)}`}
            />
            <Row
              label="Baseline window"
              value={`${formatDateTime(packaged.baseline_window_start)} → ${formatDateTime(packaged.baseline_window_end)}`}
            />
            <Row
              label="Anomaly-window events"
              value={packaged.anomaly_total_events.toLocaleString()}
            />
            <Row
              label="Baseline-window events"
              value={packaged.baseline_total_events.toLocaleString()}
            />
            <Row label="Requested" value={formatDateTime(packaged.requested_at)} />
            <Row
              label="Completed"
              value={
                <>
                  {formatDateTime(packaged.completed_at)}
                  {packaged.collection_duration_ms != null &&
                    ` (${packaged.collection_duration_ms} ms)`}
                </>
              }
            />
          </dl>
        ) : (
          <div className="notice">
            No evidence package is stored for this anomaly. Nothing about the
            anomalous interval has been queried from QRadar.
          </div>
        )}
      </Disclosure>

      {/* 4 — the queries themselves --------------------------------------- */}
      <Disclosure
        summary="Ariel queries"
        note={queries.length > 0 ? `${queries.length}` : undefined}
      >
        <p className="subtitle">
          How the evidence was obtained. Query structure only — no credentials,
          headers or raw event payloads are recorded or shown. Each statement
          carries the START and STOP bounds that scoped it.
        </p>
        {queries.length === 0 ? (
          <div className="notice">No queries were recorded for this package.</div>
        ) : (
          <div className="stack-6">
            {queries.map((q, i) => (
              <div key={`${q.dimension}-${q.window}-${i}`}>
                <CodePanel
                  label={`${q.dimension ? dimensionLabel(q.dimension) : "Query"} · ${q.window ?? "window"}`}
                  meta={[
                    q.rows != null ? `${q.rows} rows` : null,
                    q.truncated ? "truncated" : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  code={q.aql ?? "No statement recorded."}
                />
                {q.error && <p className="subtitle">{q.error}</p>}
              </div>
            ))}
          </div>
        )}
      </Disclosure>

      {/* 5 — versions and collection metadata ------------------------------ */}
      <Disclosure summary="Versions and collection metadata">
        <dl className="dimension-meta">
          <Row label="Detection policy version" value={`v${anomaly.policy_version}`} />
          <Row
            label="Baseline version"
            value={anomaly.baseline_version != null ? `v${anomaly.baseline_version}` : "—"}
          />
          <Row
            label="Evidence schema version"
            value={packaged ? `v${packaged.schema_version}` : "—"}
          />
          <Row label="Collection source" value="QRadar Ariel, via the backend collector" />
          <Row label="Detected" value={formatDateTime(anomaly.detected_at)} />
          <Row label="Opened" value={formatDateTime(anomaly.opened_at)} />
          <Row label="Resolved" value={formatDateTime(anomaly.resolved_at)} />
        </dl>
      </Disclosure>
    </div>
  );
}
