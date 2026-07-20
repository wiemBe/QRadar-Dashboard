import { AlertActions } from "@/components/AlertActions";
import { Evidence } from "@/components/Evidence";
import { api, sevTone, type Notification } from "@/lib/api";

function notifTone(status: string): string {
  if (status === "SENT") return "ok";
  if (status === "DEAD_LETTER") return "crit";
  if (status === "FAILED") return "warn";
  return "";
}

export default async function AlertDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let alert;
  let notifications: Notification[] = [];
  try {
    [alert, notifications] = await Promise.all([api.alert(id), api.alertNotifications(id)]);
  } catch {
    return (
      <>
        <h2>Alert Detail</h2>
        <div className="notice">Could not load alert {id}.</div>
      </>
    );
  }

  return (
    <>
      <h2>{alert.title}</h2>
      <p className="subtitle">
        <span className={`pill ${sevTone(alert.severity)}`}>{alert.severity}</span>{" "}
        <span className="pill">{alert.status}</span> · seen {alert.occurrence_count}× ·
        fingerprint <code>{alert.fingerprint}</code>
      </p>

      <div className="card">
        <div className="k">Evidence</div>
        <div style={{ marginTop: 8 }}>
          <Evidence data={alert.evidence_snapshot} />
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="k">Lifecycle</div>
        <p>Opened: {new Date(alert.opened_at).toLocaleString()}</p>
        <p>Acknowledged by: {alert.acknowledged_by ?? "—"}</p>
        <p>Resolved by: {alert.resolved_by ?? "—"}</p>
        <p>Resolution: {alert.resolution_reason ?? "—"}</p>
        <p>Source anomalies: {alert.source_anomaly_ids.length}</p>
      </div>

      <AlertActions alertId={alert.id} status={alert.status} />

      <h3 style={{ marginTop: 24 }}>Notification Delivery History</h3>
      {notifications.length === 0 ? (
        <div className="notice">No notifications for this alert.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Channel</th>
              <th>Transition</th>
              <th>Status</th>
              <th>Attempts</th>
              <th>Sent</th>
              <th>Next attempt</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {notifications.map((n) => (
              <tr key={n.id}>
                <td>{n.channel}</td>
                <td>{n.transition}</td>
                <td><span className={`pill ${notifTone(n.status)}`}>{n.status}</span></td>
                <td>{n.attempts}/{n.max_attempts}</td>
                <td>{n.sent_at ? new Date(n.sent_at).toLocaleString() : "—"}</td>
                <td>{n.next_attempt_at ? new Date(n.next_attempt_at).toLocaleString() : "—"}</td>
                <td>{n.error_message ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
