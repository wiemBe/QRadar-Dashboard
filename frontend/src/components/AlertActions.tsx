"use client";

// The only interactive island on the alert detail page. The page itself stays a
// server component and passes primitives across the boundary — no alert object,
// no session, no credentials.

import { useRouter } from "next/navigation";
import { useState } from "react";

import { actionErrorMessage, api } from "@/lib/api";

// Mirrors AlertActionIn.reason (max_length=1000) in app/schemas/alert.py.
// The backend leaves the reason optional, so this does not force one; it only
// stops a request that the schema would reject from being sent at all.
const REASON_MAX = 1000;

type Action = "acknowledge" | "resolve";

export function AlertActions({ alertId, status }: { alertId: string; status: string }) {
  const router = useRouter();
  const [pending, setPending] = useState<Action | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const busy = pending !== null;

  async function run(action: Action, call: () => Promise<unknown>, success: string) {
    // Belt and braces with the disabled attribute: a double-submit would enqueue
    // a second notification and write a second audit row.
    if (busy) return;
    setPending(action);
    setError(null);
    setDone(null);
    try {
      await call();
      setDone(success);
      // Re-fetch the server component so lifecycle and delivery history reflect
      // the change instead of showing stale data next to a success message.
      router.refresh();
    } catch (err) {
      setError(actionErrorMessage(err));
    } finally {
      setPending(null);
    }
  }

  if (status === "RESOLVED") {
    return (
      <div className="card" style={{ marginTop: 16 }}>
        <div className="k">Operator Actions</div>
        <p className="subtitle" style={{ margin: "8px 0 0" }}>
          This alert is resolved. No further action is available.
        </p>
      </div>
    );
  }

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="k">Operator Actions</div>

      <div className="actions" style={{ marginTop: 10 }}>
        {status === "OPEN" && (
          <button
            type="button"
            className="action"
            disabled={busy}
            aria-busy={pending === "acknowledge"}
            onClick={() =>
              run("acknowledge", () => api.acknowledgeAlert(alertId), "Alert acknowledged.")
            }
          >
            {pending === "acknowledge" ? "Acknowledging…" : "Acknowledge"}
          </button>
        )}

        <input
          type="text"
          className="field"
          placeholder="Resolution reason (optional)"
          maxLength={REASON_MAX}
          value={reason}
          disabled={busy}
          aria-label="Resolution reason"
          onChange={(e) => setReason(e.target.value)}
        />

        <button
          type="button"
          className="action secondary"
          disabled={busy}
          aria-busy={pending === "resolve"}
          onClick={() =>
            run("resolve", () => api.resolveAlert(alertId, reason.trim()), "Alert resolved.")
          }
        >
          {pending === "resolve" ? "Resolving…" : "Resolve"}
        </button>
      </div>

      {done && (
        <p role="status" className="pill ok" style={{ display: "inline-block", marginTop: 10 }}>
          {done}
        </p>
      )}
      {error && (
        <p role="alert" className="pill crit" style={{ display: "inline-block", marginTop: 10 }}>
          {error}
        </p>
      )}
    </div>
  );
}
