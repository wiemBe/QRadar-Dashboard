# Next-session prompt

Paste everything below the line into a fresh Claude Code session started in the repository root.

---

Continue work on this existing repository. Do **not** re-scaffold or recreate the project.

Read `docs/PHASE2-HANDOFF.md` first — it is an accurate, verified description of the current state.
Then confirm it against the code with `git log --oneline -5`, `git status`, and by reading the files
it references. Trust the code over the document if they disagree.

**Context:** on-premises QRadar observability platform, strictly read-only against QRadar. FastAPI +
SQLAlchemy 2 + Celery backend, Next.js frontend, PostgreSQL + TimescaleDB. Phase 1 is complete.
Phase 2 (scheduled searches, metric collection, baselines, anomaly detection, alert lifecycle,
notification delivery) is complete on the **backend** and verified by unit tests.

**Two tasks remain, both frontend-facing. Nothing else.**

1. **Operator action UI.** `POST /alerts/{id}/acknowledge`, `POST /alerts/{id}/resolve` and
   `POST /searches/{id}/run` all exist, enforce RBAC, write audit rows and enqueue notifications —
   but no UI can call them. Every page in `frontend/src/` is a read-only server component; there is
   no `"use client"` component anywhere. Add the client methods to `frontend/src/lib/api.ts`, create
   `AlertActions.tsx` and `RunSearchButton.tsx` client components, and mount them in
   `frontend/src/app/alerts/[id]/page.tsx` and `frontend/src/app/searches/[id]/page.tsx`. Also add
   `next_run_at` to the `ScheduledSearch` TS interface and wire up the unused `syncLogSources()`.

2. **Search result-trend endpoint + chart.** `SearchExecutor._store_metrics()` writes
   `SearchResultMetric` rows, but no API exposes them and no chart renders them.
   `frontend/src/app/searches/[id]/page.tsx:104-107` is a literal placeholder. Add a
   `ResultMetricPoint` schema and `GET /searches/{search_id}/results` to
   `backend/app/api/routes/searches.py` (joined to `SearchExecution` to carry `query_version`), plus
   integration tests. Then render it with `echarts-for-react` (already in `package.json`, never
   imported), **annotating query-version boundaries with a `markLine`** — results either side of an
   AQL change are not comparable and the UI must say so.

Section 7 of the handoff has exact step-by-step instructions for both.

**Do this first, before writing any code:** start a real database (`make db-test-up`) and run
`make test-integration`. **75 integration tests — 24 of them newly written — have never been
executed against a database**, because the previous session's environment had no Docker, Podman or
PostgreSQL. They only ever confirmed to collect and skip. Fix whatever surfaces before adding
features. Then run `make check` for migration drift, and
`cd frontend && npm install && npm run typecheck && npm run build` to get a known-good frontend
baseline (`npm` was also unavailable last session).

**Constraints — these are hard:**

- Do not modify the pinned `qradar-mcp/` gitlink (`b8f6a4a3fe901eac4f55e4ca5d146d952f55db51`); keep
  it read-only.
- Never enable MCP `POST` or `DELETE` tools.
- Keep AQL execution on the direct QRadar REST/Ariel provider path — never MCP.
- Never persist raw events; aggregates only.
- Never execute frontend-supplied AQL; only stored, validated, versioned queries.
- Do not weaken, skip or delete existing tests.
- Preserve existing architecture and naming conventions.
- Do **not** run `ruff format` — it is deliberately not part of this project's toolchain and would
  restyle 41 pre-existing files. The gates are `ruff check` and `pytest`.
- `mypy` is advisory (`mypy app || true` in CI) with a known 30-error Phase 1 baseline. Do not
  regress it; you are not required to fix it.
- **Do not begin Phase 3** — no offenses, no MITRE coverage, no LLM implementation.

Section 16 of the handoff lists 21 design decisions that must not be changed. Read it before
altering anything in `app/alerts/`, `app/anomaly/` or `app/services/search_*.py`.

When finished: run every gate (`make lint`, `mypy app`, `make test-integration`, `npm run
typecheck`, `npm run build`), report the **actual** output of each, update `README.md`, and commit as
`feat: complete phase 2 frontend actions and result trends`. Report honestly — if something fails or
could not be run, say so plainly rather than describing it as complete. Then stop at the Phase 2
boundary.

Acceptance criteria are in section 20 of the handoff.
