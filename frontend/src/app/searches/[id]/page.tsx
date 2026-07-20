import { api, type SearchExecution } from "@/lib/api";

function execTone(status: string): string {
  if (status === "COMPLETED") return "ok";
  if (status === "FAILED" || status === "TIMEOUT") return "crit";
  return "warn";
}

export default async function SearchDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let search;
  let versions;
  let executions: SearchExecution[] = [];
  try {
    [search, versions, executions] = await Promise.all([
      api.search(id),
      api.searchVersions(id),
      api.searchExecutions(id),
    ]);
  } catch {
    return (
      <>
        <h2>Search Detail</h2>
        <div className="notice">Could not load search {id}.</div>
      </>
    );
  }

  return (
    <>
      <h2>{search.name}</h2>
      <p className="subtitle">
        {search.category ?? "uncategorised"} · severity {search.severity} · v
        {search.query_version} · <code>{search.schedule_cron}</code>
      </p>

      <div className="card">
        <div className="k">Current AQL (v{search.query_version})</div>
        <pre style={{ whiteSpace: "pre-wrap", margin: "8px 0 0" }}>{search.aql_query}</pre>
      </div>

      <h3 style={{ marginTop: 24 }}>Execution History</h3>
      {executions.length === 0 ? (
        <div className="notice">No executions yet.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Started</th>
              <th>Status</th>
              <th>Trigger</th>
              <th>Ver</th>
              <th>Duration</th>
              <th>Results</th>
              <th>Retries</th>
              <th>Threshold</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {executions.map((e) => (
              <tr key={e.id}>
                <td>{e.started_at ? new Date(e.started_at).toLocaleString() : "—"}</td>
                <td><span className={`pill ${execTone(e.status)}`}>{e.status}</span></td>
                <td>{e.trigger}</td>
                <td>v{e.query_version}</td>
                <td>{e.duration_ms != null ? `${e.duration_ms} ms` : "—"}</td>
                <td>{e.result_count ?? "—"}{e.truncated ? " (trunc)" : ""}</td>
                <td>{e.retry_count}</td>
                <td>{e.threshold_breached ? <span className="pill crit">breached</span> : "—"}</td>
                <td>{e.error_type ? <span className="pill crit">{e.error_type}</span> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3 style={{ marginTop: 24 }}>Query Version History</h3>
      <table>
        <thead>
          <tr>
            <th>Version</th>
            <th>Changed by</th>
            <th>Note</th>
            <th>When</th>
          </tr>
        </thead>
        <tbody>
          {versions.map((v) => (
            <tr key={v.version}>
              <td>v{v.version}</td>
              <td>{v.changed_by ?? "—"}</td>
              <td>{v.change_note ?? "—"}</td>
              <td>{new Date(v.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="subtitle" style={{ marginTop: 16 }}>
        Result-trend charts (Apache ECharts) render here once executions accumulate. Trends
        annotate query-version boundaries, since results across versions are not comparable.
      </p>
    </>
  );
}
