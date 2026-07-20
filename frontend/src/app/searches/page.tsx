import { api, sevTone, type ScheduledSearch } from "@/lib/api";

export default async function SearchCatalogPage() {
  let searches: ScheduledSearch[] = [];
  let error = false;
  try {
    searches = await api.searches();
  } catch {
    error = true;
  }

  return (
    <>
      <h2>Search Catalog</h2>
      <p className="subtitle">
        Stored, validated AQL searches. Only persisted definitions are scheduled or executed —
        the frontend never runs ad-hoc AQL.
      </p>

      {error ? (
        <div className="notice">Backend unreachable.</div>
      ) : searches.length === 0 ? (
        <div className="notice">No scheduled searches defined yet.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Category</th>
              <th>MITRE</th>
              <th>Schedule</th>
              <th>Severity</th>
              <th>Ver</th>
              <th>Enabled</th>
            </tr>
          </thead>
          <tbody>
            {searches.map((s) => (
              <tr key={s.id}>
                <td>
                  <a href={`/searches/${s.id}`}>{s.name}</a>
                </td>
                <td>{s.category ?? "—"}</td>
                <td>{s.mitre_techniques.join(", ") || "—"}</td>
                <td><code>{s.schedule_cron}</code></td>
                <td><span className={`pill ${sevTone(s.severity)}`}>{s.severity}</span></td>
                <td>v{s.query_version}</td>
                <td>{s.enabled ? "yes" : <span className="pill warn">no</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
