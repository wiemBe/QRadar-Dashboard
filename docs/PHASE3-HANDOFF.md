# Phase 3 Development Handoff

**Status: VERTICAL SLICE WORKING against a live QRadar 7.6.0 FP1 lab.** Collection,
scheduling, APIs and the offense/rule/coverage frontend all run end to end on real data.
Remaining: Compose stack verification, and the analytics depth Phase 3 originally scoped.

> **Read [§11](#11-live-qradar-session) first** if you are picking this up. It records what
> was proven against a real appliance, and three findings that will cost you a day each if
> you rediscover them.

This session ran out of budget partway through Phase 3. This document records exactly
what was built, what was verified and how, and what the next session must do. Nothing
here is aspirational: if a thing is not listed as verified, it was not verified.

Read this together with [PHASE2-HANDOFF.md](PHASE2-HANDOFF.md), which remains accurate
for everything Phase 3 did not touch.

---

## 1. Session summary

Three commits landed before this one:

| Commit | What |
|---|---|
| `7104187` | `chore(security)` — Next.js 15.1.3 → 15.5.20, React 19.0.0 → 19.2.7 |
| `5dc0c34` | `fix` — restored the missing `.gitmodules` for `qradar-mcp` |
| *(this one)* | `wip` — Phase 3 backend: providers, collectors, evaluators, API |

The Phase 3 feature commit named in the original brief
(`feat: implement QRadar offenses rule health and detection coverage`) has **not** been
made, because Phase 3 is not complete. This is committed as WIP so no work is lost.

---

## 2. The Next.js security upgrade

### Versions

| Package | Before | After | Pinned |
|---|---|---|---|
| `next` | 15.1.3 | **15.5.20** | exact |
| `react` | 19.0.0 | **19.2.7** | exact |
| `react-dom` | 19.0.0 | **19.2.7** | exact |

Confirmed with `npm ls next react react-dom` — all three dedupe to a single copy.

### Status of the announced 20 July 2026 release — read this before deploying

> Next.js was upgraded to the latest stable 15.5.x patch available at implementation
> time. The announced July 20, 2026 scheduled security release had not yet been
> published. The dependency must be rechecked before production deployment.

Evidence gathered on 2026-07-20:

- `npm view next dist-tags` → `latest: 16.2.10`, `backport: 15.5.20`, `canary: 16.3.0-canary.90`
- `npm view next time` → the newest **stable** release on *either* the 15.5 or 16.2 line
  is dated **2026-07-01** (15.5.20 and 16.2.10). Nothing stable has shipped since.
- The only artifact published on 2026-07-20 is `16.3.0-canary.90`, which the brief
  forbids.
- The GitHub Advisory DB's newest `next` advisory is dated **2026-05-11**
  (GHSA-26hh-7cqf-hhc6 / CVE-2026-45109), first patched in 15.5.18 / 16.2.6. Every
  published advisory is therefore already fixed at or below 15.5.20.

**15.5.20 predates the announced release by 19 days. If that announcement is real,
this dependency is by definition still exposed to whatever it fixes.**

### CVE-2025-66478 is a rejected identifier

NVD reports `CVE-2025-66478` with `vulnStatus: "Rejected"` and the reason
*"This CVE is a duplicate of CVE-2025-55182."* There is no GitHub advisory under that
ID. The authoritative identifier for the React Server Components RCE is:

- **CVE-2025-55182 / GHSA-fv66-9v8q-g76r** (critical)
- Fixed in `react-server-dom-{webpack,turbopack,parcel}` at 19.0.1 / 19.1.2 / 19.2.1
- React 19.2.7 is above that patch level.

Track the RSC vulnerability under CVE-2025-55182. Do not use CVE-2025-66478 in reports.

### npm audit result

`npm audit --omit=dev` → **4 moderate, 0 high, 0 critical.** The critical Next.js RCE
present on 15.1.3 is cleared. No suppressions, no `--force`, no audit exceptions added.

Remaining production findings, both pre-existing and both accepted for this phase:

| Advisory | Package | Severity | Why it remains |
|---|---|---|---|
| GHSA-qx2v-qp2m-jg93 | `postcss <8.5.10` | moderate | Pinned transitively by `next@15.5.20` (`postcss: 8.4.31`). Cannot be moved without overriding what Next ships. |
| GHSA-fgmj-fm8m-jvvx | `echarts <6.1.0` | moderate | XSS in ECharts. Fix requires the echarts 6 major, which is a breaking change outside Phase 3 scope. **Mitigation:** all QRadar-sourced text is sanitized server-side (`app/security/sanitizer.py`) before it can reach a chart option, and no chart uses an HTML-rendering formatter. Revisit in Phase 4. |

The full `npm audit` (including dev) reports 1 high + 1 critical, both in the
`vite`/`vitest` chain. They require a local dev or UI server to be listening and are
**not present in the deployed production build**, which runs `next start` against a
prebuilt bundle.

### Deployment gate (must be automated in Phase 4)

Before any production deploy, re-run and record:

```bash
npm view next dist-tags --json
npm view next@15.5 version --json
npm audit --omit=dev
npm ls next react react-dom
```

If a stable 15.5.x newer than 15.5.20 exists, upgrade to it, re-run the full backend and
frontend regression gates, and record the exact version and audit result here.

---

## 3. The qradar-mcp submodule fix

`qradar-mcp/` was committed as a gitlink (mode `160000`) but the repository had **no
`.gitmodules`**, so `git submodule status` failed outright and a fresh clone could not
populate the directory — which meant `docker compose build qradar-mcp` was broken for
anyone but the original author.

Restored the mapping (`path = qradar-mcp`, `url = https://github.com/IBM/qradar-mcp.git`,
matching the remote already configured in the existing checkout).

The pin is **unchanged**: `b8f6a4a3fe901eac4f55e4ca5d146d952f55db51`.
`git diff --submodule` reports no change; no file inside `qradar-mcp/` was modified; no
MCP write operation was enabled. Fresh-clone instructions were added to the README.

---

## 4. Toolchain notes for the next session

Two environment facts cost time this session; they are recorded so they do not again.

1. **Node is not installed on the host.** Installed userspace to `~/.local/node`
   (v22.23.1 / npm 10.9.8). Prefix commands with:
   ```bash
   export PATH="$HOME/.local/node/bin:$PATH"
   ```
2. **SELinux blocks Docker bind mounts** of the repo (`ls: can't open '/app'`). Per
   project policy this was *not* worked around with `:z`/`:Z` or `label:disable`. Use
   the userspace node above, or `docker cp` into a container.

Backend venv is at `backend/.venv`. Tests need:
```bash
export ENCRYPTION_KEY=sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A=
export TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_test
```

---

## 5. A landmine in migration 0001 — understand this before adding a migration

`0001_initial_schema.py` builds the schema with `Base.metadata.create_all()`, i.e. from
**live model metadata**, not a frozen snapshot. Adding a model therefore *changes what
0001 creates*. On an empty database 0001 now creates the Phase 3 tables too; on an
existing Phase 2 database it did not.

0001 is committed and must not be edited, so **`0002` is written defensively**: every
`CREATE`/`ADD` is guarded by an inspector check and is a no-op when the object already
exists, so the schema converges from either starting point.

`NOT NULL` columns carry a `server_default` only to backfill existing rows; it is
dropped immediately afterwards, because the models declare a client-side `default=` only
and leaving it would make a Phase-2-upgraded database differ from a fresh one.

**Any future migration must follow the same pattern until 0001 is replaced with a frozen
DDL baseline.** Freezing 0001 is a strong Phase 4 candidate.

### Migration verification actually performed

| Path | Result |
|---|---|
| Fresh DB → `upgrade head` → `alembic check` | ✅ "No new upgrade operations detected" |
| Fresh DB → `downgrade 0001` → `upgrade head` → `check` | ✅ clean round trip |
| Genuine Phase 2 DB (built from a `630825e` worktree) → `upgrade head` → `check` | ✅ clean |
| Phase 2 path: offense_snapshot columns | 24 → 33 |
| Phase 2 path: new Phase 3 tables | 0 → 5 |
| Hypertables after upgrade | 6 (adds `rule_health_snapshot`, `detection_coverage_snapshot`) |
| Leftover server defaults on added columns | none (only `created_at`/`updated_at`, as intended) |

---

## 6. What was built

### 6.1 Providers

**`app/providers/qradar_rest.py`** — full production implementation.

Implemented: `validate_connection`, `get_instance_info`, `list_log_sources`,
`get_log_source`, `list_log_source_types`, `list_rules`, `list_building_blocks`,
`get_rule`, `list_offenses` (with `updated_since` server-side filter),
`get_offense`, `list_offense_types`, `list_offense_closing_reasons`,
`create_ariel_search`, `get_ariel_search_status`, `get_ariel_search_results`,
`cancel_ariel_search`.

Security/robustness properties:
- `verify_ssl=False` raises at construction; non-`https://` base URL raises.
- Internal CA bundle supported via `ca_bundle`.
- Bounded connect *and* read timeouts (`httpx.Timeout`).
- Retries with full-jitter exponential backoff, **idempotent verbs only** — the Ariel
  create POST is attempted exactly once, because a replayed create strands an untracked
  search on the appliance.
- `Retry-After` honoured on 429, clamped to the configured max.
- `Range: items=start-end` pagination, bounded by `max_pages` *and* a short-page exit.
- Upstream error text is **never forwarded**: `_sanitize_upstream_error()` rebuilds
  messages from the status code alone.
- The SEC token is set once as a client header — never logged, never in an exception.
- Malformed rows are dropped with a warning rather than aborting a batch; only a small
  descriptor subset of `/system/about` is retained, never the full payload.

**`app/providers/qradar_mcp.py`** — full read-only implementation.

- `ALLOWED_TOOLS`: 19 read tools. `KNOWN_WRITE_TOOLS`: **25** (17 POST + 8 DELETE),
  enumerated from the capability matrix. Verified programmatically: the two sets do not
  intersect.
- `_guard()` runs before argument validation and before any I/O, so a blocked tool never
  produces a request.
- `FORBIDDEN_TOOL_PREFIXES` blocks `create_ariel*`, `delete_ariel*`, `validate_aql`
  unconditionally, even if the allowlist were widened by mistake.
- `negotiate_capabilities()` **fails closed**: refuses to operate if the server
  advertises any known write tool, or advertises none of our read tools.
- Arguments must be scalars (`str`/`int`/`bool`); nested structures are rejected.
- Response size cap (8 MiB) enforced before parsing.
- Every call audited: tool name, duration, outcome, caller, **argument key names only** —
  never values, never response bodies.
- **A denied tool does not fall back to REST.** Denial is terminal.

**`app/providers/normalize.py`** (new) — shared, total coercion helpers so REST and MCP
normalize identical QRadar JSON identically.
**`app/providers/telemetry.py`** (new) — per-call latency / error-class / endpoint-category
counters. Categories are coarse (`offense_detail`, not `siem/offenses/4821`) so no
identifier leaks into telemetry.

### 6.2 Data model + migration

`0002_phase3_offenses_rules_coverage.py`.

Extended: `offense_snapshot` (+9 cols incl. `content_hash`, `usernames`,
`log_source_ids`, `close_time`, `source_network`), `analytics_rule` (+13 cols incl.
SOC-owned block, response config, contribution counters, `health_status`),
`detection_coverage` (+6 cols incl. `evidence`, `confidence`, `reason`).

New tables: `rule_dependency`, `rule_state_transition`, `rule_health_snapshot`,
`technique_mapping`, `detection_coverage_snapshot`.

New enums: `RuleHealthStatus` (8 values), `MappingSource`, `RuleDependencyKind`;
`CoverageStatus` gained `MISSING` / `NOT_EVALUATED` (`NOT_COVERED` retained as a legacy
alias so historical rows keep meaning).

### 6.3 Collectors and evaluators

- **`app/collectors/offense_collector.py`** — incremental via watermark, bounded backfill
  and page count, advisory-locked per (instance, collector), idempotent upsert,
  **change-driven** via `offense_content_hash()` so a stable offense produces one row
  rather than 288/day. Watermark is *not* advanced on a partial failure.
- **`app/collectors/rule_collector.py`** — merges rules + building blocks, never
  overwrites the SOC-owned column set, records enable/disable transitions, syncs EXPLICIT
  dependencies while leaving INFERRED ones alone.
- **`app/services/rule_health.py`** — ordered classifier (DISABLED → INSUFFICIENT_DATA →
  DEPENDENCY_DEGRADED → NOISY → NEVER_OBSERVED/INACTIVE → HEALTHY), versioned via
  `logic_version`, flap-damped, stores full evidence per snapshot. Preloads firing counts
  / dependencies / log-source health so N rules cost O(1) queries.
- **`app/services/detection_coverage.py`** — evaluates technique → rule → health →
  building block → log source → telemetry. A disabled rule can never contribute coverage;
  inferred mappings below `coverage_min_confidence` are recorded but not counted.
- **`app/services/locks.py`** (new) — advisory lock now keyed on **(instance, collector)**,
  so the offense and metric collectors no longer contend. The Phase 2 lock test was
  updated to derive its key from the shared helper instead of hardcoding it.

### 6.4 API

25 new paths under `/api/v1`, verified present in the OpenAPI schema:

- `/offenses`, `/offenses/analytics`, `/offenses/aggregates`,
  `/offenses/{id}`, `/offenses/{id}/history`
- `/rules`, `/rules/health-summary`, `/rules/{inactive,noisy,disabled,dependency-degraded,never-observed}`,
  `/rules/{id}` (GET + PATCH), `/rules/{id}/health-history`, `/rules/{id}/transitions`
- `/coverage/summary`, `/coverage/techniques`, `/coverage/techniques/{id}`,
  `/coverage/techniques/{id}/history`, `/coverage/degraded`, `/coverage/missing`,
  `/coverage/by-rule`, `/coverage/by-data-source`, `/coverage/mappings` (POST)
- `/providers/capabilities`

**No offense mutation endpoint exists.** The only writes are `PATCH /rules/{id}`
(SOC-owned metadata in *our* database) and `POST /coverage/mappings` (SOC-owned MITRE
mapping). Neither touches QRadar.

All list endpoints clamp limit/offset/date-range against config; sort fields resolve
through a lookup table and an unknown value returns 422 rather than silently falling back.

---

## 7. Verification actually performed

| Gate | Result |
|---|---|
| `ruff check app tests` | **clean** |
| `mypy app` | **29 errors in 17 files** — unchanged baseline, **zero new errors** |
| `pytest -m "not integration"` | **474 passed** |
| `pytest -m integration` | **211 passed** |
| `pytest` | **685 passed, 0 skipped, 0 failed** |
| `alembic upgrade head` + `alembic check` | **no drift** on a freshly created database |
| Frontend `npm ci` / `lint` / `typecheck` / `test` / `build` | all pass — 22 tests, production build succeeds on Next 15.5.20 |
| `npm audit --omit=dev` | 4 moderate, 0 high, 0 critical |

Test totals: **227 → 685** (+458). See §7a for the breakdown.

### Not performed

- `docker compose up -d --build` full-stack health check — **not run**.
- Compose smoke tests against the live endpoints — **not run**.
- Celery worker/beat registration — **no Phase 3 tasks exist yet** (§8.1).

> **Running the gates.** Run pytest from `backend/`, not the repo root: the migration
> tests resolve `alembic.ini` relative to the working directory and fail otherwise.
> Never run two pytest processes against the test database concurrently — they share
> one schema and will produce spurious cross-run failures.
>
> `alembic check` must run against a **freshly created** database. The `db_schema`
> fixture drops all model tables on teardown but leaves `alembic_version` stamped, so
> a post-test-run `alembic check` reports the entire schema as drift. Recreate the
> database first (`DROP DATABASE` / `CREATE DATABASE`) — this is the pre-existing
> Phase 2 behaviour noted in §9.3, not a Phase 3 regression.

---

## 7a. Characterization test workstream

Commit: `test(phase3): cover providers offenses rules and coverage`.

The Phase 3 core from `e733648` shipped with no tests. This workstream characterizes
the existing implementation; it adds **no new production features**.

### Tests added by domain

| Domain | File | Tests |
|---|---|---|
| QRadar REST provider | `tests/unit/test_qradar_rest_provider.py` | 82 |
| QRadar MCP provider | `tests/unit/test_qradar_mcp_provider.py` | 124 |
| Rule health | `tests/unit/test_rule_health.py` | 45 |
| Detection coverage | `tests/unit/test_detection_coverage.py` | 43 |
| Offense change detection / bounding | `tests/unit/test_offense_domain.py` | 46 |
| Offense collection (DB) | `tests/integration/test_offense_collection.py` | 30 |
| Rule health + coverage (DB) | `tests/integration/test_rule_health_coverage.py` | 29 |
| Phase 3 API (DB) | `tests/integration/test_phase3_api.py` | 59 |

Integration tests run against the real TimescaleDB service; nothing about
PostgreSQL/TimescaleDB is mocked. HTTP and MCP transports are mocked (`httpx.MockTransport`),
retry sleeps are captured rather than performed, and jitter uses a seeded `Random`, so no
test depends on a live QRadar instance or on wall-clock timing.

### Production bugs discovered and fixed

**1. Phase 3 API endpoints were unauthenticated.** *(security — the significant one)*

`offenses`, `rules`, `coverage` and `providers` were registered with no principal
dependency and no permission check. Phase 2 routers guard writes via `PrincipalDep` +
`require_permission`; these four guarded nothing, so **under OIDC they served offence
records — parsed usernames, source addresses, analyst assignment — and a map of where
detection coverage is absent, to a caller presenting no bearer token at all.**

Fixed by enforcing at the router rather than per-handler, so a new endpoint added to one
of these modules cannot ship unguarded by omission:

- `app/security/rbac.py` — added `PERM_OFFENSE_READ`, `PERM_RULE_READ`,
  `PERM_COVERAGE_READ`, `PERM_PROVIDER_READ` (all `read:*`-prefixed).
- `app/api/deps.py` — added `requires(permission)`, a dependency that resolves
  `get_principal` (this is what enforces authentication) then checks the permission.
- `app/api/router.py` — the four Phase 3 routers now declare `dependencies=[...]`.

Compatibility: all four are satisfied by the existing `read:*` wildcard, so read-only and
admin roles are unaffected. What changes is that *some* principal is now required.
Guarded by `TestAuthorization`, including a schema-driven test that walks every Phase 3
path in the OpenAPI document and asserts each refuses an unauthorized caller.

**2. MCP tool failures escaped the audit log.**

In `QRadarMCPProvider.call_tool`, `_decode` and `_unwrap` were called outside the audited
region. A JSON-RPC `error` member — the ordinary way a tool reports invalid arguments or
an internal fault — propagated with **no audit record written at all**, as did an
oversized response and unparseable JSON. Transport, auth and HTTP-status failures were
audited; the most common failure mode was not.

Fixed in `app/providers/qradar_mcp.py`: both calls are now wrapped, emitting a `FAILURE`
record before re-raising. `TestAuditing` asserts exactly one audit record per call across
the success and every failure path.

### Behaviour corrections (not bugs, but semantics changed)

**3. `NEVER_OBSERVED` now requires proof of observation.** *(carried in from the
uncommitted working-tree change; tested and committed here)*

No successful `RuleMetric` collection exists yet, so a zero trigger count meant "nobody
looked", not "it never fired". `RuleHealthEvaluator.classify` took `trigger_count == 0`
plus `last_fired_at is None` as evidence and returned `NEVER_OBSERVED` — reporting the
collector's own gap as a detection gap, the exact confusion this platform exists to
remove.

`classify` now takes `observation_complete`, **defaulting to `False`** so a caller that
omits it cannot get the unsafe verdict. `evaluate_instance` derives it from a real
`CollectionWatermark` for collector `rule_metric`: the watermark must exist, have
`intervals_collected > 0`, and have advanced *into* the evaluation window. Otherwise the
verdict is `INSUFFICIENT_DATA` with `confidence=0.2` and
`evidence["observation_complete"] = False`.

Covered by `TestObservationCompleteness` (unit) and
`TestObservationCompletenessAgainstTheDatabase` (integration — including that another
collector's watermark, another instance's watermark, a zero-interval watermark, and a
watermark stopping short of the window all fail closed).

This supersedes §9.6, which predicted every enabled rule would classify as
`NEVER_OBSERVED`. It now classifies as `INSUFFICIENT_DATA` until rule-metric collection
lands.

### Known limitations recorded, deliberately not "fixed"

- **Offense entity-list ordering.** `offense_content_hash` hashes
  `source_addresses` / `usernames` / `rule_ids` in received order, so a reordered but
  otherwise identical list reads as a change and writes a redundant snapshot. Not
  normalized: sorting would also erase first-seen ordering that the aggregation layer may
  later rely on. Pinned by `TestListOrdering` so the behaviour is a decision, not an
  accident.
- **Per-record failure isolation in `OffenseCollector._store` is partial.** Only
  `ValueError`/`TypeError` are caught. A record failing at the *database* layer
  (over-length text, constraint violation) aborts the transaction and the whole run, and
  the advisory-lock release then fails too. Fixing it properly needs a per-record
  SAVEPOINT — deferred, documented at the test that probes the boundary.
- **`httpx` deprecation.** Passing `ca_bundle` as a string triggers
  `DeprecationWarning: verify=<str> is deprecated`. Works today; should become
  `ssl.create_default_context(cafile=...)`.

### Not covered by this workstream

`RuleCollector.sync` (rule/building-block inventory synchronization, dependency mapping,
SOC-metadata preservation) has **no dedicated tests yet** — it is the largest remaining
characterization gap. The repository layer (`OffenseRepository` aggregations, top-entity
queries, magnitude trend) is exercised only through the API surface, not directly.

---

## 8. What remains

Ordered by dependency. Items 1–3 are required before Phase 3 can be called complete.

### 1. Background jobs (not started)
`app/workers/tasks.py` and the beat schedule in `app/workers/celery_app.py` have **no
Phase 3 entries**. Add async orchestrations + Celery shims mirroring the existing
`collect_metrics` pattern:
- `collect_offenses` — periodic offense sync (`offense_collection_interval_seconds`)
- `sync_rule_inventory` (`rule_collection_interval_seconds`)
- `evaluate_rule_health`
- `evaluate_detection_coverage` (`coverage_evaluation_interval_seconds`)
- `detect_stale_collection` — watermark lag / `consecutive_failures` alerting
- offense aggregate precomputation, if the analytics endpoint proves slow

Wire `QRadarMCPProvider(audit_sink=...)` to `app.repositories.audit.record_audit` here;
the provider accepts the callable but nothing currently supplies it, so MCP tool calls
are logged but **not yet written to AuditLog**.

### 2. Tests (not started)
The brief lists ~24 required unit tests and ~13 integration tests. None exist. At minimum:
- REST: pagination, retry/backoff, timeout, TLS refusal, auth failure, malformed responses
- MCP: allowlist enforcement, **all 25 write tools blocked by name**, unknown-tool denial,
  Ariel denial, capability negotiation failing closed
- Offense: normalization, content-hash idempotency, watermark behaviour, distributed lock
- Rule health: each classification branch, insufficient-history, dependency degradation
- Coverage: each status, explicit vs inferred, maintenance windows
- Integration (real Postgres/Timescale): snapshots, history, watermarks, concurrent
  collectors, API pagination/filtering, constraints, migrations

Use `respx` (already a dev dep) for REST contract tests and a mock HTTP server for MCP.
No production QRadar credentials in tests.

### 3. Frontend (not started)
`src/app/{offenses,rules,coverage}/page.tsx` are still Phase 2 "scaffolded" placeholders.
Needed: offense list/detail/history/analytics, rule list/detail/health-history and the
four canned health views, coverage summary/technique/rule/data-source/degraded/missing/history.
Server components for read-only data, small client components for charts and filters.
Add the Phase 3 types and calls to `src/lib/api.ts` and flip `live: true` in
`src/lib/sections.ts`. Handle empty data, API failure and loading in every view.

**Sanitize all QRadar text at render** — the backend sanitizes, but the echarts moderate
XSS advisory (§2) means chart formatters must not render HTML.

### 4. Docs
Update `README.md` (Phase 3 sections, env vars), `docs/mcp-capability-matrix.md` (record
that the provider-level allowlist is now enforced in code with a 25-tool blocklist), the
API documentation and `.env.example` with the ~20 new settings added to `app/core/config.py`.

### 5. Final verification
Full Compose stack up + health, smoke tests through the real stack, then the Phase 3
feature commit.

---

## 9. Known risks

1. **The Next.js dependency may still be exposed.** See §2. This is the single most
   important open item — recheck npm before any deploy.
2. **Migration 0001 reads live metadata.** Every future migration must be
   introspection-guarded until 0001 is frozen. See §5.
3. **`make check` is order-dependent.** The integration conftest does
   `Base.metadata.drop_all()` at teardown but leaves `alembic_version` stamped at `0001`,
   so a subsequent `alembic upgrade head` is a no-op against an empty schema and
   `alembic check` then reports every table as missing. Run migration checks against a
   fresh database, or make the teardown drop `alembic_version` too. This is pre-existing
   Phase 2 behaviour, not a Phase 3 regression.
4. **MCP tool calls are not yet audited to the database** — sink not wired (see §8.1).
5. **QRadar field mapping is unvalidated against a real appliance.** The REST
   normalization targets documented QRadar 20.0 shapes and is defensive about missing
   fields, but `building_block_ids` / `log_source_type_ids` / `last_triggered_time` are
   not exposed consistently by all QRadar versions. Expect to adjust
   `_to_rule()` once tested against a real console — and note that where QRadar gives us
   nothing, the collector records an *inference* with confidence, never a fact.
6. **Rule health depends on `rule_metric` being populated.** Nothing currently writes it
   for Phase 3. ~~Without firing counts every enabled rule will classify as
   NEVER_OBSERVED.~~ **Corrected (§7a.3):** an enabled silent rule now classifies as
   `INSUFFICIENT_DATA` until a completed rule-metric collection covers the evaluation
   window; `NEVER_OBSERVED` can no longer be emitted without that proof. The rule-metric
   feed remains a prerequisite for *meaningful* rule health — until it lands, rule health
   and every detection-coverage verdict downstream of it read as unestablished rather
   than as gaps.
7. **echarts moderate XSS** remains until the echarts 6 upgrade (§2).

---

## 10. Phase 4 recommendations

- Freeze migration 0001 to explicit DDL and drop the introspection guards.
- Upgrade echarts to 6.x and clear the last production audit finding.
- Automate the pre-deploy dependency gate (§2) in CI.
- Migrate `next lint` → ESLint CLI (deprecated in 15.5, removed in 16).
- Resolve the 29 pre-existing mypy errors.
- Only then consider the LLM/RAG work — explicitly out of scope for Phase 3, and the
  read-only MCP posture must survive it unchanged.

---

## 11. Live QRadar session

This session connected the platform to a real appliance for the first time. Everything
below was executed, not designed.

### 11.1 The lab

| | |
|---|---|
| Console | QRadar CE, reachable over a verified TLS chain |
| Version | **7.6.0 FP1** (`external_version` 7.6.0.0) |
| API versions offered | 0.1 → **29.0** (43 total, 34 flagged deprecated — including 20.0) |
| PKI | three-level: leaf → `QRadar Local CA` → `QRadar Local Root CA` |

Credentials live in `.secrets/` (git-ignored). `.gitignore` previously covered `*.pem`
but **not** `.secrets/` or `*.sec`, so a token file would have been committable; fixed.

### 11.2 Three findings that cost real time

**1. The CA bundle needs two certificates, not one.**
QRadar sends only its leaf. A bundle containing just the intermediate fails with
`unable to get issuer certificate`. Concatenate root + intermediate.

**2. Python rejects a certificate `openssl` accepts.**
Python 3.13+ turns on `VERIFY_X509_STRICT` in `create_default_context`, enforcing
RFC 5280 §4.2.1.1. QRadar's self-generated console certificate has a Subject Key
Identifier but **no Authority Key Identifier**, so verification fails with
`Missing Authority Key Identifier` while the `openssl` CLI verifies the same chain
happily. This presents as an application-only TLS failure after everything else checks
out.

`QRADAR_TLS_ALLOW_MISSING_AKI=true` clears that one flag in `app/providers/tls.py`.
Chain, expiry and hostname/IP-SAN verification remain enforced, and the context refuses
to build if they are ever weakened. **This is not `verify_ssl=false`**, which the
codebase still refuses outright.

**3. `/analytics/rules` on 7.6.0 returns 14 fields, and none of them are firing evidence.**

```
average_capacity  base_capacity  base_host_id  capacity_timestamp  creation_date
enabled  id  identifier  linked_rule_identifier  modification_date  name  origin
owner  type
```

No `last_triggered_time`. No `building_block_ids`. No `log_source_type_ids`. No
rule-statistics endpoint exists to substitute. This confirms §9.5 against a real console
and has three consequences:

- **Rule health cannot be established from inventory alone.** 95 of 133 rules classify as
  `INSUFFICIENT_DATA` and **zero** as `NEVER_OBSERVED`. That is the correct output, not a
  gap in the implementation — see §7a.3.
- **`rule_dependency` stays empty**, because explicit dependencies are read from
  `building_block_ids`.
- **Detection coverage has nothing to evaluate**, and reports `NOT_EVALUATED`.

The one defensible metric source available is offense contribution: an offense names its
contributing rules, which *proves* those rules fired. In this lab all 7 offenses trace to
**1** rule, so that would establish 1 of 133. Deliberately not built yet — see §12.

### 11.3 What was proven

Three consecutive `python -m app.cli.sync all` runs against the live console:

| | 1st run | 2nd | 3rd |
|---|---|---|---|
| Log sources | 36 created | 36 updated | 36 updated |
| Offenses | 7 seen / 7 written | — | — |
| Rules (incl. building blocks) | 352 created | 250 upd / 102 unch | 352 unchanged |
| Rule health | 133 evaluated | 133 | 133 |

Row counts afterwards — **no duplicates**:

| Table | Rows | Distinct |
|---|---|---|
| `log_source` | 36 | 36 |
| `analytics_rule` | 352 | 352 |
| `offense_snapshot` | **7** | 7 |
| `rule_health_snapshot` | 399 | 133 (×3 evaluations — time series, intended) |
| `qradar_instance` | 1 | 1 |

`offense_snapshot` holding 7 rows rather than 21 is the content-hash change detection from
§6.3 working on real data. Watermarks reached `intervals_collected=3` with
`consecutive_failures=0` for all three collectors. Re-running `qradar add` left the
instance count at 1.

All 15 offense/rule/coverage/provider endpoints return 200 against this data, and all four
frontend routes render it.

> **Rules converge on the third run, not the second.** Run 2 reports 250 updates because
> `_create` does not populate every field `_qradar_fingerprint` compares, so the first
> update writes them. Run 3 is fully `unchanged`. Harmless but worth tidying.

### 11.4 What this session added

| Commit | |
|---|---|
| `e01c487` | File-backed tokens, `app/providers/tls.py`, `build_provider_for_instance` |
| `b7b6f7b` | `LogSourceCollector`, 5 Celery tasks + Beat entries, `app.cli.qradar` / `app.cli.sync` |
| `9adbd48` | MCP audit sink → `AuditLog`; redaction correlation fix |
| `9b4baae` | `CountEntry` key coercion (live-data 500) |
| `f9d1feb` | Offense / rule / coverage frontend |

Two production bugs found by real data:

- **`GET /offenses/analytics` 500'd** on any instance with offenses. `CountEntry.key` is
  typed `str`, but several distributions group by numeric columns. The mock provider never
  produced a numeric grouping key, so nothing caught it.
- **Audit correlation was silently destroyed.** `redact()` masks token-shaped values and a
  UUID is token-shaped, so `correlation_id` and `instance_id` were written as
  `***redacted***`. Exempting UUIDs by *value* was not an option — a QRadar SEC token is
  itself a UUID — so the exemption is keyed on field name, covering exactly two names.

### 11.5 Operating it

```bash
# register (idempotent)
cd backend && python -m app.cli.qradar add --name qradarce2 \
  --url https://<console> --token-file ../.secrets/qradar.sec \
  --ca-file ../.secrets/qradar-ca.pem --api-version 29.0

python -m app.cli.qradar list
python -m app.cli.qradar test --name qradarce2

# collect on demand (Beat also schedules all of these)
python -m app.cli.sync all --instance qradarce2 [--json]
```

Beat entries: `sync-log-sources`, `collect-offenses`, `sync-rule-inventory`,
`evaluate-rule-health`, `evaluate-detection-coverage`. Every interval is configurable;
see `.env.example`. Building blocks are covered by `sync_rule_inventory` — `RuleCollector`
merges both endpoints into one locked pass, so a separate task would double the upstream
fetch and self-contend on the advisory lock.

---

## 12. Remaining work

Ordered by what unblocks the most.

1. **Compose stack verification — not done.** The slice was proven with host-side uvicorn
   and `next start` against a containerised database. `docker compose up -d --build` with
   all eight services has **not** been run this session. Secrets must be mounted as
   read-only Docker secrets; SELinux stays enforcing and `:z`/`:Z` remain forbidden
   (see §4.2).
2. **Rule metrics from offense contribution.** The only honest firing evidence available
   on 7.6.0. Derive `RuleMetric` from stored `offense_snapshot.rule_ids`, recording
   provenance (`offense_contribution`) and completeness — it establishes only rules that
   produced offenses, within the offense retention window, so it must not be presented as
   total observation. This is what moves rules off `INSUFFICIENT_DATA`.
3. **ATT&CK technique mappings.** Coverage reports `NOT_EVALUATED` until they exist.
   `POST /api/v1/coverage/mappings` accepts them; there is no seed set.
4. **`RuleCollector.sync` still has no dedicated tests** (§7a) — the largest
   characterization gap, now also the most exercised code path.
5. **Rule detail depth.** `/rules/[id]` shows metadata and health evidence; building-block
   dependencies, telemetry dependencies and health history are not surfaced (and
   dependencies are empty upstream anyway — §11.2).
6. **Offense analytics visualisation.** `/offenses/analytics` returns aging buckets and
   distributions that no page renders yet.
7. The pre-existing items in §10 still stand.
