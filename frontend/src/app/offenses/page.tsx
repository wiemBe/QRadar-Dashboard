import { api, type OffenseAggregates, type OffenseSummary, type Page } from "@/lib/api";
import { StatCard } from "@/components/StatCard";
import { formatAge, formatDateTime, magnitudeTone } from "@/lib/health";
import { Pagination } from "@/components/Pagination";
import { OffenseFilters } from "@/components/OffenseFilters";

const PAGE_SIZE = 25;

export default async function OffensesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const offset = Math.max(0, Number(params.offset ?? 0) || 0);
  const search = params.search ?? "";
  const status = params.status ?? "";

  let page: Page<OffenseSummary> | null = null;
  let aggregates: OffenseAggregates | null = null;
  let error = false;

  try {
    // Fetched together: the counters describe the whole instance, not the
    // current page, so they must not be derived from `items`.
    [page, aggregates] = await Promise.all([
      api.offenses({ limit: PAGE_SIZE, offset, search, status }),
      api.offenseAggregates(),
    ]);
  } catch {
    error = true;
  }

  if (error) {
    return (
      <>
        <h2>Offenses</h2>
        <div className="notice">
          Backend unreachable. Start the stack, then run{" "}
          <code>python -m app.cli.sync offenses</code>.
        </div>
      </>
    );
  }

  const items = page?.items ?? [];

  return (
    <>
      <h2>Offenses</h2>
      <p className="subtitle">
        Read-only view of offenses collected from QRadar. This page issues no writes:
        offenses are triaged in QRadar, not here.
      </p>

      {aggregates && (
        <div className="cards">
          <StatCard label="Total" value={page?.total ?? 0} />
          <StatCard label="Active" value={aggregates.active} />
          <StatCard
            label={`Critical (mag ${aggregates.critical_magnitude}+)`}
            value={aggregates.critical}
            tone={aggregates.critical > 0 ? "crit" : undefined}
          />
          <StatCard
            label="Unassigned"
            value={aggregates.unassigned}
            tone={aggregates.unassigned > 0 ? "warn" : undefined}
          />
          <StatCard
            label={`Over ${aggregates.sla_hours}h SLA`}
            value={aggregates.exceeding_sla}
            tone={aggregates.exceeding_sla > 0 ? "warn" : undefined}
          />
          <StatCard label="Oldest" value={formatAge(aggregates.oldest_age_seconds)} />
        </div>
      )}

      <OffenseFilters search={search} status={status} />

      {items.length === 0 ? (
        <div className="notice">
          {search || status
            ? "No offenses match these filters."
            : "No offenses collected yet. Run an offense sync to pull them from QRadar."}
        </div>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Description</th>
                <th>Mag</th>
                <th>Sev</th>
                <th>Status</th>
                <th>Assigned</th>
                <th>Events</th>
                <th>Started</th>
                <th>Age</th>
              </tr>
            </thead>
            <tbody>
              {items.map((o) => (
                <tr key={o.qradar_offense_id}>
                  <td>
                    <a href={`/offenses/${o.qradar_offense_id}`}>{o.qradar_offense_id}</a>
                  </td>
                  {/* Rendered as text, never as markup: this string comes from
                      QRadar. The backend sanitizes it; React escapes it again. */}
                  <td>{o.description?.trim() || "—"}</td>
                  <td>
                    <span className={`pill ${magnitudeTone(o.magnitude)}`}>
                      {o.magnitude ?? "—"}
                    </span>
                  </td>
                  <td>{o.severity ?? "—"}</td>
                  <td>
                    <span className={`pill ${o.status === "OPEN" ? "warn" : "ok"}`}>
                      {o.status}
                    </span>
                  </td>
                  <td>{o.assigned_to ?? <span className="muted">unassigned</span>}</td>
                  <td>{o.event_count ?? "—"}</td>
                  <td>{formatDateTime(o.start_time)}</td>
                  <td>{formatAge(o.age_seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <Pagination
            total={page?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            basePath="/offenses"
            params={{ search, status }}
          />
        </>
      )}
    </>
  );
}
