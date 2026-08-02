// Evidence completeness for one anomaly's explanation package.
//
// Every one of the six states is rendered with its meaning spelled out, because
// four of them (NOT_REQUESTED, PENDING, UNAVAILABLE, FAILED) produce an
// investigation page with no contributors — visually identical to "we looked
// and nothing stood out". Only the stated status distinguishes them.

import type { EvidenceStatus, ExplanationPackage } from "@/lib/api";
import { evidenceMeaning, evidenceTone } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

export function EvidenceStatusPanel({
  status,
  packaged,
}: {
  status: EvidenceStatus;
  packaged: ExplanationPackage | null;
}) {
  return (
    <section>
      <h3>Evidence completeness</h3>
      <p>
        <span className={`pill ${evidenceTone(status)}`}>{status}</span>
      </p>
      <p className="subtitle">{evidenceMeaning(status)}</p>

      {packaged?.error && (
        // The backend sanitizes this string: never a provider response body,
        // never headers. React escapes it again on the way into the DOM.
        <div className="notice" role="alert">
          <strong>Collection error:</strong> {packaged.error}
        </div>
      )}

      {packaged ? (
        <table style={{ maxWidth: 560 }}>
          <tbody>
            <tr>
              <td className="muted">Requested</td>
              <td>{formatDateTime(packaged.requested_at)}</td>
            </tr>
            <tr>
              <td className="muted">Completed</td>
              <td>{formatDateTime(packaged.completed_at)}</td>
            </tr>
            <tr>
              <td className="muted">Anomaly-window events</td>
              <td>{packaged.anomaly_total_events.toLocaleString()}</td>
            </tr>
            <tr>
              <td className="muted">Baseline-window events</td>
              <td>{packaged.baseline_total_events.toLocaleString()}</td>
            </tr>
            <tr>
              <td className="muted">Evidence schema</td>
              <td>v{packaged.schema_version}</td>
            </tr>
          </tbody>
        </table>
      ) : (
        <div className="notice">
          No evidence package is stored for this anomaly. Nothing about the
          anomalous interval has been queried from QRadar.
        </div>
      )}
    </section>
  );
}
