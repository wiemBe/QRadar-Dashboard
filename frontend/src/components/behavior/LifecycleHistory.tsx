// Every recorded lifecycle transition for one anomaly, oldest first.
//
// Nothing is filtered. "This incident flapped CANDIDATE/NORMAL six times before
// opening" is a fact about detector tuning, and a page that shows only the
// transitions leading to the current state hides exactly the evidence that the
// thresholds need adjusting. Holds caused by incomplete data are shown too.

import type { AnomalyTransition } from "@/lib/api";
import { formatMetric, stateTone } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

export function LifecycleHistory({
  transitions,
  policyVersion,
}: {
  transitions: AnomalyTransition[];
  policyVersion: number | null;
}) {
  return (
    <section>
      <h3>Lifecycle history</h3>
      {transitions.length === 0 ? (
        <div className="notice">
          No transitions recorded. The anomaly exists but its lifecycle audit
          trail is empty, which is itself unexpected — the engine writes a
          transition for every state change.
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>From</th>
              <th>To</th>
              <th>Bucket</th>
              <th>Observed</th>
              <th>Expected</th>
              <th>Reason</th>
              <th>Actor</th>
              <th>Policy</th>
            </tr>
          </thead>
          <tbody>
            {transitions.map((t, i) => (
              <tr key={`${t.occurred_at}-${t.to_state}-${i}`}>
                <td>{formatDateTime(t.occurred_at)}</td>
                {/* The first transition has no previous state. That is an
                    origin, not an unknown, and is labelled as such. */}
                <td>
                  {t.from_state ? (
                    <span className={`pill ${stateTone(t.from_state)}`}>
                      {t.from_state}
                    </span>
                  ) : (
                    <span className="muted">initial</span>
                  )}
                </td>
                <td>
                  <span className={`pill ${stateTone(t.to_state)}`}>{t.to_state}</span>
                </td>
                <td>{formatDateTime(t.bucket_start)}</td>
                <td>{formatMetric(t.observed_value)}</td>
                <td>{formatMetric(t.expected_value)}</td>
                <td style={{ maxWidth: 320 }}>
                  {t.reason ?? <span className="muted">not recorded</span>}
                </td>
                <td>{t.actor}</td>
                <td>{policyVersion != null ? `v${policyVersion}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
