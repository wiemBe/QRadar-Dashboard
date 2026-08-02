// Filters for the anomaly list.
//
// A plain GET form: no client JavaScript, the resulting URL is shareable, and
// submitting resets paging naturally because `offset` is simply absent from
// the new query string.
//
// Four filters are visible and three are behind a disclosure. The split is by
// how often each is used from this page rather than by importance — an analyst
// arriving at the list narrows by state, detector, severity and time; source
// and instance are usually already chosen by the link that brought them here,
// and evidence status is an auditing filter. The disclosure opens itself when
// one of the filters inside it is active, so a filtered view never hides the
// control responsible for it.

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

/** Human labels for the active-filter chips. */
export function chipLabel(
  key: keyof AnomalyFilterValues,
  value: string,
  sources: SourceBehavior[],
): string {
  switch (key) {
    case "log_source_id": {
      const match = sources.find((s) => s.log_source_id === value);
      return `Source: ${match?.name ?? value}`;
    }
    case "instance_id":
      return `Instance: ${value}`;
    case "anomaly_type":
      return `Detector: ${value}`;
    case "state":
      return `State: ${value}`;
    case "severity":
      return `Severity: ${value}`;
    case "evidence_status":
      return `Evidence: ${value}`;
    case "since":
      return `From: ${value.replace("T", " ")}`;
    case "until":
      return `To: ${value.replace("T", " ")}`;
    default:
      return `${key}: ${value}`;
  }
}

/** The URL with one filter removed, so each chip can clear just itself. */
export function withoutFilter(
  values: AnomalyFilterValues,
  drop: keyof AnomalyFilterValues,
): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(values)) {
    if (v && k !== drop) qs.set(k, v);
  }
  const s = qs.toString();
  return s ? `/anomalies?${s}` : "/anomalies";
}

export function AnomalyFilters({
  values,
  sources,
}: {
  values: AnomalyFilterValues;
  sources: SourceBehavior[];
}) {
  const advancedActive = Boolean(
    values.log_source_id || values.instance_id || values.evidence_status,
  );
  const activeChips = (
    Object.entries(values) as Array<[keyof AnomalyFilterValues, string]>
  ).filter(([, v]) => Boolean(v));

  return (
    <>
      <form className="filters" method="get" action="/anomalies">
        <label className="sr-only" htmlFor="anomaly-state">
          Lifecycle state
        </label>
        <select id="anomaly-state" name="state" defaultValue={values.state}>
          <option value="">All states</option>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="anomaly-detector">
          Detector type
        </label>
        <select
          id="anomaly-detector"
          name="anomaly_type"
          defaultValue={values.anomaly_type}
        >
          <option value="">All detectors</option>
          {DETECTOR_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="anomaly-severity">
          Severity
        </label>
        <select id="anomaly-severity" name="severity" defaultValue={values.severity}>
          <option value="">All severities</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        {/* The backend caps the range; these are datetime-local so an operator
            cannot accidentally request an unbounded window. */}
        <label className="sr-only" htmlFor="anomaly-since">
          Start time
        </label>
        <input
          id="anomaly-since"
          type="datetime-local"
          name="since"
          defaultValue={values.since}
        />
        <label className="sr-only" htmlFor="anomaly-until">
          End time
        </label>
        <input
          id="anomaly-until"
          type="datetime-local"
          name="until"
          defaultValue={values.until}
        />

        <button type="submit">Filter</button>

        {/* Opened when something inside is active, so a filtered view never
            hides the control responsible for it. */}
        <details className="disclosure filter-more" open={advancedActive}>
          <summary className="disclosure-summary">
            <span className="disclosure-label">More filters</span>
          </summary>
          <div className="disclosure-body">
            <div className="filters filters-advanced">
              <label className="sr-only" htmlFor="anomaly-source">
                Log source
              </label>
              <select
                id="anomaly-source"
                name="log_source_id"
                defaultValue={values.log_source_id}
              >
                <option value="">All log sources</option>
                {sources.map((s) => (
                  <option key={s.log_source_id} value={s.log_source_id}>
                    {s.name}
                  </option>
                ))}
              </select>

              <label className="sr-only" htmlFor="anomaly-instance">
                QRadar instance
              </label>
              <input
                id="anomaly-instance"
                type="search"
                name="instance_id"
                defaultValue={values.instance_id}
                placeholder="QRadar instance…"
              />

              <label className="sr-only" htmlFor="anomaly-evidence">
                Evidence status
              </label>
              <select
                id="anomaly-evidence"
                name="evidence_status"
                defaultValue={values.evidence_status}
              >
                <option value="">All evidence states</option>
                {EVIDENCE.map((e) => (
                  <option key={e} value={e}>
                    {e}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </details>
      </form>

      {/* Chips render only when something is actually filtered. */}
      {activeChips.length > 0 && (
        <div className="row filter-chips">
          <span className="muted">Filtered by</span>
          {activeChips.map(([key, value]) => (
            <Link key={key} href={withoutFilter(values, key)} className="filter-chip">
              {chipLabel(key, value, sources)}
              <span aria-hidden="true"> ✕</span>
              <span className="sr-only">, remove this filter</span>
            </Link>
          ))}
          <Link href="/anomalies" className="filter-clear">
            Clear all
          </Link>
        </div>
      )}
    </>
  );
}
