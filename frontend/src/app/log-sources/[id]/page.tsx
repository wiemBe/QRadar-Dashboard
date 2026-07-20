const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

interface Detail {
  id: string;
  name: string;
  type_name: string | null;
  description: string | null;
  criticality: string;
  owner: string | null;
  owner_email: string | null;
  qradar_status: string | null;
  health_score: number | null;
  health_breakdown: {
    score: number;
    freshness: number;
    volume: number;
    parsing: number;
    collection: number;
  } | null;
  business_hours_only: boolean;
  expected_interval_seconds: number | null;
  last_event_time: string | null;
}

export default async function LogSourceDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const res = await fetch(`${BASE}/log-sources/${id}`, { cache: "no-store" }).catch(() => null);
  if (!res || !res.ok) {
    return (
      <>
        <h2>Log Source Detail</h2>
        <div className="notice">Could not load log source {id}.</div>
      </>
    );
  }
  const d: Detail = await res.json();
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
