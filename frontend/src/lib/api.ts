// Thin typed client for the qradar-observability backend.
//
// All requests go through the backend API. The browser holds no QRadar
// credentials and never contacts QRadar or the MCP service directly.

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

// --- types mirror the backend Pydantic response schemas -------------------
export interface SocOverview {
  instance_status: string;
  instance_version: string | null;
  generated_at: string;
  average_health_score: number | null;
  log_sources: {
    total_log_sources: number;
    monitored_log_sources: number;
    healthy_log_sources: number;
    silent_log_sources: number;
    anomalous_log_sources: number;
    in_maintenance: number;
  };
  offenses: {
    active: number;
    critical: number;
    unassigned: number;
    oldest_age_seconds: number | null;
  };
  alerts: { open: number; acknowledged: number; by_severity: Record<string, number> };
}

export interface LogSourceSummary {
  id: string;
  qradar_id: number;
  name: string;
  type_name: string | null;
  criticality: string;
  owner: string | null;
  enabled: boolean;
  monitoring_enabled: boolean;
  maintenance_mode: boolean;
  qradar_status: string | null;
  last_event_time: string | null;
  health_score: number | null;
  open_anomaly_count: number;
}

// --- Phase 2 types ---------------------------------------------------------
export interface ScheduledSearch {
  id: string;
  name: string;
  description: string | null;
  owner: string | null;
  category: string | null;
  mitre_techniques: string[];
  aql_query: string;
  query_version: number;
  schedule_cron: string;
  severity: string;
  threshold_value: number | null;
  threshold_operator: string;
  timeout_seconds: number;
  max_time_range_hours: number;
  max_result_rows: number;
  visualization_type: string;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  consecutive_failures: number;
}

export interface SearchExecution {
  id: string;
  search_id: string;
  query_version: number;
  status: string;
  trigger: string;
  triggered_by: string | null;
  ariel_search_id: string | null;
  ariel_status: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  result_count: number | null;
  truncated: boolean;
  error_type: string | null;
  error_message: string | null;
  retry_count: number;
  threshold_breached: boolean;
}

export interface SearchVersion {
  version: number;
  aql_query: string;
  changed_by: string | null;
  change_note: string | null;
  created_at: string;
}

export interface Anomaly {
  id: string;
  log_source_id: string;
  anomaly_type: string;
  severity: string;
  detected_at: string;
  resolved_at: string | null;
  observed_value: number | null;
  expected_value: number | null;
  deviation_score: number | null;
  explanation: string | null;
  details: Record<string, unknown>;
  suppressed: boolean;
}

export interface Alert {
  id: string;
  fingerprint: string;
  title: string;
  description: string | null;
  severity: string;
  status: string;
  source_type: string;
  opened_at: string;
  first_seen_at: string;
  last_seen_at: string | null;
  acknowledged_by: string | null;
  resolved_by: string | null;
  resolution_reason: string | null;
  occurrence_count: number;
  evidence_snapshot: Record<string, unknown>;
  source_anomaly_ids: string[];
}

// One stored aggregate joined to the execution that produced it. `query_version`
// is per point so the chart can mark where the AQL changed — results either side
// of a query change are not comparable.
export interface ResultMetricPoint {
  bucket_start: string;
  metric_key: string;
  value: number;
  dimensions: Record<string, unknown>;
  execution_id: string;
  execution_status: string;
  duration_ms: number | null;
  result_count: number | null;
  threshold_breached: boolean;
  query_version: number;
  query_version_id: string | null;
}

export interface SearchResultTrend {
  search_id: string;
  metric_key: string;
  threshold_value: number | null;
  threshold_operator: string;
  count: number;
  points: ResultMetricPoint[];
}

export interface Notification {
  id: string;
  alert_id: string;
  channel: string;
  target: string;
  transition: string;
  status: string;
  attempts: number;
  max_attempts: number;
  next_attempt_at: string | null;
  sent_at: string | null;
  error_message: string | null;
}

export const api = {
  overview: () => request<SocOverview>("/overview"),
  logSources: () => request<LogSourceSummary[]>("/log-sources"),
  syncLogSources: () => request<unknown>("/log-sources/sync", { method: "POST" }),

  searches: () => request<ScheduledSearch[]>("/searches"),
  search: (id: string) => request<ScheduledSearch>(`/searches/${id}`),
  searchVersions: (id: string) => request<SearchVersion[]>(`/searches/${id}/versions`),
  searchExecutions: (id: string) => request<SearchExecution[]>(`/searches/${id}/executions`),
  searchResults: (id: string, metricKey = "total", limit = 500) =>
    request<SearchResultTrend>(
      `/searches/${id}/results?metric_key=${encodeURIComponent(metricKey)}&limit=${limit}`,
    ),

  // --- operator actions ----------------------------------------------------
  // Mutations only. Authorization is the backend's job: these call the guarded
  // endpoints and surface the status, they never pre-judge permissions here.
  runSearch: (id: string) =>
    request<SearchExecution>(`/searches/${id}/run`, { method: "POST" }),

  anomalies: (openOnly = false) =>
    request<Anomaly[]>(`/anomalies${openOnly ? "?open_only=true" : ""}`),

  alerts: (status?: string) =>
    request<Alert[]>(`/alerts${status ? `?status=${status}` : ""}`),
  alert: (id: string) => request<Alert>(`/alerts/${id}`),
  alertNotifications: (id: string) => request<Notification[]>(`/alerts/${id}/notifications`),

  acknowledgeAlert: (id: string) =>
    request<Alert>(`/alerts/${id}/acknowledge`, { method: "POST" }),
  resolveAlert: (id: string, reason: string) =>
    request<Alert>(`/alerts/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
};

// Turns any thrown value into something safe to render.
//
// Never surfaces a raw exception: an unexpected error could carry a stack trace
// or an upstream URL, and this text goes straight into the DOM. Known HTTP
// statuses get an actionable message; everything else degrades to a fixed
// string.
export function actionErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.status) {
      case 401:
        return "Your session has expired. Sign in again to continue.";
      case 403:
        return "You do not have permission to perform this action.";
      case 404:
        return "This item no longer exists. Refresh the page.";
      case 409:
        return "This conflicts with the current state. Refresh and try again.";
      case 422:
        // The backend's validation detail is generated from the schema and is
        // safe to show; it tells the operator what to correct.
        return err.message || "The request was rejected as invalid.";
      default:
        return err.status >= 500
          ? "The server could not complete the request. Try again shortly."
          : err.message || "The request failed.";
    }
  }
  return "The request could not be sent. Check your connection and try again.";
}

// Severity → CSS pill class, shared by Phase 2 views.
export function sevTone(sev: string): string {
  if (sev === "CRITICAL" || sev === "HIGH") return "crit";
  if (sev === "MEDIUM") return "warn";
  return "ok";
}
