# Phase 2 Development Handoff

**Written:** 2026-07-20
**Branch:** `master`
**Handoff commit:** `checkpoint: phase 2 session handoff` (see `git log -1`)
**Preceding commit:** `c8ee3f9` — `feat: complete phase 2 analytics and alerting`

This document is the complete state of the project at the end of the Phase 2 implementation
session. Everything below was verified against the code in this repository. Where something could
**not** be verified in this environment, it says so explicitly rather than claiming success.

---

## 1. Project purpose and architecture

An **on-premises QRadar observability and SOC analytics platform**. QRadar reports *offenses*; it is
poor at reporting when it has quietly stopped *seeing* — a log source silent since 02:00, a DSM
update that broke username parsing, a rule that has not fired in 90 days. Those failures produce no
events, so they are invisible. This platform watches for the **absence** of signal, scores log-source
health, and alerts **once** per condition without duplicate noise.

It is **strictly read-only** against QRadar.

```
browser ──▶ frontend (Next.js 15, TypeScript, ECharts; holds NO QRadar credentials)
               │ REST /api/v1
            backend (FastAPI + SQLAlchemy 2 + Pydantic 2; rate-limited, RBAC, audit log)
               ├── Celery worker + beat ─── Redis (broker/result backend)
               └── QRadarProvider ABC (app/providers/base.py)
                     ├── QRadarRestProvider  REST/Ariel — collection + AQL   (Phase 3)
                     ├── QRadarMCPProvider   GET-only inventory, future AI   (Phase 3)
                     └── MockQRadarProvider  dev/tests — IMPLEMENTED
            PostgreSQL + TimescaleDB (4 hypertables)
```

### Backend layout (`backend/app/`)

| Package | Responsibility |
|---|---|
| `api/` | FastAPI routers (`api/routes/`) + `deps.py` (SessionDep, ProviderDep) |
| `core/` | `config.py` (Settings), `database.py`, `logging.py`, `rate_limit.py` |
| `models/` | SQLAlchemy 2 models, one module per bounded context |
| `schemas/` | Pydantic 2 request/response models |
| `services/` | Domain logic — health scoring, inventory sync, AQL validation, search execution + scheduling |
| `providers/` | `QRadarProvider` ABC + REST / MCP / Mock + DTOs + factory |
| `collectors/` | `metric_collector.py` |
| `anomaly/` | `baseline.py`, `detectors.py`, `engine.py`, `statistics.py`, `thresholds.py`, `evidence.py` |
| `alerts/` | lifecycle `service.py`, `fingerprint.py`, `routing.py`, `dispatcher.py`, `search_alerts.py`, `notifiers/` |
| `workers/` | `celery_app.py` + `tasks.py` |
| `repositories/` | data-access helpers (`audit.py`, `log_source.py`) |
| `security/` | `auth.py`, `rbac.py`, `crypto.py`, `redaction.py`, `sanitizer.py` |

---

## 2. Pinned qradar-mcp commit and MCP security decisions

**`qradar-mcp/` is a git gitlink (submodule) pinned to commit
`b8f6a4a3fe901eac4f55e4ca5d146d952f55db51`.** Verified clean and unmodified this session
(`git ls-files -s qradar-mcp`). **It must never be modified.**

Before any integration code was written, all 73 tools at that commit were AST-parsed into
[`docs/mcp-capability-matrix.md`](mcp-capability-matrix.md), mapping every tool to its QRadar
endpoint, HTTP verb, read/write classification and suitability. Regenerate with:

```bash
python backend/scripts/extract_mcp_capabilities.py
```

That analysis drove these **non-negotiable** decisions:

- The pinned commit is literally *"enable-crud"* — the upstream server ships with **`POST` and
  `DELETE` enabled**, plus `verify_ssl: false` and `debug: true` defaults.
- QRadar uses `POST` (not `PUT`) for updates, so **`POST` must be treated as mutating**.
- Running AQL is itself a `POST` (`create_ariel_search`) — which independently confirms the rule
  that **AQL execution never goes through MCP**.

Enforcement is layered, not documentary:

1. `deploy/mcp/feature_toggles.json` is mounted to disable `POST`/`DELETE`.
2. `QRadarMCPProvider` enforces a **hardcoded read-only tool allowlist** independent of what the
   server advertises.
3. The QRadar service account is itself view-only.
4. The MCP container runs on an internal Docker network with **no published ports**, `read_only`,
   `cap_drop: ALL`, `no-new-privileges`, behind the `mcp` compose profile (off by default).
5. `QRadarMCPProvider` does not declare `ProviderCapability.AQL_EXECUTION`, so
   `SearchExecutor.execute()` refuses it with a `VALIDATION` error rather than falling back.

---

## 3. Phase 1 completed functionality

- Architecture, `docker-compose.yml` (postgres, redis, backend, migrate, celery-worker,
  celery-beat, frontend, qradar-mcp behind a profile), `Makefile`, CI.
- 18 SQLAlchemy models across `instance`, `log_source`, `search`, `offense`, `rule`,
  `config_change`, `alert`, `identity`, `monitoring`. Four TimescaleDB hypertables:
  `log_source_metric`, `search_result_metric`, `rule_metric`, `offense_snapshot`.
- Alembic migration `0001_initial_schema` materialising the schema from model metadata, then
  applying hypertables. Retention is **not** applied by the migration.
- `MockQRadarProvider` (deterministic, seeded).
- SOC Overview + Log Source inventory (API + frontend).
- Security: `EncryptedString` Fernet column type + key rotation, RBAC + audit log, output
  sanitization, structured JSON logging with redaction, rate limiting, production-hardening
  validators in `Settings`.
- `app/services/health_score.py` — pure function, 40% freshness + 25% volume + 20% parsing +
  15% collection.

---

## 4. Phase 2 requirements as originally requested

1. Scheduled searches
2. Metric collection
3. Baseline calculation
4. Anomaly detection
5. Alert lifecycle
6. Notification delivery

Constraints carried through the whole phase: keep qradar-mcp read-only and unmodified; do not
enable MCP `POST`/`DELETE`; keep AQL execution on the direct QRadar REST/Ariel provider path; do not
begin Phase 3 (offenses, MITRE coverage, LLM); do not weaken or delete existing tests.

---

## 5. Phase 2 functionality completed

Items 1–5 below existed before this session (written in the previous session) and were **verified by
reading the code** — they are not new work. Items 6–9 are **this session's work**.

| # | Capability | Where | Status |
|---|---|---|---|
| 1 | Metric collection | `app/collectors/metric_collector.py` | Complete. PG advisory lock (non-overlapping runs), UTC interval flooring, watermark + lag, bounded backfill, idempotent `ON CONFLICT` upsert. |
| 2 | Baselines | `app/anomaly/baseline.py` | Complete. median + MAD per (weekday, hour); excludes maintenance windows, off-business-hours intervals and known anomaly spans; `is_reliable` below `baseline_min_samples`; versioned cells. |
| 3 | Anomaly detection | `app/anomaly/detectors.py`, `engine.py` | Complete. All 9 `AnomalyType` members implemented; `NO_EVENTS` suppresses 5 redundant detectors; per-type hysteresis via `LogSourceDetectorState`; per-interval idempotency; maintenance suppression. |
| 4 | Alert lifecycle | `app/alerts/service.py` | Complete. `OPEN → ACKNOWLEDGED → RESOLVED`; fingerprint dedup plus `IntegrityError` reload against the partial unique index. |
| 5 | Notification delivery | `app/alerts/dispatcher.py`, `notifiers/` | Complete. Enqueue-per-route, dedup per (alert, transition, channel, target), retry with backoff+jitter, dead-letter. Teams / Slack / generic webhook / syslog / email notifiers. |
| 6 | **Scheduled-search dispatch** | `app/services/search_scheduler.py` **(new)** | **New this session.** See below. |
| 7 | **Search threshold + failure alerting** | `app/alerts/search_alerts.py` **(new)** | **New this session.** See below. |
| 8 | **Operator-transition notifications** | `app/api/routes/alerts.py` | **New this session.** ack/resolve now enqueue. |
| 9 | **Email routing** | `app/alerts/routing.py` | **New this session.** `NOTIFY_EMAIL_RECIPIENTS`. |

### 6. Scheduled-search dispatch — `app/services/search_scheduler.py`

Before this session, `app/workers/tasks.py::run_due_searches` was a self-described *"Placeholder
driver"* that counted enabled searches and returned. Consequently `ScheduledSearch.schedule_cron`,
`next_run_at`, `last_run_at`, `consecutive_failures` and
`ScheduledSearchService.scheduled_run_key()` were **all dead code**. The executor
(`SearchExecutor`) was complete; nothing ever called it on a schedule.

New public surface:

- `next_fire_time(cron, timezone, *, after, now) -> datetime` — cron expansion via APScheduler's
  `CronTrigger.from_crontab` (already a pinned dependency; **no new dependency added**). Returns UTC.
  Raises `InvalidCronExpression`.
- `class InvalidCronExpression(ValueError)`
- `@dataclass SchedulerReport` — `considered`, `dispatched`, `seeded`, `skipped_running`,
  `invalid_cron`.
- `class SearchScheduler` — `run_due(*, now=None) -> SchedulerReport`; private `_process`,
  `_dispatch`, `_has_run_in_flight`.

Behavioural policies (each covered by a test):

| Situation | Behaviour |
|---|---|
| Search seen for the first time | `next_run_at` is seeded; it does **not** run. Deploying 50 searches never fires 50 searches at once. |
| Worker was down for an hour | Overdue search runs **once**, then reschedules from *now*. Replaying every missed tick would stampede QRadar with already-stale queries. |
| Duplicate beat delivery | Same tick → same `run_key` → the `uq_search_execution_run_key` constraint reuses the row. No double-run. |
| A run is still in flight | Skipped, not queued. Past `2 × timeout_seconds` a run is presumed dead, so a crashed worker cannot silently retire a search forever. |
| Malformed cron | Logged, reported in `SchedulerReport.invalid_cron`, skipped. `next_run_at` preserved so fixing the expression resumes the schedule. One bad search never stops the cycle. |
| Many searches due at once | Bounded by `SEARCH_MAX_DISPATCH_PER_CYCLE` (default 10). |

### 7. Search alerting — `app/alerts/search_alerts.py`

Before this session, `search_threshold_fingerprint()` had **zero callers** and
`SearchExecution.threshold_breached` had **no consumer** — scheduled searches could not produce an
alert at all. Only anomalies could.

`class SearchAlertEvaluator` with `evaluate(search, execution) -> list[AlertResult]` applies two
independent conditions, each with its own fingerprint so they never deduplicate into one another:

- **`search_threshold`** — opens when a `COMPLETED` run has `threshold_breached`; resolves when a
  later run completes under the threshold. A **failed** run never resolves it (a transient error
  must not clear a real breach).
- **`search_failure`** — opens when `consecutive_failures >= SEARCH_FAILURE_ALERT_AFTER`; resolves
  on the next successful run. Severity fixed at `HIGH` (a detection not running is a control
  failure, not a finding). **Rationale:** a detection that has quietly stopped executing produces no
  results and therefore no threshold breach — without this condition, the exact failure mode this
  platform exists to catch was itself silent.

New: `search_failure_fingerprint()` in `app/alerts/fingerprint.py`.

### 8 & 9. Notification reachability

- `app/api/routes/alerts.py::_enqueue_transition()` — acknowledge/resolve now enqueue. Before this,
  `AlertTransition.ACKNOWLEDGED` was **unreachable in production** and a manual resolve went out
  silently despite `notify_send_recovery` defaulting to `true`. Enqueue-only: an HTTP request
  records intent and returns; it never blocks on an outbound webhook.
- `app/api/routes/searches.py` — the manual-run endpoint now runs `SearchAlertEvaluator` too, so a
  breach found by hand is not invisible to whoever is on call.
- `app/alerts/routing.py::default_policy()` — an `EmailNotifier` was always built by
  `build_notifiers()` but no `EMAIL` rule was ever created, making email **structurally
  undeliverable**. Now reads `NOTIFY_EMAIL_RECIPIENTS` (comma-separated), one route per recipient so
  one bad mailbox does not park delivery for the others.
- `app/alerts/dispatcher.py` — added `class AlertEnqueuer(Protocol)` so engines depend on the
  enqueue contract structurally, not on the concrete dispatcher.

---

## 6. Partially implemented

| Area | What works | What is missing |
|---|---|---|
| **Frontend Phase 2 pages** | All four render real backend data server-side: `searches/`, `searches/[id]/`, `anomalies/`, `alerts/`, `alerts/[id]/`. | **Entirely read-only.** There is no `"use client"` component anywhere in `frontend/src/`. No operator can acknowledge, resolve, or manually run anything from the UI. → **Remaining task 1.** |
| **Search result trend** | `SearchExecutor._store_metrics()` writes `SearchResultMetric` rows (a `total` plus per-dimension aggregates). | Those rows are **not exposed by any API endpoint** and no chart renders them. `frontend/src/app/searches/[id]/page.tsx:104-107` is a literal placeholder paragraph. `echarts@5.5.1` + `echarts-for-react@3.0.2` are in `package.json` but **never imported anywhere**. → **Remaining task 2.** |
| **`QRadarRestProvider` / `QRadarMCPProvider`** | Class skeletons, capability declarations, TLS refusal logic, MCP allowlist. | All data methods `raise NotImplementedError("... implemented in Phase 3")`. **This is correct and intentional** — Phase 3 scope. Phase 2 runs entirely on `MockQRadarProvider`. |
| **mypy** | Configured in `pyproject.toml`; new Phase 2 modules are clean. | 30 pre-existing errors across 17 Phase 1 files. CI runs it as `mypy app \|\| true` — advisory by design. |

---

## 7. The final two unfinished tasks

Both are frontend-facing. The backend for task 1 is **complete and tested**; task 2 needs a small
backend addition first.

### Task 1 — Operator action UI (alert acknowledge/resolve, manual search run)

**Why it matters:** `POST /api/v1/alerts/{id}/acknowledge`, `POST /api/v1/alerts/{id}/resolve` and
`POST /api/v1/searches/{id}/run` all exist, enforce RBAC, write audit rows and enqueue
notifications — and **nothing in the UI can call them.**

Exact steps:

1. **`frontend/src/lib/api.ts`** — add three methods next to the existing `syncLogSources` (which is
   itself defined but never called — wire it up while you are there):
   ```ts
   acknowledgeAlert: (id: string) =>
     request<Alert>(`/alerts/${id}/acknowledge`, { method: "POST" }),
   resolveAlert: (id: string, reason: string) =>
     request<Alert>(`/alerts/${id}/resolve`, {
       method: "POST", body: JSON.stringify({ reason }),
     }),
   runSearch: (id: string) =>
     request<SearchExecution>(`/searches/${id}/run`, { method: "POST" }),
   ```
2. **Add `next_run_at: string | null`** to the `ScheduledSearch` interface in `api.ts`. The backend
   `ScheduledSearchOut` schema (`app/schemas/search.py:108`) returns it and the scheduler now
   populates it, but the frontend type omits it.
3. **Create `frontend/src/components/AlertActions.tsx`** — a `"use client"` component taking
   `{ alertId, status }`. Renders Acknowledge (when `status === "OPEN"`) and Resolve (when status is
   not `RESOLVED`, with a reason input). On success call `router.refresh()` from
   `next/navigation`. Disable buttons while in flight; surface `ApiError.status === 403` as a
   permissions message rather than a generic failure.
4. **Create `frontend/src/components/RunSearchButton.tsx`** — same pattern for `runSearch`.
5. **Mount them:** `frontend/src/app/alerts/[id]/page.tsx` (below the Lifecycle card) and
   `frontend/src/app/searches/[id]/page.tsx` (next to the `<h2>`).
6. **Verify:** `cd frontend && npm install && npm run typecheck && npm run build`.

**Caution:** these are the first client components in the codebase. Keep data fetching in the server
components and pass only ids/primitives into the client boundary — do not convert the pages
themselves to `"use client"`.

### Task 2 — Search result-trend endpoint + ECharts visualisation

**Why it matters:** `visualization_type` is stored per search and `SearchResultMetric` rows
accumulate on every run, but there is no way to see a trend. `docs/` and the README both promise it.

Exact steps:

1. **Backend schema** — add to `backend/app/schemas/search.py`:
   ```python
   class ResultMetricPoint(BaseModel):
       model_config = ConfigDict(from_attributes=True)
       bucket_start: datetime
       metric_key: str
       value: float
       query_version: int   # from the joined SearchExecution
   ```
2. **Backend route** — add to `backend/app/api/routes/searches.py`:
   `GET /searches/{search_id}/results?metric_key=total&limit=500`, ordered by `bucket_start`.
   Select `SearchResultMetric` joined to `SearchExecution` to carry `query_version`. Return 404 if
   the search does not exist, matching the existing `list_versions` / `list_executions` pattern.
   **Do not add a new permission** — the other read routes on this router are unauthenticated reads.
3. **Frontend client** — `searchResults: (id, metricKey = "total") => request<ResultMetricPoint[]>(...)`
   plus the matching interface in `api.ts`.
4. **Chart component** — `frontend/src/components/ResultTrend.tsx`, `"use client"`, using
   `echarts-for-react` (already a dependency). **Annotate query-version boundaries** with a
   `markLine` where `query_version` changes: results either side of an AQL change are not
   comparable, and the UI must say so. This is a stated design requirement, not decoration.
5. **Replace the placeholder** at `frontend/src/app/searches/[id]/page.tsx:104-107`.
6. **Tests** — add to `backend/tests/integration/test_api.py` (or a new
   `test_search_results_api.py`): ordering, `metric_key` filtering, `limit` behaviour, 404 on
   unknown search. Follow the DB-gated `db_session` fixture pattern.
7. **Verify:** backend gates below, then `npm run typecheck && npm run build`.

---

## 8. Files created or modified, by component

### Created this session

| File | Component |
|---|---|
| `backend/app/services/search_scheduler.py` | Scheduled-search dispatch |
| `backend/app/alerts/search_alerts.py` | Search threshold + failure alerting |
| `backend/tests/unit/test_search_schedule.py` | 12 unit tests — cron expansion |
| `backend/tests/integration/test_search_scheduler.py` | 11 integration tests — dispatch policy |
| `backend/tests/integration/test_search_alerts.py` | 9 integration tests — search alerting |
| `backend/tests/integration/test_alert_api_notifications.py` | 4 integration tests — operator transitions |
| `docs/PHASE2-HANDOFF.md` | This document |
| `docs/NEXT-SESSION-PROMPT.md` | Fresh-session prompt |

### Modified this session

| File | Change |
|---|---|
| `backend/app/workers/tasks.py` | `run_due_searches` placeholder → real scheduler + alert wiring; imports |
| `backend/app/alerts/fingerprint.py` | + `search_failure_fingerprint()` |
| `backend/app/alerts/routing.py` | + `NOTIFY_EMAIL_RECIPIENTS` → EMAIL routes |
| `backend/app/alerts/dispatcher.py` | + `AlertEnqueuer` Protocol |
| `backend/app/api/routes/alerts.py` | + `_enqueue_transition()`, called from acknowledge + resolve |
| `backend/app/api/routes/searches.py` | manual run now evaluates search alerts |
| `backend/app/core/config.py` | + `search_max_dispatch_per_cycle`, `search_failure_alert_after` |
| `backend/tests/integration/factories.py` | `make_search()` accepts `schedule_cron` + `**kwargs` (additive; no existing call changed) |
| `backend/tests/unit/test_fingerprint.py` | +3 tests (additive) |
| `backend/tests/unit/test_routing.py` | +2 tests (additive) |
| `.env.example` | + scheduled-search driver block, + `NOTIFY_EMAIL_RECIPIENTS` |
| `README.md` | Phase 2 marked complete; dispatch-policy and alert-condition tables; corrected test counts and tooling note |

**No existing test was weakened, skipped or deleted.**

### Pre-existing Phase 2 files (previous session, unchanged)

`app/collectors/metric_collector.py`, `app/anomaly/{baseline,detectors,engine,evidence,statistics,thresholds}.py`,
`app/alerts/{service,dispatcher}.py`, `app/alerts/notifiers/{base,channels,registry}.py`,
`app/services/{search_executor,scheduled_search,aql_validator,ariel_errors,backoff,concurrency,timescale}.py`,
`app/api/routes/{searches,alerts,anomalies}.py`, `app/schemas/{search,alert}.py`.

---

## 9. Database migrations

| Revision | File | `down_revision` | Status |
|---|---|---|---|
| `0001` | `backend/alembic/versions/0001_initial_schema.py` | `None` | Head. Only migration in the repo. |

**No migration was added this session, and none was needed** — Phase 2 introduced **zero model
changes**. Confirmed: `git diff --cached --stat -- backend/app/models/` was empty.
`ScheduledSearch.next_run_at`, `last_run_at` and `consecutive_failures` already existed in the
Phase 1 schema; this session made them *used* rather than adding them.

The two new settings (`search_max_dispatch_per_cycle`, `search_failure_alert_after`) are
environment configuration, not columns.

> **Not verified in this environment:** `alembic upgrade head` and `alembic check` (migration-drift)
> could not be run — no PostgreSQL, Docker or Podman is available here. CI runs both
> (`.github/workflows/ci.yml`) and `alembic check` **fails the build** on model/migration drift.
> Since no model changed, drift is not expected.

---

## 10. API endpoints

All under `/api/v1`. Verified live against the generated OpenAPI document.

| Method | Path | Notes |
|---|---|---|
| GET | `/overview` | Phase 1 |
| GET | `/log-sources` | Phase 1 |
| GET | `/log-sources/{id}` | Phase 1 |
| POST | `/log-sources/sync` | Phase 1 |
| GET | `/health` | Phase 1 |
| GET | `/searches` | Phase 2 |
| POST | `/searches` | Phase 2 — requires `search:write` |
| GET | `/searches/{search_id}` | Phase 2 |
| PATCH | `/searches/{search_id}` | Phase 2 — requires `search:write`; AQL change mints a new version |
| GET | `/searches/{search_id}/versions` | Phase 2 |
| GET | `/searches/{search_id}/executions` | Phase 2 |
| POST | `/searches/{search_id}/run` | Phase 2 — requires `search:execute`; **modified this session** (now evaluates alerts) |
| GET | `/anomalies` | Phase 2 — `log_source_id`, `open_only`, `limit` filters |
| GET | `/alerts` | Phase 2 — `status`, `limit` filters |
| GET | `/alerts/{alert_id}` | Phase 2 |
| GET | `/alerts/{alert_id}/notifications` | Phase 2 |
| POST | `/alerts/{alert_id}/acknowledge` | Phase 2 — requires `alert:ack`; **modified this session** (now enqueues) |
| POST | `/alerts/{alert_id}/resolve` | Phase 2 — requires `alert:resolve`; **modified this session** (now enqueues) |

**No new endpoint was added this session.** Task 2 above adds `GET /searches/{id}/results`.

RBAC constants (`app/security/rbac.py`): `search:execute`, `search:write`, `alert:ack`,
`alert:resolve`, `admin:*`.

---

## 11. Celery tasks, scheduled jobs and distributed locks

Registered task names (verified by importing `celery_app` and inspecting `celery_app.tasks`):

| Task | Async orchestration | Beat schedule |
|---|---|---|
| `collect_metrics` | `tasks.collect_metrics()` | `COLLECTION_INTERVAL_SECONDS` (default 300s) |
| `evaluate_anomalies` | `tasks.evaluate_anomalies()` | `COLLECTION_INTERVAL_SECONDS` (runs just after collection) |
| `run_due_searches` | `tasks.run_due_searches()` | 60s — **rewritten this session** |
| `dispatch_notifications` | `tasks.dispatch_notifications()` | 30s |
| `rebuild_baselines` | `tasks.rebuild_baselines()` | 86400s (daily; baselining is expensive) |

Each Celery task is a thin sync shim (`_run(coro)` → `asyncio.run`) around an importable `async`
function, so the same orchestration runs unchanged under the **APScheduler MVP fallback** and is
directly awaitable in tests. Config: `task_acks_late=True`, `worker_prefetch_multiplier=1`,
`timezone="UTC"`.

### Distributed locks

**Exactly one exists:** `_AdvisoryLock` in `app/collectors/metric_collector.py` — a **non-blocking**
session-level PostgreSQL advisory lock, `pg_try_advisory_lock(namespace, key)`, with
`namespace = COLLECTION_ADVISORY_LOCK_NAMESPACE` (default `4711`) and `key` = low 31 bits of the
instance UUID. A second worker returns `skipped_locked=True` rather than queueing. On non-PostgreSQL
binds it degrades to always-acquired.

**No lock was added this session.** See technical debt item TD-1 below — `SearchScheduler` does
**not** take one.

---

## 12. Frontend pages

`frontend/src/lib/sections.ts` declares 12 sections. Actual state:

| Route | Phase | State |
|---|---|---|
| `/` (SOC Overview) | 1 | Complete, live data |
| `/log-sources` | 1 | Complete, live data |
| `/log-sources/[id]` | 1 | Complete, live data |
| `/searches` | 2 | Complete (read-only list) |
| `/searches/[id]` | 2 | **Partial** — execution history + version history render; result-trend chart is a placeholder paragraph; no manual-run control |
| `/anomalies` | 2 | Complete (read-only list) |
| `/alerts` | 2 | Complete (read-only list) |
| `/alerts/[id]` | 2 | **Partial** — evidence, lifecycle and full notification-delivery history render; no acknowledge/resolve controls |
| `/offenses` | 3 | Intentional scaffold — "Planned for Phase 3" |
| `/rules` | 3 | Intentional scaffold |
| `/coverage` | 3 | Intentional scaffold |
| `/config-changes` | 3 | Intentional scaffold |
| `/admin` | 4 | Intentional scaffold |

Shared: `components/StatCard.tsx`, `components/Evidence.tsx`, `lib/api.ts`, `app/globals.css`.

**Every page is a React Server Component.** `grep -rln "use client" frontend/src/` returns nothing.

---

## 13. Tests added this session

| File | Count | Kind | Covers |
|---|---|---|---|
| `tests/unit/test_search_schedule.py` | 12 | unit | Cron seeding, strictly-after advance, backlog reschedule, hourly/daily, non-UTC timezone → UTC, tz-awareness, 4 malformed-cron cases, unknown timezone |
| `tests/integration/test_search_scheduler.py` | 11 | integration | Seed-without-running, due dispatch + schedule advance, not-yet-due, disabled search, backlog coalescing, duplicate-cycle no-double-run, in-flight blocking, stale in-flight recovery, invalid cron isolation, per-cycle bound, failure-streak tracking + reset |
| `tests/integration/test_search_alerts.py` | 9 | integration | Threshold open + notify, repeated breach → one alert one page, recovery resolve, no-threshold no-alert, failed run does not resolve a breach, failures below limit, failure alert, success resolves failure alert, both conditions coexist |
| `tests/integration/test_alert_api_notifications.py` | 4 | integration | Acknowledge enqueues, resolve enqueues recovery, repeated acknowledge does not double-enqueue, unconfigured deployment enqueues nothing |
| `tests/unit/test_fingerprint.py` | +3 | unit | `search_failure` prefix, no collision with `search_threshold`, stability + source scoping |
| `tests/unit/test_routing.py` | +2 | unit | Each configured email recipient routed; blank config ignored |

**Total added: 41 tests** (17 unit, 24 integration).

Full per-file inventory is in §14.

---

## 14. Current test, lint and type-check results

All commands run from `backend/` on 2026-07-20. **These are actual recorded outputs.**

```
$ python3 -m ruff check app tests
All checks passed!                                            # exit 0

$ python3 -m mypy app
Found 30 errors in 17 files (checked 83 source files)         # advisory — see TD-2

$ python3 -m pytest -m "not integration"
124 passed, 76 deselected, 2 warnings in 2.01s

$ python3 -m pytest
125 passed, 75 skipped, 2 warnings in 2.12s

$ python3 -m pytest -m integration
1 passed, 75 skipped, 124 deselected, 2 warnings in 1.67s
```

**0 failures, 0 errors.**

Test inventory (200 collected):

```
integration/test_alert_api_notifications.py   4     unit/test_aql_validator.py        18
integration/test_alert_lifecycle.py           4     unit/test_backoff_and_errors.py   13
integration/test_anomaly_engine.py            6     unit/test_concurrency.py           3
integration/test_api.py                       3     unit/test_config_and_crypto.py    10
integration/test_baseline.py                  6     unit/test_crypto_rotation.py       6
integration/test_db_constraints.py            5     unit/test_fingerprint.py           9
integration/test_inventory_sync.py            3     unit/test_health_score.py          8
integration/test_metric_collection.py         5     unit/test_mock_provider.py         8
integration/test_migrations.py                5     unit/test_notifiers.py             6
integration/test_notification_dispatch.py     6     unit/test_providers.py             7
integration/test_permissions.py               4     unit/test_routing.py               7
integration/test_search_alerts.py             9     unit/test_sanitizer.py             6
integration/test_search_executor.py           7     unit/test_search_schedule.py      12
integration/test_search_scheduler.py         11     unit/test_statistics.py            9
```

### What could NOT be verified here — read this

- **The 75 integration tests were never executed against a database.** This environment has no
  `docker`, no `podman`, and no local PostgreSQL. They were confirmed to *collect* and to *skip
  cleanly* when `TEST_DATABASE_URL` is unset — that is all. **The 24 integration tests added this
  session have never actually run.** CI will be their first real execution. Treat any failure there
  as expected-normal new-test debugging, not as a regression.
  (The single integration-marked test that passes without a DB is
  `test_metric_collection.py::test_floor_to_interval_is_utc_and_deterministic`, a pure function.)
- **`alembic upgrade head` / `alembic check` were not run** — no database. See §9.
- **Frontend was not type-checked or built** — `npm` is not installed in this environment. No
  frontend file was modified this session, so no frontend regression was introduced.
- **`ruff format` was deliberately not run.** It is **not part of this project's toolchain**:
  `.github/workflows/ci.yml` and the `Makefile` both gate on `ruff check` only. Running it would
  restyle **41 pre-existing files**, fighting the codebase's consistent compact hand-formatted
  style. Adopting it is a repo-wide decision, not something to slip into a feature commit.

---

## 15. Known errors, failing tests, TODOs and technical debt

**There are no failing tests and no known runtime errors.** The items below are debt, not breakage.

- **TD-1 — `SearchScheduler` takes no advisory lock.** `MetricCollector` guards against overlapping
  runs with a PG advisory lock; the scheduler does not. Two concurrent Celery beat instances could
  select the same tick, and the second `INSERT` would hit `uq_search_execution_run_key`, raising
  `IntegrityError` and poisoning the session mid-cycle. **The data invariant is safe** — the unique
  constraint still makes a duplicate `SearchExecution` impossible; the failure mode is an aborted
  cycle, not double execution. Single-instance beat is the documented topology. *Not fixed because
  it could not be integration-tested in this environment.* Suggested fix: reuse the `_AdvisoryLock`
  pattern with a distinct namespace, or catch `IntegrityError` per-search in `_dispatch` and roll
  back to a savepoint. **Phase 4 hardening.**
- **TD-2 — 30 mypy errors across 17 Phase 1 files.** Pre-existing; measured against the staged tree
  before any change this session and **unchanged after** (baseline 30 in 17 files / 81 sources →
  30 in 17 files / 83 sources). The two new modules are mypy-clean. Dominant patterns:
  `record_audit(object_id=UUID)` vs `str | None` (5×), untyped `clock=None` parameters,
  `Repository.list` shadowing the `list` type in annotations. CI runs `mypy app || true`.
- **TD-3 — `execution_instance_key()`** in `app/services/search_executor.py:304` always returns
  `"default"`; searches are not yet instance-scoped. Per-instance Ariel concurrency caps therefore
  collapse to a single global bucket. Explicitly deferred to Phase 3 (comment in place).
- **TD-4 — `syncLogSources()`** is defined in `frontend/src/lib/api.ts:173` and never called from
  any page. Fold into remaining task 1.
- **TD-5 — `ScheduledSearch` TS interface** omits `next_run_at`, which the backend now returns and
  populates. Fold into remaining task 1.
- **TD-6 — `default_policy()`** reads channel targets from `os.environ` directly rather than from
  `Settings`, unlike every other configuration consumer. Pre-existing; left alone for consistency
  with its established pattern. Worth unifying in Phase 4.
- **TD-7 — Two deprecation warnings** in the test run (starlette `TestClient`/httpx;
  `pythonjsonlogger.jsonlogger` moved). Harmless, upstream.

The only `NotImplementedError`s in the codebase are the intentional Phase 3 provider stubs in
`app/providers/qradar_rest.py` and `app/providers/qradar_mcp.py`, and the abstract methods in
`app/providers/base.py`. **There are no `TODO` or `FIXME` markers in the source.**

---

## 16. Design decisions that must not be changed

1. **Read-only against QRadar, always.** The provider interface has no create/update/delete method.
2. **AQL execution never goes through MCP.** Running AQL is a `POST` (`create_ariel_search`). It
   stays on the direct REST/Ariel path. `QRadarMCPProvider` does not declare
   `ProviderCapability.AQL_EXECUTION`.
3. **Never enable MCP `POST`/`DELETE`,** and never modify the pinned `qradar-mcp/` gitlink.
4. **Capability, not type.** Code asks `provider.supports(ProviderCapability.X)` / `.require(...)`.
   Never branch on `isinstance`.
5. **Never persist raw events.** Only aggregates (`SearchResultMetric`, `LogSourceMetric`). Raw
   events duplicate the SIEM's storage, multiply breach blast radius, and drag regulated data into a
   system with a different retention policy.
6. **Frontend-supplied AQL is never executed.** Only a stored, validated, versioned
   `ScheduledSearch.aql_query` runs — and `SearchExecutor._run_once` re-validates defensively.
7. **Two-layer dedup.** Application-layer fingerprint lookup is primary; the partial unique index
   `alert(dedup_key) WHERE status <> 'RESOLVED'` is the final concurrency safeguard. Do not remove
   either.
8. **An update to an already-open alert has `transition=None` and must not notify.** This is *the*
   anti-noise guarantee.
9. **Enqueue is separate from delivery.** HTTP requests and detection engines record intent; only
   the `dispatch_notifications` worker performs I/O. Never send inline from a request.
10. **Idempotency keys everywhere.** `run_key` per logical search run (retries reuse the row);
    `(log_source_id, bucket_start)` upsert for metrics; `last_interval_start` for anomaly
    evaluation.
11. **Hysteresis before alerting.** Open only after N consecutive anomalous intervals, resolve only
    after M healthy ones. Prevents flapping.
12. **`NO_EVENTS` suppresses its 5 dependent detectors** — a source sending nothing trivially
    satisfies every other failure condition and would otherwise emit five alerts.
13. **Baselines are median + MAD per (weekday, hour)** — robust to incident spikes, no ML. Cells
    below `BASELINE_MIN_SAMPLES` are stored but marked `is_reliable=False` and must not drive alerts.
14. **Baselines exclude maintenance windows, off-hours intervals and known anomaly spans** — an
    active incident must not poison the baseline it will later be judged against.
15. **Retention is opt-in and never applied by a migration.** All retention settings default to
    `None` (disabled). Applied only by `app/services/timescale.py`.
16. **Scheduler policies** (§5.6): seed-don't-run, coalesce-don't-replay, refuse overlap, isolate bad
    cron, bound the cycle. Each exists to stop the scheduler becoming an incident.
17. **`search_threshold` and `search_failure` are separate fingerprints.** Different problems,
    different owners; they must never deduplicate into one another.
18. **A failed run never resolves a threshold alert.** A transient error says nothing about the
    threshold.
19. **Secrets never reach the browser or an LLM.** QRadar tokens are Fernet-encrypted at rest and
    never returned by any API response.
20. **Production hardening is enforced at startup,** not documented and hoped for — `Settings`
    raises if `DEBUG` is on, TLS is off, the provider is `mock`, a REST token is missing, or
    autonomous LLM actions are enabled in production.
21. **The LLM performs no autonomous actions**, enforced by the read-only architecture rather than
    by prompt. Phase 3+.

---

## 17. Required environment variables

Full annotated list in [`.env.example`](../.env.example). Copy it to `.env` before starting.

### Mandatory — the app will not start correctly without these

| Variable | Notes |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `QRADAR_PROVIDER` | `mock` \| `rest` \| `mcp`. Leave `mock` for local work. `Settings` **refuses to boot** on `mock` in production. |

### Real QRadar (Phase 3; `QRADAR_PROVIDER=rest`)

`QRADAR_HOST` (must be `https://`), `QRADAR_SEC_TOKEN` (read-only authorized service token),
`QRADAR_VERIFY_SSL=true`, `QRADAR_CA_BUNDLE` (if internal CA).

### Infrastructure

`POSTGRES_HOST/PORT/USER/PASSWORD/DB`, `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`,
`NEXT_PUBLIC_API_BASE_URL` (frontend).

### Phase 2 tuning (all have working defaults)

```ini
COLLECTION_INTERVAL_SECONDS=300     COLLECTION_MAX_BACKFILL_INTERVALS=12
BASELINE_MIN_SAMPLES=8              BASELINE_LOOKBACK_DAYS=28
ANOMALY_OPEN_AFTER_INTERVALS=2      ANOMALY_RESOLVE_AFTER_INTERVALS=3
ANOMALY_DEVIATION_THRESHOLD=3.5
ARIEL_MAX_CONCURRENT_SEARCHES=3     ARIEL_GLOBAL_MAX_CONCURRENT_SEARCHES=6
ARIEL_MAX_TIMEOUT_SECONDS=900       ARIEL_MAX_RETRIES=3
SEARCH_MAX_DISPATCH_PER_CYCLE=10    # added this session
SEARCH_FAILURE_ALERT_AFTER=3        # added this session
NOTIFY_MAX_RETRIES=5                NOTIFY_SEND_RECOVERY=true
```

### Notification channels — all optional; blank disables that channel

`NOTIFY_TEAMS_WEBHOOK_URL`, `NOTIFY_SLACK_WEBHOOK_URL`, `NOTIFY_GENERIC_WEBHOOK_URL`,
`NOTIFY_SYSLOG_HOST` / `NOTIFY_SYSLOG_PORT`, **`NOTIFY_EMAIL_RECIPIENTS`** (comma-separated; added
this session) plus `SMTP_HOST/PORT/USERNAME/PASSWORD/FROM/USE_TLS`.

### Testing

`TEST_DATABASE_URL` — when unset, all integration tests skip cleanly.

---

## 18. Commands

### Start (mock provider — no QRadar required)

```bash
cp .env.example .env
python -c "import secrets; print('SECRET_KEY='+secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY='+Fernet.generate_key().decode())"
# paste both into .env; leave QRADAR_PROVIDER=mock

docker compose up -d postgres redis
docker compose run --rm migrate                                 # alembic upgrade head
docker compose run --rm backend python -m app.services.seed     # roles + inventory
docker compose up -d backend frontend celery-worker celery-beat
```

Frontend on <http://localhost:3000>, API on <http://localhost:8000/api/v1>,
OpenAPI at <http://localhost:8000/docs>.

### Backend without Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export QRADAR_PROVIDER=mock
uvicorn app.main:app --reload
```

### Full test suite

```bash
# Unit only — runs anywhere, no database
make test

# Everything, including the 75 DB-gated integration tests
make db-test-up          # ephemeral timescale/timescaledb:2.17.2-pg16 on port 5433
make test-integration

# Migration drift (fails on model/migration disagreement)
make check

# Lint
make lint                # ruff check app tests

# Type check (advisory)
cd backend && mypy app

# Frontend
cd frontend && npm install && npm run typecheck && npm run build
```

Raw equivalents:

```bash
cd backend
ENCRYPTION_KEY=sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A= pytest -q -m "not integration"
ENCRYPTION_KEY=... TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_test pytest -q
```

---

## 19. Recommended order for the next session

1. **Establish a real database first.** `make db-test-up`, then `make test-integration`. This is the
   single highest-value first step: **75 integration tests, 24 of them new, have never run.** Fix
   anything that surfaces before writing new code. Also run `make check` for migration drift.
2. **Verify the frontend toolchain** — `cd frontend && npm install && npm run typecheck && npm run
   build` — so you have a known-good baseline before adding the first client components.
3. **Remaining task 1** (operator action UI). Backend is done and tested; this is pure frontend.
   Do it first because it is lower risk and establishes the `"use client"` pattern task 2 reuses.
4. **Remaining task 2** (result-trend endpoint + ECharts). Backend schema + route + tests first,
   then the chart component.
5. **Re-run every gate**: `make lint`, `mypy app`, `make test-integration`, `npm run typecheck`,
   `npm run build`.
6. **Update `README.md`** with final test counts and drop the "Result-trend charts render here"
   language once real.
7. **Commit** as `feat: complete phase 2 frontend actions and result trends`.
8. **Stop.** Do not start Phase 3.

Optional if time allows, in priority order: TD-1 (scheduler advisory lock), TD-4/TD-5 (small
frontend cleanups), TD-2 (chip away at mypy debt).

---

## 20. Acceptance criteria for completing Phase 2

Phase 2 is done when **all** of the following hold:

- [ ] `make test-integration` passes against a real PostgreSQL + TimescaleDB — **all 200 tests**,
      0 failures, 0 errors. (Currently: 125 pass, 75 skip for lack of a database.)
- [ ] `make check` (`alembic upgrade head && alembic check`) reports **no drift**.
- [ ] `make lint` → `All checks passed!`
- [ ] `mypy app` introduces **no new errors** beyond the 30-error Phase 1 baseline.
- [ ] `cd frontend && npm run typecheck && npm run build` both succeed.
- [ ] An operator can **acknowledge and resolve an alert from `/alerts/[id]`**, and the resulting
      `AlertNotification` row is visible in that page's delivery history.
- [ ] An operator can **trigger a manual run from `/searches/[id]`** and see the new execution
      appear in the history.
- [ ] `/searches/[id]` renders a **result-trend chart** from `SearchResultMetric` data, with
      **query-version boundaries annotated**.
- [ ] `GET /searches/{id}/results` is implemented, returns ordered points, and has integration
      tests.
- [ ] A 403 from a permission-guarded action surfaces as a clear message in the UI, not a silent
      failure.
- [ ] `README.md` Phase 2 section and test counts match reality.
- [ ] `qradar-mcp/` gitlink still reads `b8f6a4a3fe901eac4f55e4ca5d146d952f55db51`, unmodified.
- [ ] No Phase 3 code exists — no offense analysis, no MITRE coverage, no LLM implementation, and
      `QRadarRestProvider` / `QRadarMCPProvider` data methods still raise `NotImplementedError`.
- [ ] No existing test was weakened, skipped or deleted.
