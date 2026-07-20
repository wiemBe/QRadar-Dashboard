"use client";

// Manual execution control for a stored scheduled search.
//
// Only the search id crosses the client boundary. The AQL is never sent from the
// browser — the backend runs the stored, validated, versioned query.

import { useRouter } from "next/navigation";
import { useState } from "react";

import { actionErrorMessage, api, type SearchExecution } from "@/lib/api";

export function RunSearchButton({ searchId }: { searchId: string }) {
  const router = useRouter();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SearchExecution | null>(null);

  async function run() {
    // A search run costs a real Ariel query; a double click must not buy two.
    if (running) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const execution = await api.runSearch(searchId);
      setResult(execution);
      // Bring the new execution into the history table below.
      router.refresh();
    } catch (err) {
      setError(actionErrorMessage(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="actions">
      <button
        type="button"
        className="action"
        onClick={run}
        disabled={running}
        aria-busy={running}
      >
        {running ? "Running…" : "Run now"}
      </button>

      {result && (
        <span role="status" className={`pill ${result.status === "COMPLETED" ? "ok" : "warn"}`}>
          {result.status}
          {result.result_count != null ? ` · ${result.result_count} results` : ""}
        </span>
      )}
      {error && (
        <span role="alert" className="pill crit">
          {error}
        </span>
      )}
    </div>
  );
}
