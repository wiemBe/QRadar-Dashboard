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

  anomalies: (openOnly = false) =>
    request<Anomaly[]>(`/anomalies${openOnly ? "?open_only=true" : ""}`),

  alerts: (status?: string) =>
    request<Alert[]>(`/alerts${status ? `?status=${status}` : ""}`),
  alert: (id: string) => request<Alert>(`/alerts/${id}`),
  alertNotifications: (id: string) => request<Notification[]>(`/alerts/${id}/notifications`),
};

// Severity → CSS pill class, shared by Phase 2 views.
export function sevTone(sev: string): string {
  if (sev === "CRITICAL" || sev === "HIGH") return "crit";
  if (sev === "MEDIUM") return "warn";
  return "ok";
}
