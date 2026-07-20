import { api, type LogSourceSummary } from "@/lib/api";

function healthTone(score: number | null): "ok" | "warn" | "crit" | undefined {
  if (score == null) return undefined;
  if (score >= 70) return "ok";
  if (score >= 40) return "warn";
  return "crit";
}

export default async function LogSourcesPage() {
  let sources: LogSourceSummary[] = [];
  let error = false;
  try {
    sources = await api.logSources();
  } catch {
    error = true;
  }

  return (
    <>
      <h2>Log Sources</h2>
      <p className="subtitle">
        {sources.length} sources. QRadar-owned fields refresh on sync; criticality, owner and
        maintenance are SOC-owned and preserved.
      </p>

      {error ? (
        <div className="notice">
          Backend unreachable — start the stack, then POST <code>/api/v1/log-sources/sync</code>.
        </div>
      ) : sources.length === 0 ? (
        <div className="notice">
          No log sources yet. Trigger an inventory sync to pull them from the configured provider.
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Criticality</th>
              <th>Status</th>
              <th>Health</th>
              <th>Anomalies</th>
              <th>Last Event</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.id}>
                <td>
                  <a href={`/log-sources/${s.id}`}>{s.name}</a>
                  {s.maintenance_mode && <span className="pill warn"> maint</span>}
                </td>
                <td>{s.type_name ?? "—"}</td>
                <td>{s.criticality}</td>
                <td>
                  <span className={`pill ${s.qradar_status === "SUCCESS" ? "ok" : "crit"}`}>
                    {s.qradar_status ?? "unknown"}
                  </span>
                </td>
                <td>
                  <span className={`pill ${healthTone(s.health_score) ?? ""}`}>
                    {s.health_score ?? "—"}
                  </span>
                </td>
                <td>{s.open_anomaly_count > 0 ? <span className="pill warn">{s.open_anomaly_count}</span> : "0"}</td>
                <td>{s.last_event_time ? new Date(s.last_event_time).toLocaleString() : "never"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
