# qradar-observability

An **on-premises QRadar observability and SOC analytics platform**. It monitors IBM QRadar SIEM
health, scheduled security searches, log-source anomalies, offenses, analytics-rule health and
MITRE ATT&CK detection coverage — and it operates strictly **read-only** against QRadar.

> **Status:** Phases 1–2 complete (architecture, data model + migrations, `MockQRadarProvider`,
> SOC overview and log-source inventory; scheduled searches, metric collection, baselines,
> anomaly detection, alert lifecycle and notification delivery). Phases 3–4 are scoped and
> scaffolded. See [Roadmap](#roadmap).

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

### Quick start (mock provider — no QRadar needed)

```bash
git clone <this-repo> qradar-observability && cd qradar-observability

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

In `.env`:

```ini
QRADAR_PROVIDER=rest
QRADAR_HOST=https://qradar.your.internal
QRADAR_SEC_TOKEN=<read-only authorized service token>
QRADAR_VERIFY_SSL=true
QRADAR_CA_BUNDLE=/etc/ssl/certs/qradar-ca.pem   # if internal CA
```

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
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1 npm run dev
```

**Scheduling:** Celery + Redis is the default. Every background job is an `async` function in
`app/workers/tasks.py` with a thin Celery shim around it, so the same orchestration runs unchanged
under APScheduler — the documented MVP fallback for teams not running Celery. Celery beat drives
five jobs: `collect_metrics`, `evaluate_anomalies`, `run_due_searches`, `dispatch_notifications`
and a daily `rebuild_baselines`.

## Testing

```bash
cd backend
pip install -e '.[dev]'
ruff check app tests
ENCRYPTION_KEY=<fernet-key> pytest          # unit tests run anywhere

# Integration tests need PostgreSQL + TimescaleDB:
export TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/qradar_obs_test
pytest -m integration
```

Unit tests cover the health-score arithmetic, output sanitization, mock-provider determinism, the
MCP allowlist and no-AQL capability, TLS refusal, encrypted-column round-trip, production config
hardening, robust statistics, alert fingerprinting, notification routing and cron expansion.
Integration tests exercise inventory sync, metric collection, baselines, the anomaly engine, the
search executor and scheduler, search alerting, notification dispatch and the API against a real
database, and **skip cleanly** when `TEST_DATABASE_URL` is unset. All QRadar responses in tests are
mocked and no notification is ever really sent (`MockNotifier`).

Current suite: **124 unit tests passing, 76 integration tests** (DB-gated), `ruff check` clean.

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
  notifications (Teams / email / Slack / generic webhook / syslog).
- **Phase 3** — offenses, analytics rules, rule health, detection coverage; the real
  `QRadarRestProvider` and `QRadarMCPProvider`.
- **Phase 4** — security hardening, expanded tests, documentation, observability.

## License

The vendored `qradar-mcp/` retains its upstream Apache-2.0 license and `NOTICE`. This platform's own
code is under this repository's license.
