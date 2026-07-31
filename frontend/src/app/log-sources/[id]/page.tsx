import { api } from "@/lib/api";

export default async function LogSourceDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const d = await api.logSource(id).catch(() => null);
  if (!d) {
    return (
      <>
        <h2>Log Source Detail</h2>
        <div className="notice">Could not load log source {id}.</div>
      </>
    );
  }
  const hb = d.health_breakdown;

  return (
    <>
      <h2>{d.name}</h2>
      <p className="subtitle">
        {d.type_name ?? "unknown type"} · criticality {d.criticality} · owner {d.owner ?? "—"}
      </p>

      {hb && (
        <div className="grid">
          <div className="card">
            <div className="k">Overall Health</div>
            <div className="v">{hb.score}</div>
          </div>
          <div className="card">
            <div className="k">Freshness (40%)</div>
            <div className="v">{hb.freshness}</div>
          </div>
          <div className="card">
            <div className="k">Volume (25%)</div>
            <div className="v">{hb.volume}</div>
          </div>
          <div className="card">
            <div className="k">Parsing (20%)</div>
            <div className="v">{hb.parsing}</div>
          </div>
          <div className="card">
            <div className="k">Collection (15%)</div>
            <div className="v">{hb.collection}</div>
          </div>
        </div>
      )}

      <div className="card" style={{ marginTop: 20 }}>
        <div className="k">Configuration</div>
        <p>Description: {d.description ?? "—"}</p>
        <p>Business hours only: {d.business_hours_only ? "yes" : "no"}</p>
        <p>Expected interval: {d.expected_interval_seconds ?? "—"} s</p>
        <p>Last event: {d.last_event_time ? new Date(d.last_event_time).toLocaleString() : "never"}</p>
        <p>QRadar status: {d.qradar_status ?? "unknown"}</p>
      </div>

      <p className="subtitle" style={{ marginTop: 20 }}>
        EPS trend, anomaly timeline and baseline overlay charts (Apache ECharts) arrive in Phase 2
        once metric collection is populating the time-series tables.
      </p>
    </>
  );
}
