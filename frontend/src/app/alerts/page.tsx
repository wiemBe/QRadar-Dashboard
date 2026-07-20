import { api, sevTone, type Alert } from "@/lib/api";

function statusTone(status: string): string {
  if (status === "OPEN") return "crit";
  if (status === "ACKNOWLEDGED") return "warn";
  return "ok";
}

export default async function AlertsPage() {
  let alerts: Alert[] = [];
  let error = false;
  try {
    alerts = await api.alerts();
  } catch {
    error = true;
  }

  return (
    <>
      <h2>Alerts</h2>
      <p className="subtitle">
        Lifecycle OPEN → ACKNOWLEDGED → RESOLVED. Repeated detections update the open alert and
        bump its occurrence count rather than creating duplicates.
      </p>

      {error ? (
        <div className="notice">Backend unreachable.</div>
      ) : alerts.length === 0 ? (
        <div className="notice">No alerts.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Severity</th>
              <th>Status</th>
              <th>Occurrences</th>
              <th>First seen</th>
              <th>Last seen</th>
            </tr>
          </thead>
          <tbody>
            {alerts.map((a) => (
              <tr key={a.id}>
                <td><a href={`/alerts/${a.id}`}>{a.title}</a></td>
                <td><span className={`pill ${sevTone(a.severity)}`}>{a.severity}</span></td>
                <td><span className={`pill ${statusTone(a.status)}`}>{a.status}</span></td>
                <td>{a.occurrence_count}</td>
                <td>{new Date(a.first_seen_at).toLocaleString()}</td>
                <td>{a.last_seen_at ? new Date(a.last_seen_at).toLocaleString() : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
