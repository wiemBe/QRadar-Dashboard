// Sanitized provenance for the evidence package.
//
// This renders query *structure* — the AQL, the windows, the row counts — which
// is what makes a contributor claim auditable rather than something the analyst
// must take on faith. It is not a debug dump: the backend never writes a SEC
// token, a request header, a credential or a raw event payload into this
// document, and this component reads only the named fields below, so a future
// addition to the provenance blob cannot leak through it.

import type { ExplanationPackage } from "@/lib/api";
import { dimensionLabel } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

export function QueryProvenance({ packaged }: { packaged: ExplanationPackage }) {
  const p = packaged.query_provenance ?? {};
  const queries = Array.isArray(p.queries) ? p.queries : [];

  return (
    <section>
      <h3>Query provenance</h3>
      <p className="subtitle">
        How the evidence above was obtained. Query structure only — no
        credentials, headers or raw event payloads are recorded or shown.
      </p>

      <table style={{ maxWidth: 720 }}>
        <tbody>
          <tr>
            <td className="muted">Comparison strategy</td>
            <td>{packaged.comparison_strategy}</td>
          </tr>
          <tr>
            <td className="muted">Anomaly window</td>
            <td>
              {formatDateTime(packaged.anomaly_window_start)} →{" "}
              {formatDateTime(packaged.anomaly_window_end)}
            </td>
          </tr>
          <tr>
            <td className="muted">Baseline window</td>
            <td>
              {formatDateTime(packaged.baseline_window_start)} →{" "}
              {formatDateTime(packaged.baseline_window_end)}
            </td>
          </tr>
          <tr>
            <td className="muted">Query completion</td>
            <td>
              {formatDateTime(packaged.completed_at)}
              {packaged.collection_duration_ms != null &&
                ` (${packaged.collection_duration_ms} ms)`}
            </td>
          </tr>
          <tr>
            <td className="muted">Evidence schema version</td>
            <td>v{packaged.schema_version}</td>
          </tr>
          <tr>
            <td className="muted">Collection source</td>
            <td>QRadar Ariel, via the backend collector</td>
          </tr>
        </tbody>
      </table>

      {queries.length === 0 ? (
        <div className="notice">No queries were recorded for this package.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Dimension</th>
              <th>Window</th>
              <th>Rows</th>
              <th>Truncated</th>
              <th>Query</th>
            </tr>
          </thead>
          <tbody>
            {queries.map((q, i) => (
              <tr key={`${q.dimension}-${q.window}-${i}`}>
                <td>{q.dimension ? dimensionLabel(q.dimension) : "—"}</td>
                <td>{q.window ?? "—"}</td>
                <td>{q.rows ?? "—"}</td>
                <td>{q.truncated ? "yes" : "no"}</td>
                <td>
                  {/* AQL as text. React escapes it; it is never interpreted. */}
                  <code>{q.aql ?? "—"}</code>
                  {q.error && (
                    <p className="subtitle" style={{ margin: "4px 0 0" }}>
                      {q.error}
                    </p>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
