# qradar-observability

An **on-premises QRadar observability and SOC analytics platform**. It monitors IBM QRadar SIEM
health, scheduled security searches, log-source anomalies, offenses, analytics-rule health and
MITRE ATT&CK detection coverage — and it operates strictly **read-only** against QRadar.

> **Status:** Phases 1–3 complete, including a verified live QRadar 7.6.0 FP1 vertical slice:
> REST/Ariel collection, PostgreSQL persistence, Celery scheduling, guarded APIs, and offense,
> rule-health, and detection-coverage dashboards. Phase 4 remains out of scope. See
> [Roadmap](#roadmap) and [the Phase 3 handoff](docs/PHASE3-HANDOFF.md).

Phase 3.5 adds an opt-in, manual synthetic-telemetry lab without changing the read-only QRadar
application contract. See [the lab guide](docs/LAB-SYNTHETIC-TELEMETRY.md) and
[sanitized demo results](docs/LAB-DEMO-RESULTS.md). The generator is never started by the backend,
Celery, Beat, Compose, or normal tests.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Architecture](#architecture)
- [The QRadar MCP integration](#the-qradar-mcp-integration)
- [Provider abstraction](#provider-abstraction)
- [Security model](#security-model)
- [Installation](#installation)
- [Development](#development)
- [Testing](#testing)
- [Data model](#data-model)
- [Roadmap](#roadmap)

---

## Why this exists

QRadar tells you about *offenses*. It is far less good at telling you when it has quietly stopped
*seeing* — a log source that went silent at 02:00, a DSM update that broke username parsing, a rule
that has not fired in ninety days, a MITRE technique whose only detection is disabled. Those
failures are invisible precisely because they produce no events. This platform watches for the
**absence** of signal as well as its presence, scores each log source's health, and alerts once —
without duplicate noise — when something degrades.

## Architecture

```
                      ┌──────────────┐
   browser  ───────▶  │  frontend    │   Next.js + TypeScript + ECharts
                      │  (Next.js)   │   (holds NO QRadar credentials)
                      └──────┬───────┘
                             │  REST /api/v1
                      ┌──────▼───────┐
                      │  backend     │   FastAPI + SQLAlchemy 2 + Pydantic 2
                      │  (FastAPI)   │   rate-limited, RBAC, audit log
                      └──┬────────┬──┘
              async jobs │        │ provider abstraction
                  ┌──────▼──┐  ┌──▼─────────────────────────────┐
                  │ Celery  │  │ QRadarProvider (base.py)        │
                  │ worker/ │  │  ├─ QRadarRestProvider  (REST/Ariel — collection + AQL)
                  │ beat    │  │  ├─ QRadarMCPProvider   (GET-only inventory, future AI)
                  └────┬────┘  │  └─ MockQRadarProvider  (dev/tests)
                       │       └──────────────┬──────────────────┘
             ┌─────────▼───────┐              │
             │ PostgreSQL +    │        ┌─────▼────────┐        ┌──────────────┐
             │ TimescaleDB     │        │ QRadar console│  ◀────│  qradar-mcp  │ (internal net,
             │ (hypertables)   │        │  REST + Ariel │        │  GET-only    │  no host ports)
             └─────────────────┘        └───────────────┘        └──────────────┘
             Redis ── Celery broker/result backend
```

**Key rule:** deterministic background data collection and scheduled AQL execution go through the
**direct REST/Ariel** provider. The **MCP** service is used only for inventory retrieval and
future AI-agent investigations. The two are never conflated — see below.

### Backend layout

```
backend/app/
  api/          FastAPI routers + dependencies
  core/         config, database, logging, rate limiting
  models/       SQLAlchemy 2 models (one module per bounded context)
  schemas/      Pydantic 2 request/response models
  services/     domain logic (health scoring, inventory sync, overview, seed,
                AQL validation, search execution + cron scheduling)
  providers/    QRadarProvider ABC + REST / MCP / Mock + DTOs + factory
  collectors/   metric collectors
  anomaly/      baselines, detectors, anomaly engine
  alerts/       alert lifecycle, fingerprints, routing, notifiers
  workers/      Celery app + tasks
  repositories/ data-access layer
  security/     sanitization, RBAC, redaction
  tests/        unit (always run) + integration (Postgres-gated)
```

## The QRadar MCP integration

The upstream [`IBM/qradar-mcp`](https://github.com/IBM/qradar-mcp) repository is vendored, **pinned
to commit `b8f6a4a3fe901eac4f55e4ca5d146d952f55db51`**, and run as a **separate Docker service**.
It is not modified.

Before writing a line of integration code we AST-parsed all 73 tools in that commit and produced a
**capability matrix** — [`docs/mcp-capability-matrix.md`](docs/mcp-capability-matrix.md) — mapping
every MCP tool to its QRadar endpoint, HTTP verb, read/write classification, required permission and
suitability for this platform. The README roadmap of the upstream project was deliberately ignored;
only code that exists at the pinned commit is trusted.

That analysis surfaced several things that shaped the design, notably:

- The commit is literally *"enable-crud"* — the MCP server ships with **`POST` and `DELETE`
  enabled** and **`verify_ssl: false`** / **`debug: true`** defaults.
- QRadar uses `POST` (not `PUT`) for updates, so `POST` must be treated as *mutating*.
- Running AQL is itself a `POST` (`create_ariel_search`), which independently confirms our rule to
  keep query execution off the MCP path.

We therefore run the MCP service locked down (§ [Security model](#security-model)) and never depend
the application on it — everything goes through the provider abstraction instead.

To regenerate the matrix after a version bump:

```bash
python backend/scripts/extract_mcp_capabilities.py
```

## Provider abstraction

`app/providers/base.py` defines `QRadarProvider`, a **read-only** interface that returns normalized
DTOs (`app/providers/dto.py`) — never raw QRadar JSON. Three implementations:

| Provider | Role | AQL execution? |
|---|---|---|
| `QRadarRestProvider` | deterministic collection + scheduled AQL | ✅ owns the Ariel lifecycle |
| `QRadarMCPProvider`  | inventory retrieval, future AI investigations | ❌ by design |
| `MockQRadarProvider` | development and tests | ✅ (in-memory, deterministic) |

Code asks a provider **what it can do** via `ProviderCapability`, rather than checking its type:

```python
provider.require(ProviderCapability.AQL_EXECUTION)   # raises on MCP
```

The provider is selected by the `QRADAR_PROVIDER` environment variable (`mock` | `rest` | `mcp`)
through `app/providers/factory.py`.

## Security model

Read-only, defence-in-depth, secrets never in code or browser.

- **Read-only everywhere.** The provider interface has no create/update/delete methods. The MCP
  write surface is disabled by a mounted [`feature_toggles.json`](deploy/mcp/feature_toggles.json)
  (`POST`/`DELETE` off), *and* `QRadarMCPProvider` enforces a hardcoded read-only tool allowlist
  independent of what the server advertises, *and* the QRadar service account is itself view-only.
- **MCP is not exposed.** It runs on an internal Docker network with **no published ports**, with
  `read_only`, `cap_drop: ALL`, `no-new-privileges`.
- **TLS is mandatory.** `QRadarRestProvider` refuses `verify_ssl=False` and non-`https` hosts.
  Production config refuses to boot with TLS verification off.
- **Secrets never in source.** All via env; see [`.env.example`](.env.example). Sensitive
  configuration (QRadar tokens, webhook URLs) is **Fernet-encrypted at rest** via a custom
  `EncryptedString` column type. QRadar tokens are never returned in an API response, never sent to
  the browser, and never sent to an LLM.
- **Structured JSON logging** with a redaction filter that strips token-like values.
- **RBAC + audit.** Role/permission model plus an append-only `AuditLog` for administrative and
  search actions.
- **Output sanitization.** All QRadar-sourced text (offense descriptions, usernames, source names)
  is sanitized before display to prevent stored XSS.
- **Rate limiting** on the API (`slowapi`), with a tighter limit on search execution.
- **Production hardening is enforced at startup**, not documented and hoped for: `Settings` raises
  if `DEBUG` is on, TLS is off, the provider is `mock`, a REST token is missing, or autonomous LLM
  actions are enabled in production.

### LLM posture

Future LLM support exists **as an interface only and is disabled by default** (`LLM_ENABLED=false`).
The planned `LLMProvider` accepts a **redacted, normalized** offense investigation package and
returns structured JSON (summary, likely scenario, severity, evidence, affected entities, missing
info, recommended AQL, recommended actions, confidence). **The LLM performs no autonomous
actions** — it can never close offenses, block IPs, disable users, modify rules or update reference
sets. This is enforced by the read-only architecture, not merely by prompt.

## Installation

### Prerequisites

- Docker + Docker Compose
- (For a real deployment) a QRadar **read-only authorized service token** and, if QRadar uses an
  internal CA, the CA bundle.

### Cloning (the qradar-mcp submodule)

`qradar-mcp/` is a **git submodule** pinned to commit `b8f6a4a3fe901eac4f55e4ca5d146d952f55db51`
of <https://github.com/IBM/qradar-mcp.git>. The Compose stack builds its image from that checkout,
so a clone without it will fail at `docker compose build qradar-mcp`.

```bash
# Either clone recursively:
git clone --recurse-submodules <this-repo> qradar-observability

# …or populate it after an ordinary clone:
git clone <this-repo> qradar-observability && cd qradar-observability
git submodule update --init --recursive
```

Verify the pin before building — it must report exactly this commit, and the leading status
character must be a space (not `+`, which would mean the checkout has drifted off the pin):

```bash
git submodule status
#  b8f6a4a3fe901eac4f55e4ca5d146d952f55db51 qradar-mcp (heads/main)
```

The pin is deliberate: the MCP allowlist in `backend/app/providers/qradar_mcp.py` is written
against the tool surface of that exact commit. Do not bump it without re-reviewing the allowlist
against [docs/mcp-capability-matrix.md](docs/mcp-capability-matrix.md).

### Quick start (mock provider — no QRadar needed)

```bash
git clone --recurse-submodules <this-repo> qradar-observability && cd qradar-observability

cp .env.example .env
# Generate the two required secrets:
python -c "import secrets; print('SECRET_KEY='+secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY='+Fernet.generate_key().decode())"
# paste both into .env; leave QRADAR_PROVIDER=mock

docker compose up -d postgres redis
docker compose run --rm migrate            # alembic upgrade head
docker compose run --rm backend python -m app.services.seed   # roles + inventory
docker compose up -d backend frontend celery-worker celery-beat
```

Then open **http://localhost:3000**. The SOC Overview and Log Sources pages are populated from the
mock provider.

### Connecting a real QRadar

#### 1. Put the credentials on disk, not in the environment

Create `.secrets/` (git-ignored) with the SEC token and the CA bundle. Do not
pass the token on a command line — an argv element is visible in `ps` output
and lands in shell history.

```bash
mkdir -p .secrets && chmod 700 .secrets
read -rs QR_TOK && printf '%s' "$QR_TOK" > .secrets/qradar.sec && unset QR_TOK
chmod 600 .secrets/qradar.sec
```

The CA bundle must contain the **whole** trust path the appliance does not
send. QRadar presents only its leaf certificate, so a file holding just the
intermediate fails with `unable to get issuer certificate` — concatenate the
root and the intermediate into `.secrets/qradar-ca.pem`.

Then load both into the `qradar-observability-secrets` volume, which is what the
containers actually mount at `/run/secrets`:

```bash
./deploy/load-secrets.sh
```

`.secrets/` is **not** bind-mounted. On an SELinux-enforcing host the repository
is labelled `user_home_t` and no container process can read it — not even root
in the container — and the usual `:z`/`:Z` fix would relabel the host tree as a
side effect. The script streams the files into a named volume over stdin, so no
host label changes. Re-run it after rotating either file; the volume is declared
`external`, so `docker compose down -v` will not wipe your credentials.

Verify the chain before going further:

```bash
openssl s_client -connect qradar.your.internal:443 \
  -CAfile .secrets/qradar-ca.pem -verify_return_error </dev/null 2>&1 | grep Verify
# expect: Verify return code: 0 (ok)
```

#### 2. Configure the provider

In `.env`:

```ini
QRADAR_PROVIDER=rest
QRADAR_BASE_URL=https://qradar.your.internal
QRADAR_API_TOKEN_FILE=/run/secrets/qradar_sec_token   # preferred over QRADAR_SEC_TOKEN
QRADAR_VERIFY_SSL=true
QRADAR_CA_BUNDLE=/run/secrets/qradar_ca.pem
```

> **If TLS fails only from Python.** Python 3.13+ enables `VERIFY_X509_STRICT`
> by default. QRadar's self-generated console certificate carries a Subject Key
> Identifier but no Authority Key Identifier, which RFC 5280 §4.2.1.1 requires,
> so strict mode rejects a chain the `openssl` CLI accepts. The symptom is
> `SSLCertVerificationError: Missing Authority Key Identifier` while the
> `openssl` check above passes. Set `QRADAR_TLS_ALLOW_MISSING_AKI=true`. That
> relaxes a certificate *labelling* assertion only — chain, expiry and
> hostname/IP-SAN verification stay enforced, and it is not equivalent to
> disabling verification, which this codebase refuses outright. The real fix is
> to reissue the appliance certificate with an AKI.

#### 3. If QRadar is a libvirt VM, put the containers on its segment

Skip this unless the appliance is a VM on a libvirt bridge. The symptom is a
container that gets `ECONNREFUSED` reaching QRadar while the host connects
fine — libvirt installs a `FORWARD ... -j REJECT` rule that drops traffic
arriving from other bridges, so the Compose bridge cannot route to the VM.

Rather than change the host firewall, attach the QRadar-facing services to a
macvlan over the libvirt bridge. Edit the interface and subnet in the script to
match `ip -br addr show virbr0`, then:

```bash
./deploy/create-vmnet.sh
```

and add a `docker-compose.override.yml` putting `backend`, `celery-worker`,
`celery-beat` and `migrate` on that network. The override is git-ignored: it
encodes one machine's network layout, not the project's topology.

#### 4. Register the console

Registration is idempotent by name — running it twice updates the existing
instance rather than creating a second one.

```bash
cd backend
python -m app.cli.qradar add \
  --name qradarce2 \
  --url https://qradar.your.internal \
  --token-file ../.secrets/qradar.sec \
  --ca-file ../.secrets/qradar-ca.pem \
  --api-version 29.0
```

The token is encrypted at rest with `ENCRYPTION_KEY` and is never returned by
any API. `add` finishes with a live connection check; `--no-verify` skips it.

```bash
python -m app.cli.qradar list          # registered consoles
python -m app.cli.qradar test --name qradarce2
```

#### 5. Collect

Beat runs these on a schedule, but you do not have to wait for it:

```bash
python -m app.cli.sync log-sources --instance qradarce2
python -m app.cli.sync log-source-metrics --instance qradarce2
python -m app.cli.sync offenses --instance qradarce2
python -m app.cli.sync stale-offenses --instance qradarce2
python -m app.cli.sync offense-aggregates --instance qradarce2
python -m app.cli.sync rules --instance qradarce2   # rules AND building blocks
python -m app.cli.sync rule-metrics --instance qradarce2
python -m app.cli.sync rule-health --instance qradarce2
python -m app.cli.sync coverage --instance qradarce2
python -m app.cli.sync all --instance qradarce2     # dependency order shown above
```

Every sync is idempotent: re-running creates no duplicate rows, and a failed
run does not advance its watermark. Add `--json` for machine-readable output.

Then open <http://localhost:3000/offenses>, `/rules` and `/coverage`.

#### 6. Run and verify the full local stack

The production-style images copy source at build time; the application source tree is not bind
mounted. SELinux stays enforcing and no service is privileged or mounts the Docker socket.

```bash
./deploy/load-secrets.sh
docker compose up -d --build
docker compose ps -a
docker compose logs --tail=300

curl --fail http://127.0.0.1:8000/api/v1/health/live
curl --fail http://127.0.0.1:8000/api/v1/health/ready
curl --fail http://127.0.0.1:8000/api/v1/providers/capabilities
curl --fail http://127.0.0.1:3000/offenses

docker compose exec celery-worker \
  celery -A app.workers.celery_app inspect registered
```

Required long-running services are `postgres`, `redis`, `backend`, `celery-worker`,
`celery-beat`, and `frontend`; each must report `healthy`. `migrate` is a one-shot service and must
report exit code 0. `qradar-mcp` is intentionally excluded unless the `mcp` profile is requested.

For a safe operational smoke test, run `sync all` twice. Natural-key row counts must remain stable,
and every `collection_watermark` row must retain `consecutive_failures=0` and a null `last_error`.
The second rule-inventory pass should converge to `unchanged`; log-source upserts may report
`updated` while their natural-key row count remains unchanged.

#### A note on what rule health can honestly report

QRadar's `/analytics/rules` endpoint returns no last-triggered timestamp, no
building-block references and no log-source-type references, and there is no
rule-statistics endpoint. So for a rule that has produced no offense, the
platform has **no evidence either way** about whether it has ever fired.

`RuleMetric` therefore derives only a lower bound from stored offense contributions. Each row is
marked `provenance=offense_contribution`, `completeness=incomplete`, and `inferred=true`. A positive
contribution proves that a rule fired; absence of a contribution does not prove zero. Silent rules
remain `INSUFFICIENT_DATA`, never `NEVER_OBSERVED`, until a future complete source verifies a zero
window. Detection coverage likewise shows `NOT_EVALUATED` rather than 0% until ATT&CK technique
mappings exist.

To also enable the MCP service (inventory / future AI), copy
`deploy/mcp/config.json.example` → `deploy/mcp/config.json`, fill in the read-only token, and start
it with the `mcp` profile:

```bash
docker compose --profile mcp up -d qradar-mcp
```

The MCP container has no host ports; only the backend reaches it.

## Development

Backend, without Docker:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export QRADAR_PROVIDER=mock
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
INTERNAL_API_BASE_URL=http://localhost:8000/api/v1 \
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1 npm run dev
```

**Scheduling:** Celery + Redis is the default. Every background job is an `async` function in
`app/workers/tasks.py` with a thin Celery shim around it, so the same orchestration runs unchanged
under APScheduler — the documented MVP fallback for teams not running Celery. Celery Beat drives
13 configurable jobs: log-source metrics/inventory, offense collection/staleness/aggregates,
rule inventory/metrics/health, detection coverage, anomaly evaluation, scheduled searches,
notification dispatch, and baseline rebuilds. Building blocks are collected in the same locked
inventory pass as analytics rules.

## Testing

```bash
cd backend
pip install -e '.[dev]'
ruff check app tests
ENCRYPTION_KEY=<fernet-key> pytest -m "not integration"

# Integration tests need PostgreSQL + TimescaleDB:
export TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/qradar_obs_test
pytest -m integration
pytest
alembic upgrade head
alembic check
```

Unit tests cover the health-score arithmetic, output sanitization, mock-provider determinism, the
MCP allowlist and no-AQL capability, TLS refusal, encrypted-column round-trip, production config
hardening, robust statistics, alert fingerprinting, notification routing and cron expansion.
Integration tests exercise inventory sync, metric collection, baselines, the anomaly engine, the
search executor and scheduler, search alerting, notification dispatch, the result-trend endpoint and
the API against a real database. All QRadar
responses in tests are mocked and no notification is ever really sent (`MockNotifier`).

Current suite: **881 tests — 634 unit + 247 integration, all passing with 0 skipped** against a real
`timescale/timescaledb:2.17.2-pg16` instance. `ruff check` is clean; `mypy app` reports **28 existing
errors in 16 files**, improved from the Phase 3 baseline of 29 in 17, with no new errors. Alembic
upgrades a fresh database through `0003` and reports no drift.

Frontend tests run under Vitest + Testing Library:

```bash
cd frontend
npm ci
npm audit --omit=dev
npm run lint
npm run typecheck
npm test             # 64 tests
npm run build
```

The production build succeeds on Next.js **15.5.22**. The production-only audit currently reports
5 transitive findings (2 moderate, 3 high): ECharts, PostCSS, and sharp. npm offers only breaking or
incorrect forced resolutions for this pinned Next.js line, so no `--force` override is applied; see
the Phase 3 handoff for the exact advisories and mitigations.

Note on tooling: the enforced gates are `ruff check` and `pytest` (see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)). `mypy` runs advisory-only until the Phase 1
modules are fully annotated. `ruff format` is deliberately **not** part of the toolchain — the
codebase uses a compact hand-formatted style throughout.

## Data model

18 domain models across bounded contexts (`instance`, `log_source`, `search`, `offense`, `rule`,
`config_change`, `alert`, `identity`). Four are **TimescaleDB hypertables** for time-series:
`log_source_metric`, `search_result_metric`, `rule_metric`, `offense_snapshot`.

Highlights:

- **Log-source health** is scored 0–100 as **40% freshness + 25% volume + 20% parsing +
  15% collection** (`app/services/health_score.py`, a pure function).
- **Baselines** use **median + MAD** per (weekday, hour) cell — robust to incident spikes, no ML.
- **Anomaly types:** `NO_EVENTS`, `VOLUME_DROP`, `VOLUME_SPIKE`, `PARSING_DEGRADATION`,
  `UNKNOWN_EVENT_SPIKE`, `TIMESTAMP_DELAY`, `CARDINALITY_DROP`, `COLLECTION_ERROR`,
  `REPEATED_PAYLOAD`.
- **Alert lifecycle:** `OPEN → ACKNOWLEDGED → RESOLVED`, with a **partial unique index** on
  `dedup_key` for non-resolved alerts that structurally prevents duplicate notifications.
- **Scheduled searches** store the AQL, enforce timeout / max time-range / max rows / concurrency,
  keep query **version history**, and store **aggregated** results by default — never raw events.

### Scheduled-search dispatch

`app/services/search_scheduler.py` expands each search's cron expression (via APScheduler's
`CronTrigger`, already a dependency) and persists `next_run_at`, so the schedule survives a restart
and two workers agree on which tick is which. The policies are chosen so the scheduler cannot become
an incident of its own:

| Situation | Behaviour |
|---|---|
| A search is seen for the first time | `next_run_at` is seeded; it does **not** run. Deploying 50 searches never fires 50 searches at once. |
| The worker was down for an hour | The overdue search runs **once**, then reschedules from *now*. Replaying every missed tick would stampede QRadar with already-stale queries. |
| A duplicate beat delivery | Same tick → same `run_key` → the unique constraint reuses the execution row. No double-run. |
| A previous run is still in flight | Skipped, not queued. Past twice its own timeout a run is presumed dead so a crashed worker cannot silently retire a search. |
| A cron expression is malformed | Reported and skipped; `next_run_at` is preserved so fixing it resumes the schedule. One bad search never stops the cycle. |
| Many searches are due at once | Bounded by `SEARCH_MAX_DISPATCH_PER_CYCLE`. |

### What raises an alert

Three independent conditions, each with its own fingerprint so they never deduplicate into one
another, all flowing into the same lifecycle and notification pipeline:

| Condition | Opens when | Resolves when |
|---|---|---|
| `anomaly` | a detector stays anomalous for N consecutive intervals (hysteresis) | it stays healthy for M intervals |
| `search_threshold` | a completed run crosses the search's threshold | a later run completes under it |
| `search_failure` | a search fails `SEARCH_FAILURE_ALERT_AFTER` consecutive runs | the next run succeeds |

`search_failure` exists because a detection that has quietly stopped executing produces no results
and therefore no threshold breach — it would otherwise fail silently, which is precisely the blind
spot this platform exists to close.

### Result trends

`GET /api/v1/searches/{search_id}/results` returns a search's stored aggregates, oldest first, for
charting:

| Parameter | Default | Notes |
|---|---|---|
| `metric_key` | `total` | `total` plus one key per GROUP BY dimension |
| `start` / `end` | unbounded | Inclusive. Naive timestamps are read as UTC; offsets are converted, never relabelled |
| `limit` | 500 (max 5000) | When more points match, the **most recent** win — a chart silently showing the oldest 500 of 50,000 points would be actively misleading |

Each point carries its `execution_id`, `execution_status`, `duration_ms`, `result_count`,
`threshold_breached`, `query_version` and `query_version_id`. Executions and version rows are joined
in one statement, so point count never drives query count. An inverted range or an over-cap limit is
rejected with 422; an unknown search is 404.

`/searches/[id]` renders this with Apache ECharts (`components/ResultTrend.tsx`, the only place
ECharts is imported). A **failed run breaks the line rather than plotting zero** — inventing a
traffic cliff that never happened is worse than a visible gap — and **dashed markers annotate
query-version boundaries**, because results either side of an AQL change are not comparable.

### Operator actions

`/alerts/[id]` and `/searches/[id]` expose acknowledge, resolve and manual-run controls
(`components/AlertActions.tsx`, `components/RunSearchButton.tsx`). These are the only client
components in the app: every page remains a React Server Component and passes ids and primitives
across the boundary, never an object or a credential. Authorization stays entirely server-side — the
components call the guarded endpoints and render the outcome, and a 403 surfaces as a clear
permissions message rather than a silent failure.

Operator actions (acknowledge / resolve) enqueue notifications too, so a manual resolve is not
invisible to whoever is on call. Enqueueing is always separate from delivery: an HTTP request
records the intent and returns, and the `dispatch_notifications` worker does the sending with retry
and dead-lettering.

Migrations are managed by Alembic; the initial migration materializes the schema from model
metadata (so it can never drift) then applies hypertable and retention policies.

## Roadmap

Development follows four phases; each phase explains its design, lists files, implements, tests,
lints and updates this README.

- **Phase 1 ✅** — architecture, Docker Compose, models + migrations, `MockQRadarProvider`,
  SOC overview + log-source inventory.
- **Phase 2 ✅** — scheduled searches, metric collection, anomaly detection, alert lifecycle +
  notifications (Teams / email / Slack / generic webhook / syslog), operator action UI
  (acknowledge / resolve / manual run) and the ECharts result-trend chart.
- **Phase 3 ✅** — live read-only QRadar REST/Ariel collection, offenses, analytics rules,
  provenance-aware rule metrics and health, detection coverage, guarded APIs, Celery scheduling,
  MCP audit persistence, and operator dashboards.
- **Phase 4** — security hardening, expanded tests, documentation, observability.

## License

The vendored `qradar-mcp/` retains its upstream Apache-2.0 license and `NOTICE`. This platform's own
code is under this repository's license.
