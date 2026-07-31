import {
  api,
  type RuleDetail,
  type RuleHealthSnapshot,
} from "@/lib/api";
import Link from "next/link";
import { formatDateTime, healthMeaning, healthTone, isUnestablished } from "@/lib/health";

export default async function RuleDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let rule: RuleDetail | null = null;
  let history: RuleHealthSnapshot[] = [];
  let missing = false;
  let error = false;

  try {
    [rule, history] = await Promise.all([
      api.rule(id),
      api.ruleHealthHistory(id).catch(() => []),
    ]);
  } catch (e) {
    if (e && typeof e === "object" && "status" in e && e.status === 404) missing = true;
    else error = true;
  }

  if (missing || error || !rule) {
    return (
      <>
        <h2>Rule</h2>
        <div className="notice">
          {missing
            ? "This rule has not been collected."
            : "Could not load this rule. Try again shortly."}
        </div>
        <p>
          <Link href="/rules">Back to rules</Link>
        </p>
      </>
    );
  }

  const r = rule;
  const latest = history[0] ?? null;
  const buildingBlocks = r.dependencies.filter((d) => d.kind === "BUILDING_BLOCK");
  const telemetry = r.dependencies.filter((d) => d.kind !== "BUILDING_BLOCK");

  return (
    <>
      <p className="muted">
        <Link href="/rules">← Rule Health</Link>
      </p>
      <h2>{r.name}</h2>
      <p className="subtitle">
        QRadar rule {r.qradar_id}
        {r.is_building_block ? " · building block" : ""}
      </p>

      <div className="cards">
        <div className="card">
          <div className="k">Health</div>
          <div className={`v ${healthTone(r.health_status)}`}>
            {r.health_status?.replace(/_/g, " ") ?? "—"}
          </div>
        </div>
        <div className="card">
          <div className="k">Enabled</div>
          <div className={`v ${r.enabled ? "ok" : "warn"}`}>{r.enabled ? "Yes" : "No"}</div>
        </div>
        <div className="card">
          <div className="k">Offense contributions</div>
          <div className="v">{r.offense_contribution_count}</div>
        </div>
        <div className="card">
          <div className="k">Event contributions</div>
          <div className="v">{r.event_contribution_count}</div>
        </div>
      </div>

      <h3>Health evidence</h3>
      {isUnestablished(r.health_status) ? (
        <div className="notice">
          <strong>{healthMeaning(r.health_status)}</strong>
          <p>
            {latest?.reason ??
              "No complete rule-metric observation covers this rule. Missing data is reported as unestablished rather than as a detection gap."}
          </p>
        </div>
      ) : (
        <p>{latest?.reason ?? healthMeaning(r.health_status)}</p>
      )}

      <div className="detail-grid">
        <div className="detail-block">
          <h4>Building blocks ({buildingBlocks.length})</h4>
          {buildingBlocks.length === 0 ? (
            <p className="muted">No building-block dependencies were exposed by QRadar.</p>
          ) : (
            <ul>
              {buildingBlocks.map((d) => (
                <li key={`${d.kind}:${d.target_ref}`}>
                  {d.target_name ?? d.target_ref} · {d.source.toLowerCase()} · {Math.round(d.confidence * 100)}%
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="detail-block">
          <h4>Telemetry requirements ({telemetry.length})</h4>
          {telemetry.length === 0 ? (
            <p className="muted">No log-source dependencies were exposed by QRadar.</p>
          ) : (
            <ul>
              {telemetry.map((d) => (
                <li key={`${d.kind}:${d.target_ref}`}>
                  {d.target_name ?? d.target_ref} · {d.kind.replace(/_/g, " ")} · {d.source.toLowerCase()}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <h3>Metadata</h3>
      <table>
        <tbody>
          <tr>
            <th>Rule type</th>
            <td>{r.rule_type ?? "—"}</td>
          </tr>
          <tr>
            <th>Origin</th>
            <td>{r.origin ?? "—"}</td>
          </tr>
          <tr>
            <th>Owner</th>
            <td>{r.owner ?? "—"}</td>
          </tr>
          <tr>
            <th>Building block</th>
            <td>{r.is_building_block ? "Yes" : "No"}</td>
          </tr>
          <tr>
            <th>Last fired</th>
            <td>
              {r.last_fired_at ? (
                formatDateTime(r.last_fired_at)
              ) : (
                <span className="muted">not observed</span>
              )}
            </td>
          </tr>
          <tr>
            <th>Health evaluated</th>
            <td>{formatDateTime(r.health_evaluated_at)}</td>
          </tr>
          <tr>
            <th>MITRE techniques</th>
            <td>
              {r.mitre_techniques.length === 0 ? (
                <span className="muted">none mapped</span>
              ) : (
                <ul className="chips">
                  {r.mitre_techniques.map((t) => (
                    <li key={t} className="pill">
                      {t}
                    </li>
                  ))}
                </ul>
              )}
            </td>
          </tr>
        </tbody>
      </table>

      <h3>Health history</h3>
      {history.length === 0 ? (
        <div className="notice">No rule-health evaluation has been stored yet.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Evaluated</th>
              <th>Status</th>
              <th>Confidence</th>
              <th>Triggers</th>
              <th>Offenses</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.evaluated_at}>
                <td>{formatDateTime(h.evaluated_at)}</td>
                <td><span className={`pill ${healthTone(h.status)}`}>{h.status.replace(/_/g, " ")}</span></td>
                <td>{Math.round(h.confidence * 100)}%</td>
                <td>{h.trigger_count}</td>
                <td>{h.offense_contribution_count}</td>
                <td className="muted">{h.reason ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
