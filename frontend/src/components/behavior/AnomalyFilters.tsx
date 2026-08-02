// Filters for the anomaly list.
//
// A plain GET form, like the offense filters: no JavaScript needed, the
// resulting URL is shareable, and submitting resets paging naturally because
// `offset` is simply absent from the new query string.

import Link from "next/link";

import type { SourceBehavior } from "@/lib/api";

const DETECTOR_TYPES = ["VOLUME_SPIKE", "VOLUME_DROP", "NO_EVENTS"];
const STATES = [
  "CANDIDATE",
  "OPEN",
  "RECOVERING",
  "RESOLVED",
  "SUPPRESSED",
  "NORMAL",
  "INSUFFICIENT_DATA",
];
const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];
const EVIDENCE = [
  "NOT_REQUESTED",
  "PENDING",
  "COMPLETE",
  "PARTIAL",
  "UNAVAILABLE",
  "FAILED",
];

export interface AnomalyFilterValues {
  log_source_id: string;
  instance_id: string;
  anomaly_type: string;
  state: string;
  severity: string;
  evidence_status: string;
  since: string;
  until: string;
}

export function AnomalyFilters({
  values,
  sources,
}: {
  values: AnomalyFilterValues;
  sources: SourceBehavior[];
}) {
  const active = Object.values(values).some(Boolean);

  return (
    <form className="filters" method="get" action="/anomalies">
      <select name="log_source_id" defaultValue={values.log_source_id} aria-label="Log source">
        <option value="">All log sources</option>
        {sources.map((s) => (
          <option key={s.log_source_id} value={s.log_source_id}>
            {s.name}
          </option>
        ))}
      </select>

      <select name="anomaly_type" defaultValue={values.anomaly_type} aria-label="Detector type">
        <option value="">All detectors</option>
        {DETECTOR_TYPES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>

      <select name="state" defaultValue={values.state} aria-label="Lifecycle state">
        <option value="">All states</option>
        {STATES.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>

      <select name="severity" defaultValue={values.severity} aria-label="Severity">
        <option value="">All severities</option>
        {SEVERITIES.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>

      <select
        name="evidence_status"
        defaultValue={values.evidence_status}
        aria-label="Evidence status"
      >
        <option value="">All evidence states</option>
        {EVIDENCE.map((e) => (
          <option key={e} value={e}>
            {e}
          </option>
        ))}
      </select>

      {/* The backend caps the range; these are datetime-local so an operator
          cannot accidentally request an unbounded window. */}
      <input
        type="datetime-local"
        name="since"
        defaultValue={values.since}
        aria-label="Start time"
        className="field"
      />
      <input
        type="datetime-local"
        name="until"
        defaultValue={values.until}
        aria-label="End time"
        className="field"
      />

      {/* Carried through submissions so a filtered instance view stays filtered. */}
      {values.instance_id && (
        <input type="hidden" name="instance_id" value={values.instance_id} />
      )}

      <button type="submit">Filter</button>
      {active && <Link href="/anomalies">Clear</Link>}
    </form>
  );
}
