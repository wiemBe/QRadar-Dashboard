import { api, sevTone, type Anomaly } from "@/lib/api";

export default async function AnomaliesPage() {
  let anomalies: Anomaly[] = [];
  let error = false;
  try {
    anomalies = await api.anomalies();
  } catch {
    error = true;
  }

  return (
    <>
      <h2>Anomalies</h2>
      <p className="subtitle">
        Detected log-source deviations with structured evidence — observed vs expected, the
        baseline band, deviation score and a human-readable reason.
      </p>

      {error ? (
        <div className="notice">Backend unreachable.</div>
      ) : anomalies.length === 0 ? (
        <div className="notice">No anomalies detected.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Type</th>
              <th>Severity</th>
              <th>Detected</th>
              <th>Observed</th>
              <th>Expected</th>
              <th>Deviation</th>
              <th>Reason</th>
              <th>State</th>
            </tr>
          </thead>
          <tbody>
            {anomalies.map((a) => (
              <tr key={a.id}>
                <td>{a.anomaly_type}</td>
                <td><span className={`pill ${sevTone(a.severity)}`}>{a.severity}</span></td>
                <td>{new Date(a.detected_at).toLocaleString()}</td>
                <td>{a.observed_value ?? "—"}</td>
                <td>{a.expected_value ?? "—"}</td>
                <td>{a.deviation_score ?? "—"}</td>
                <td style={{ maxWidth: 320 }}>{a.explanation ?? "—"}</td>
                <td>
                  {a.suppressed ? (
                    <span className="pill warn">suppressed</span>
                  ) : a.resolved_at ? (
                    <span className="pill ok">resolved</span>
                  ) : (
                    <span className="pill crit">open</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
