// The detector's working: what was expected, what was observed, and which
// thresholds the verdict had to clear.
//
// This section exists so the verdict is checkable rather than asserted. It also
// carries the confidence limitation: when MAD was zero the robust z-score is an
// artefact and the verdict rests on a weaker deterministic test, which the
// operator must be told at the point where the confidence number is displayed.

import type { AnomalyDetail } from "@/lib/api";
import {
  formatCount,
  formatMetric,
  formatRatio,
  hasConfidenceLimitation,
  robustScoreMeaning,
} from "@/lib/behavior";

export function DetectionSummary({ anomaly }: { anomaly: AnomalyDetail }) {
  const d = anomaly.detection;

  if (!d) {
    return (
      <section>
        <h3>Detection summary</h3>
        <div className="notice">
          This anomaly recorded no structured detector evidence, so the
          thresholds behind its verdict cannot be shown.
        </div>
      </section>
    );
  }

  const degenerate = hasConfidenceLimitation(d.robust_score_status);

  return (
    <section>
      <h3>Detection summary</h3>
      {d.reason && <p>{d.reason}</p>}

      <table style={{ maxWidth: 720 }}>
        <tbody>
          <tr>
            <td className="muted">Expected EPS</td>
            <td>{formatMetric(d.expected_eps ?? anomaly.expected_value)}</td>
            <td className="muted">Observed EPS</td>
            <td>{formatMetric(d.observed_eps ?? anomaly.observed_value)}</td>
          </tr>
          <tr>
            <td className="muted">Expected range</td>
            <td>
              {formatMetric(d.expected_low)} – {formatMetric(d.expected_high)}
            </td>
            <td className="muted">Deviation ratio</td>
            <td>
              {formatRatio(d.ratio ?? anomaly.deviation_ratio)}
              {/* A spike out of a zero baseline has no ratio at all. Without
                  this note the em dash reads as "unchanged". */}
              {d.ratio_basis === "expected_zero" && (
                <span className="muted"> (baseline expected no traffic)</span>
              )}
            </td>
          </tr>
          <tr>
            <td className="muted">Expected events / bucket</td>
            <td>{formatMetric(d.expected_events, 0)}</td>
            <td className="muted">Observed events / bucket</td>
            <td>{formatMetric(d.observed_events, 0)}</td>
          </tr>
          <tr>
            <td className="muted">Absolute delta</td>
            <td>{formatMetric(d.absolute_delta_events ?? anomaly.absolute_delta, 0)}</td>
            <td className="muted">Bucket width</td>
            <td>{d.bucket_seconds != null ? `${d.bucket_seconds}s` : "—"}</td>
          </tr>
          <tr>
            <td className="muted">Robust z threshold</td>
            <td>±{formatMetric(d.threshold)}</td>
            <td className="muted">Robust z observed</td>
            <td>{formatMetric(d.robust_z ?? anomaly.robust_z)}</td>
          </tr>
          <tr>
            <td className="muted">Consecutive abnormal buckets</td>
            <td>{formatCount(anomaly.consecutive_buckets)}</td>
            <td className="muted">Baseline samples</td>
            <td>{formatCount(d.baseline_sample_count)}</td>
          </tr>
          <tr>
            <td className="muted">Baseline completeness</td>
            <td>
              {d.baseline_completeness != null
                ? `${(d.baseline_completeness * 100).toFixed(0)}%`
                : "—"}
            </td>
            <td className="muted">Confidence</td>
            <td>
              {formatMetric(anomaly.confidence)}
              {degenerate && <span className="pill warn">capped</span>}
            </td>
          </tr>
        </tbody>
      </table>

      <p>
        <span className="muted">MAD status: </span>
        <span className={`pill ${degenerate ? "warn" : "ok"}`}>
          {d.robust_score_status ?? "NOT RECORDED"}
        </span>
      </p>
      {d.robust_score_status && (
        <p className="subtitle">{robustScoreMeaning(d.robust_score_status)}</p>
      )}
      {degenerate && d.fallback_bound != null && (
        <p className="subtitle">
          Deterministic fallback bound applied: {formatMetric(d.fallback_bound)} EPS.
        </p>
      )}
    </section>
  );
}
