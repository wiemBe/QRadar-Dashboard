// Parsing the anomaly list's query string.
//
// Query parameters are user-controlled input arriving from a URL, so nothing
// here trusts them. Enum-valued filters are checked against an allow-list
// rather than forwarded, so a crafted value cannot be reflected back into the
// page or smuggled into the upstream request; timestamps are validated and the
// range is clamped before it reaches an endpoint that queries a hypertable.

export const PAGE_SIZE = 25;

/** Mirrors the backend's `api_max_range_days` guard, applied before the call
 *  rather than after, so an over-wide range is corrected instead of 422ing. */
export const MAX_RANGE_DAYS = 365;

const DETECTOR_TYPES = new Set(["VOLUME_SPIKE", "VOLUME_DROP", "NO_EVENTS"]);
const STATES = new Set([
  "INSUFFICIENT_DATA",
  "NORMAL",
  "CANDIDATE",
  "OPEN",
  "RECOVERING",
  "RESOLVED",
  "SUPPRESSED",
]);
const SEVERITIES = new Set(["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]);
const EVIDENCE = new Set([
  "NOT_REQUESTED",
  "PENDING",
  "COMPLETE",
  "PARTIAL",
  "UNAVAILABLE",
  "FAILED",
]);
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface AnomalyQuery {
  offset: number;
  log_source_id: string;
  instance_id: string;
  anomaly_type: string;
  state: string;
  severity: string;
  evidence_status: string;
  /** ISO-8601, or "" when unset. Sent to the API. */
  since: string;
  until: string;
  /** Set when a supplied range was corrected, so the page can say so. */
  rangeNote: string | null;
}

function pick(value: string | undefined, allowed: Set<string>): string {
  return value && allowed.has(value) ? value : "";
}

function uuid(value: string | undefined): string {
  return value && UUID_RE.test(value) ? value : "";
}

/** `datetime-local` value → ISO instant, or "" if it is not a real timestamp. */
function instant(value: string | undefined): string {
  if (!value) return "";
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? "" : new Date(ms).toISOString();
}

export function parseAnomalyQuery(
  params: Record<string, string | undefined>,
): AnomalyQuery {
  // Negative or non-numeric offsets become 0 rather than being forwarded; the
  // backend rejects them, but a rejected page is a worse answer than page one.
  const parsedOffset = Number(params.offset ?? 0);
  const offset =
    Number.isFinite(parsedOffset) && parsedOffset > 0
      ? Math.floor(parsedOffset)
      : 0;

  let since = instant(params.since);
  let until = instant(params.until);
  let rangeNote: string | null = null;

  // An inverted range is a mistake, not an attack, and swapping it is what the
  // operator meant. Sending it through would only produce a 422.
  if (since && until && Date.parse(since) > Date.parse(until)) {
    [since, until] = [until, since];
    rangeNote = "The start and end times were inverted and have been swapped.";
  }

  if (since && until) {
    const span = Date.parse(until) - Date.parse(since);
    const max = MAX_RANGE_DAYS * 86400_000;
    if (span > max) {
      since = new Date(Date.parse(until) - max).toISOString();
      rangeNote = `The requested range exceeded ${MAX_RANGE_DAYS} days and was clamped.`;
    }
  }

  return {
    offset,
    log_source_id: uuid(params.log_source_id),
    instance_id: uuid(params.instance_id),
    anomaly_type: pick(params.anomaly_type, DETECTOR_TYPES),
    state: pick(params.state, STATES),
    severity: pick(params.severity, SEVERITIES),
    evidence_status: pick(params.evidence_status, EVIDENCE),
    since,
    until,
    rangeNote,
  };
}

/** The subset of a parsed query that is preserved across pagination links. */
export function paginationParams(
  q: AnomalyQuery,
): Record<string, string | undefined> {
  return {
    log_source_id: q.log_source_id || undefined,
    instance_id: q.instance_id || undefined,
    anomaly_type: q.anomaly_type || undefined,
    state: q.state || undefined,
    severity: q.severity || undefined,
    evidence_status: q.evidence_status || undefined,
    since: q.since || undefined,
    until: q.until || undefined,
  };
}
