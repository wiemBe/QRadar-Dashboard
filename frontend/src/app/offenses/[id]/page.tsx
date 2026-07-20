import { api, type OffenseDetail, type OffenseHistoryPoint } from "@/lib/api";
import Link from "next/link";
import { formatAge, formatDateTime, magnitudeTone } from "@/lib/health";

function List({ label, values }: { label: string; values: (string | number)[] }) {
  return (
    <div className="detail-block">
      <h4>
        {label} <span className="muted">({values.length})</span>
      </h4>
      {values.length === 0 ? (
        <p className="muted">None recorded.</p>
      ) : (
        <ul className="chips">
          {values.map((v) => (
            <li key={String(v)} className="pill">
              {String(v)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default async function OffenseDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const offenseId = Number(id);

  if (!Number.isFinite(offenseId)) {
    return (
      <>
        <h2>Offense</h2>
        <div className="notice">Not a valid offense id.</div>
      </>
    );
  }

  let offense: OffenseDetail | null = null;
  let history: OffenseHistoryPoint[] = [];
  let missing = false;
  let error = false;

  try {
    offense = await api.offense(offenseId);
    // A missing history is not a missing offense — degrade rather than 404.
    history = await api.offenseHistory(offenseId).catch(() => []);
  } catch (e) {
    if (e && typeof e === "object" && "status" in e && e.status === 404) missing = true;
    else error = true;
  }

  if (missing || error) {
    return (
      <>
        <h2>Offense {offenseId}</h2>
        <div className="notice">
          {missing
            ? "This offense has not been collected. It may be outside the collection window."
            : "Could not load this offense. Try again shortly."}
        </div>
        <p>
          <Link href="/offenses">Back to offenses</Link>
        </p>
      </>
    );
  }

  const o = offense!;

  return (
    <>
      <p className="muted">
        <Link href="/offenses">← Offenses</Link>
      </p>
      <h2>Offense {o.qradar_offense_id}</h2>
      {/* Text only. QRadar-sourced, sanitized server-side, escaped by React. */}
      <p className="subtitle">{o.description?.trim() || "No description."}</p>

      <div className="cards">
        <div className="card">
          <div className="k">Magnitude</div>
          <div className={`v ${magnitudeTone(o.magnitude)}`}>{o.magnitude ?? "—"}</div>
        </div>
        <div className="card">
          <div className="k">Status</div>
          <div className="v">{o.status}</div>
        </div>
        <div className="card">
          <div className="k">Severity</div>
          <div className="v">{o.severity ?? "—"}</div>
        </div>
        <div className="card">
          <div className="k">Credibility</div>
          <div className="v">{o.credibility ?? "—"}</div>
        </div>
        <div className="card">
          <div className="k">Relevance</div>
          <div className="v">{o.relevance ?? "—"}</div>
        </div>
        <div className="card">
          <div className="k">Age</div>
          <div className="v">{formatAge(o.age_seconds)}</div>
        </div>
      </div>

      <h3>Attributes</h3>
      <table>
        <tbody>
          <tr>
            <th>Assigned to</th>
            <td>{o.assigned_to ?? <span className="muted">unassigned</span>}</td>
          </tr>
          <tr>
            <th>Offense type</th>
            <td>{o.offense_type_name ?? "—"}</td>
          </tr>
          <tr>
            <th>Offense source</th>
            <td>{o.offense_source ?? "—"}</td>
          </tr>
          <tr>
            <th>Source network</th>
            <td>{o.source_network ?? "—"}</td>
          </tr>
          <tr>
            <th>Events / Flows</th>
            <td>
              {o.event_count ?? 0} / {o.flow_count ?? 0}
            </td>
          </tr>
          <tr>
            <th>Sources / Destinations</th>
            <td>
              {o.source_count ?? 0} / {o.destination_count ?? 0}
            </td>
          </tr>
          <tr>
            <th>Started</th>
            <td>{formatDateTime(o.start_time)}</td>
          </tr>
          <tr>
            <th>Last updated</th>
            <td>{formatDateTime(o.last_updated_time)}</td>
          </tr>
          <tr>
            <th>Closed</th>
            <td>{formatDateTime(o.close_time)}</td>
          </tr>
          <tr>
            <th>Last collected</th>
            <td>{formatDateTime(o.captured_at)}</td>
          </tr>
        </tbody>
      </table>

      <h3>Entities</h3>
      <div className="detail-grid">
        <List label="Usernames" values={o.usernames ?? []} />
        <List label="Source addresses" values={o.source_addresses ?? []} />
        <List label="Destination addresses" values={o.local_destination_addresses ?? []} />
        <List label="Categories" values={o.categories ?? []} />
        <List label="Contributing rule IDs" values={o.rule_ids ?? []} />
        <List label="Log source IDs" values={o.log_source_ids ?? []} />
      </div>

      <h3>History</h3>
      {history.length === 0 ? (
        <div className="notice">
          No history yet. A second snapshot is written only when the offense
          changes, so a stable offense has a single entry.
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Captured</th>
              <th>Status</th>
              <th>Magnitude</th>
              <th>Severity</th>
              <th>Events</th>
              <th>Assigned</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.captured_at}>
                <td>{formatDateTime(h.captured_at)}</td>
                <td>{h.status}</td>
                <td>{h.magnitude ?? "—"}</td>
                <td>{h.severity ?? "—"}</td>
                <td>{h.event_count ?? "—"}</td>
                <td>{h.assigned_to ?? <span className="muted">unassigned</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
