import { api } from "@/lib/api";
import { StatCard } from "@/components/StatCard";

// Server component: fetches on the server so the browser never needs API creds.
export default async function OverviewPage() {
  let data;
  try {
    data = await api.overview();
  } catch {
    return (
      <>
        <h2>SOC Overview</h2>
        <p className="subtitle">
          Backend unreachable. Start the stack with <code>docker compose up</code> and run an
          inventory sync from the Log Sources page.
        </p>
      </>
    );
  }

  const ls = data.log_sources;
  const off = data.offenses;
  return (
    <>
      <h2>SOC Overview</h2>
      <p className="subtitle">
        Instance {data.instance_status}
        {data.instance_version ? ` · QRadar ${data.instance_version}` : ""} · generated{" "}
        {new Date(data.generated_at).toLocaleTimeString()}
      </p>

      <div className="grid">
        <StatCard
          label="Avg Health"
          value={data.average_health_score ?? "—"}
          tone={
            data.average_health_score == null
              ? undefined
              : data.average_health_score >= 70
                ? "ok"
                : data.average_health_score >= 40
                  ? "warn"
                  : "crit"
          }
        />
        <StatCard label="Monitored Sources" value={ls.monitored_log_sources} />
        <StatCard label="Silent Sources" value={ls.silent_log_sources} tone={ls.silent_log_sources ? "crit" : "ok"} />
        <StatCard label="Anomalous Sources" value={ls.anomalous_log_sources} tone={ls.anomalous_log_sources ? "warn" : "ok"} />
        <StatCard label="In Maintenance" value={ls.in_maintenance} />
        <StatCard label="Active Offenses" value={off.active} />
        <StatCard label="Critical Offenses" value={off.critical} tone={off.critical ? "crit" : "ok"} />
        <StatCard label="Unassigned Offenses" value={off.unassigned} tone={off.unassigned ? "warn" : "ok"} />
        <StatCard label="Open Alerts" value={data.alerts.open} tone={data.alerts.open ? "warn" : "ok"} />
      </div>
    </>
  );
}
