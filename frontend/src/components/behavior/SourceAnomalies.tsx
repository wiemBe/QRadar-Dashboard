// A source's anomalies, split by whether they still need work.
//
// The split is derived from the lifecycle state, never from `resolved_at IS
// NULL`. A NORMAL row with no resolved timestamp is not an active incident,
// and reading the absence of a timestamp as evidence of activity is the defect
// the backend corrected in ec5d352 — the UI must not reintroduce it.

import { TableScroll } from "@/components/ui/TableScroll";
import { sevTone, type AnomalySummary } from "@/lib/api";
import { evidenceTone, formatMetric, formatRatio, stateTone } from "@/lib/behavior";
import { formatDateTimeCompact } from "@/lib/health";

function AnomalyTable({
  anomalies,
  label,
  caption,
}: {
  anomalies: AnomalySummary[];
  label: string;
  caption: string;
}) {
  return (
    <TableScroll label={label}>
      <table className="sticky-actions">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Detector</th>
            <th scope="col">State</th>
            <th scope="col">Observed → Expected</th>
            <th scope="col">Deviation</th>
            <th scope="col">Started</th>
            <th scope="col">Evidence</th>
            <th scope="col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {anomalies.map((a) => (
            <tr key={a.id}>
              <td>{a.anomaly_type}</td>
              <td>
                <span className={`pill pill-strong ${stateTone(a.state)}`}>{a.state}</span>
              </td>
              <td className="num">
                {formatMetric(a.observed_value)}
                <span className="muted"> → </span>
                {formatMetric(a.expected_value)}
              </td>
              <td className="num">{formatRatio(a.deviation_ratio)}</td>
              <td className="num">
                <time dateTime={a.anomaly_start ?? a.detected_at}>
                  {formatDateTimeCompact(a.anomaly_start ?? a.detected_at)}
                </time>
              </td>
              <td>
                <span className={`pill pill-quiet ${evidenceTone(a.evidence_status)}`}>
                  {a.evidence_status}
                </span>
              </td>
              <td className="row-actions">
                <div className="row-actions-inner">
                  <a href={`/anomalies/${a.id}`}>Open</a>
                  <span className={`pill pill-quiet ${sevTone(a.severity)}`}>
                    {a.severity}
                  </span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

export function SourceAnomalies({
  active,
  recent,
  failed,
  windowLabel,
  unjudged,
}: {
  active: AnomalySummary[];
  recent: AnomalySummary[];
  /** The anomaly request failed; absence here is not an absence of anomalies. */
  failed: boolean;
  windowLabel: string;
  /** The source has no adequate baseline, so nothing has judged it. */
  unjudged: boolean;
}) {
  if (failed) {
    return (
      <div className="notice" role="alert">
        Anomaly history could not be loaded for this source. This is a failed
        request, not an absence of anomalies.
      </div>
    );
  }

  if (active.length === 0 && recent.length === 0) {
    return (
      <div className="notice">
        {unjudged
          ? "No anomalies — but this source has no adequate baseline, so no detector has judged it. This is not a clean result."
          : `No anomalies were detected in the last ${windowLabel}.`}
      </div>
    );
  }

  return (
    <div className="stack-6">
      <div>
        <h3>Needs attention</h3>
        {active.length === 0 ? (
          <p className="subtitle">
            No incident is currently open, a candidate, or recovering for this
            source.
          </p>
        ) : (
          <AnomalyTable
            anomalies={active}
            label="Active anomalies"
            caption="Anomalies still open, candidate or recovering for this source."
          />
        )}
      </div>

      <div>
        <h3>Recently closed</h3>
        {recent.length === 0 ? (
          <p className="subtitle">
            Nothing has closed for this source in this window.
          </p>
        ) : (
          <AnomalyTable
            anomalies={recent}
            label="Recently closed anomalies"
            caption="Anomalies that have resolved or are no longer active for this source."
          />
        )}
      </div>
    </div>
  );
}
