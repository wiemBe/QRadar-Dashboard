// The source's identity, in one block.
//
// The name appears once, as the page's <h1> — the previous header repeated it
// in the title and then again as the first thing in a card row. Behaviour
// state is the dominant badge; monitoring and maintenance are real but
// subordinate, and render quietly beside it.

import Link from "next/link";

import type { LogSourceDetail, SourceBehavior } from "@/lib/api";
import { stateTone } from "@/lib/behavior";

export function SourceHeader({
  behavior,
  meta,
}: {
  behavior: SourceBehavior;
  /** Inventory metadata. Null when that call failed; the page still renders. */
  meta: LogSourceDetail | null;
}) {
  return (
    <header className="page-header incident-header">
      <div>
        <p className="incident-back">
          <Link href="/behavior/sources">← All sources</Link>
        </p>

        <h1 className="page-title">{behavior.name}</h1>

        <div className="row incident-meta">
          <span className={`pill pill-strong ${stateTone(behavior.state)}`}>
            {behavior.state}
          </span>

          {/* Secondary identity, so an analyst can match this against QRadar. */}
          {meta?.qradar_id != null && (
            <span className="muted">QRadar ID {meta.qradar_id}</span>
          )}
          {meta?.type_name && <span className="muted">{meta.type_name}</span>}
          <span className="muted">Criticality {behavior.criticality}</span>

          {/* Both of these change how the numbers below should be read, so
              they are shown whenever they are not the default. */}
          {meta?.monitoring_enabled === false && (
            <span className="pill pill-quiet warn">Monitoring disabled</span>
          )}
          {meta?.maintenance_mode && (
            <span className="pill pill-quiet warn">Maintenance mode</span>
          )}
        </div>
      </div>

      <div className="page-header-actions">
        <Link
          href={`/log-sources/${behavior.log_source_id}`}
          className="action secondary button-link"
        >
          Inventory detail
        </Link>
      </div>
    </header>
  );
}
