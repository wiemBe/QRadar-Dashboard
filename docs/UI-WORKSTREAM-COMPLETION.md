# UI Workstream — Completion Report

Final verification record for the behavioral analytics UI workstreams. The UX
contracts these workstreams established are documented in
[Product UX Guidelines](PRODUCT-UX-GUIDELINES.md); this document records that
they were verified, how, and with what result.

---

## 1. Scope completed

| Area | Outcome |
|---|---|
| Navigation and shell | Grouped navigation leading with the behavioral destinations; supporting capabilities demoted to their own labelled group; `aria-current="page"` on exactly one destination |
| Behavior overview | Reduced from ten equally weighted counters to four decision counters, a worklist, recently resolved, and a health distribution |
| Source inventory | New `/behavior/sources` route: searchable, sortable, filterable, server-paginated — moved off the overview |
| Anomaly triage | `/anomalies` reduced from eleven columns to nine; version stamps, duration and confidence moved behind a disclosure |
| Anomaly investigation | `/anomalies/[id]` rebuilt around a deterministic summary, four metrics, timeline, what changed, and tabbed investigation detail |
| Source behavior detail | `/behavior/sources/[id]` rebuilt into the seven-section hierarchy with four primary metrics and a dominant timeline |
| Responsive containment | `html { overflow-x: hidden }` removed and replaced with structural fixes; `.table-scroll` made a containing block |
| Test coverage | Hierarchy, evidence, lifecycle and accessibility regression coverage |
| Product UX guidelines | Canonical normative document for future contributors |

## 2. Commit chain

All commits below are in the current local ancestry.

| Workstream | Commit | Subject | Published |
|---|---|---|---|
| 1 — visual system | `01bcd86` | `refactor(ui): establish behavioral analytics visual system` | yes |
| 2 — overview + inventory | `12b0db8` | `feat(ui): simplify overview and add source inventory` | yes |
| 3 — triage + investigation | `0d6ee14` | `feat(ui): redesign anomaly triage and investigation` | yes |
| 4 — source behavior + layout | `1a76dac` | `feat(ui): refine source behavior and responsive layout` | yes |
| 4 — completion fix | `443d509` | `feat(ui): refine source behavior and responsive layout` | yes |
| 5 — test coverage | `6956a29` | `test(ui): cover hierarchy evidence and accessibility` | yes |
| UX guidelines | `9e6bd25` | `docs(ui): add product UX guidelines` | yes |
| Final verification | `34e056d` | `docs(ui): record final verification and completion` | **no** |

No defect-fix commit was required by this workstream: final verification found
no product defect.

## 3. Product invariants preserved

Each was confirmed in a real browser against the fixture data described in §4,
in addition to the automated suite.

- **Null is not measured zero.** An unmeasured value renders as an em dash. On
  the inventory, an unbaselined source shows `—` for observed and deviation
  while a silent-but-measured source shows `0.00`.
- **COMPLETE zero is a real observation.** The source page reports Observed EPS
  `0.00`, and the chart draws the complete-zero span as a line at zero.
- **MISSING and PARTIAL are not exact zero.** Both are plotted as null with the
  line broken across them, and the chart caption states that gaps are intervals
  that were not fully collected and are not zero traffic.
- **`INSUFFICIENT_DATA` is not healthy.** Its badge is neutral, never green,
  beside a green `NORMAL` in the same table; its expected value reads
  "still learning" rather than `0`.
- **Active states are `CANDIDATE`, `OPEN`, `RECOVERING`.** Derived from the
  lifecycle state only.
- **`resolved_at` is not the active-state definition.** A `NORMAL` row with no
  resolved timestamp appears under recently closed, and an active row is not
  closed by a stray timestamp.
- **TRUNCATED limits what may be claimed.** The capped dimension keeps its rows
  and states "From a result capped at the value limit — the top of the list,
  not the whole of it"; its new and disappeared counts are withheld.
- **UNAVAILABLE and FAILED remain explicit and distinct.** The dimension list
  renders `AVAILABLE`, `TRUNCATED`, `FAILED` and `UNAVAILABLE` as four named
  states, with "2 dimensions were not checked … No conclusion about them
  follows from this investigation."
- **Deterministic summaries do not claim causation.** The rendered investigation
  page contains none of "caused by", "root cause", "attacker", "compromise",
  "malicious" or "because of", and states explicitly that contributor shares are
  "a measured share of the change, not a statement of cause".
- **Contributor headline diversity is preserved.** With the three numerically
  largest contributors deliberately placed in one dimension, the headline still
  reported two dimensions — Event name and Source IP — rather than three source
  IPs.

## 4. Verification environment

- **Data: fixture, not live.** Screenshots and measurements were produced
  against a deterministic fixture API serving the backend's response shapes with
  a pinned clock. No live QRadar data appears in any committed image, and no
  QRadar request was issued during capture.
- **Fixture IDs.** Source `11111111-1111-1111-1111-111111111111`; anomaly
  `aaaaaaaa-0000-0000-0000-000000000000`. The same source id is reused for
  `/log-sources/[id]` in the shared-shell pass.
- **Query parameters.** None required. `/behavior/sources/[id]` was verified at
  its default 6-hour range; the bounded `?range=` control was exercised
  separately by the automated suite.
- **Application URL.** `http://127.0.0.1:3100` (production build), with the
  fixture API on `:8100`. Non-default ports were used so the verification stack
  could not collide with the Compose stack on `:3000`/`:8000`.
- **Browser.** Headless Chrome driven over CDP by a temporary script kept
  outside the repository. No browser dependency, CDP utility, screenshot
  framework or visual-diff infrastructure was added.

## 5. Final viewport matrix

Every principal route was checked at all three viewports; the shared-shell
routes were included at all three at negligible cost. **30 route/viewport
checks, 30 pass.**

Acceptance condition:
`document.documentElement.scrollWidth === document.documentElement.clientWidth`.

For every row below, `document.body.scrollWidth` and `document.body.clientWidth`
were equal to the document values, and computed `overflow-x` was `visible` on
both `html` and `body` — so no defect is concealed by clipping.

### Principal routes

| Route | Viewport | doc scrollWidth | doc clientWidth | Result |
|---|---|---|---|---|
| `/behavior` | 1440×900 | 1440 | 1440 | pass |
| `/behavior` | 1280×800 | 1280 | 1280 | pass |
| `/behavior` | 1024×768 | 1024 | 1024 | pass |
| `/behavior/sources` | 1440×900 | 1440 | 1440 | pass |
| `/behavior/sources` | 1280×800 | 1280 | 1280 | pass |
| `/behavior/sources` | 1024×768 | 1024 | 1024 | pass |
| `/behavior/sources/[id]` | 1440×900 | 1440 | 1440 | pass |
| `/behavior/sources/[id]` | 1280×800 | 1280 | 1280 | pass |
| `/behavior/sources/[id]` | 1024×768 | 1024 | 1024 | pass |
| `/anomalies` | 1440×900 | 1440 | 1440 | pass |
| `/anomalies` | 1280×800 | 1280 | 1280 | pass |
| `/anomalies` | 1024×768 | 1024 | 1024 | pass |
| `/anomalies/[id]` | 1440×900 | 1440 | 1440 | pass |
| `/anomalies/[id]` | 1280×800 | 1280 | 1280 | pass |
| `/anomalies/[id]` | 1024×768 | 1024 | 1024 | pass |

### Shared-shell regression routes

| Route | 1440×900 | 1280×800 | 1024×768 |
|---|---|---|---|
| `/` | pass | pass | pass |
| `/log-sources` | pass | pass | pass |
| `/log-sources/[id]` | pass | pass | pass |
| `/offenses` | pass | pass | pass |
| `/rules` | pass | pass | pass |

## 6. Local table overflow

Local scrolling inside a table's own container is the intended mechanism and is
**not** page-level overflow. Every `.table-scroll` container reported
`position: relative`, so the containment fix is intact.

| Viewport | Route | Scroll region | scrollWidth | clientWidth |
|---|---|---|---|---|
| 1280×800 | `/anomalies` | Detected anomalies | 1115 | 984 |
| 1024×768 | `/anomalies` | Detected anomalies | 1115 | 780 |
| 1024×768 | `/behavior` | Sources needing attention | 875 | 780 |
| 1024×768 | `/behavior/sources` | Monitored log sources | 857 | 780 |
| 1024×768 | `/behavior/sources/[id]` | Active anomalies | 787 | 780 |
| 1024×768 | `/behavior/sources/[id]` | Recently closed anomalies | 821 | 780 |

At 1440×900 no table needed to scroll. The anomaly contributor table fitted its
container at every viewport.

## 7. Screenshot index

Eleven screenshots in [`screenshots/ui/`](screenshots/ui/). Each is a
viewport-sized capture with no browser chrome, so its pixel dimensions match its
filename.

### Desktop reference — 1440×900

| Screenshot | What it shows |
|---|---|
| [behavior-1440x900.png](screenshots/ui/behavior-1440x900.png) | Four decision counters, worklist, no source inventory on the overview |
| [behavior-sources-1440x900.png](screenshots/ui/behavior-sources-1440x900.png) | Inventory with filter row on one line and the table unscrolled |
| [behavior-source-detail-1440x900.png](screenshots/ui/behavior-source-detail-1440x900.png) | Compact header, exactly four metrics, dominant timeline; measured zero as `0.00` and the uncollected span drawn as a gap |
| [anomalies-1440x900.png](screenshots/ui/anomalies-1440x900.png) | Triage table with detector label and severity badge separated, statuses in text |
| [anomaly-detail-1440x900.png](screenshots/ui/anomaly-detail-1440x900.png) | Deterministic summary, four metrics, one contributor table, technical detail behind tabs |

### Responsive proof — 1024×768

| Screenshot | What it proves |
|---|---|
| [behavior-sources-1024x768.png](screenshots/ui/behavior-sources-1024x768.png) | Filter row wraps rather than pushing the button off; long source name wraps in its cell; table scrolls locally while the page does not; `INSUFFICIENT_DATA` neutral beside a green `NORMAL` |
| [behavior-source-detail-1024x768.png](screenshots/ui/behavior-source-detail-1024x768.png) | Four-metric grid reflows and the chart stays inside its container at the narrowest supported width |
| [anomalies-1024x768.png](screenshots/ui/anomalies-1024x768.png) | Nine-column triage table scrolls locally; all four severity badges stay content-sized and unclipped |
| [anomaly-detail-1024x768.png](screenshots/ui/anomaly-detail-1024x768.png) | Metrics reflow to two columns; a still-running incident shows an em-dash duration, never `0s` |

### Intermediate width — 1280×800

| Screenshot | What it proves |
|---|---|
| [behavior-source-detail-1280x800.png](screenshots/ui/behavior-source-detail-1280x800.png) | The four-metric row and chart hold their desktop shape at the intermediate width |
| [anomaly-detail-1280x800.png](screenshots/ui/anomaly-detail-1280x800.png) | Investigation hierarchy unchanged between 1440 and 1024 |

## 8. Frontend gates

Run from `frontend/` against the exact working tree, using the scripts defined
in `package.json`.

| Gate | Command | Result |
|---|---|---|
| Tests | `npm test -- --run` | **629 passed, 31 files** — 0 failed, 0 skipped |
| Lint | `npm run lint` | No ESLint warnings or errors |
| Types | `npm run typecheck` | `tsc --noEmit` clean |
| Build | `npm run build` | Compiled successfully; 16/16 static pages generated |

Backend tests were not run: no backend file changed in any of these workstreams.

## 9. Compose health

Command: `docker compose up -d --build`, then `docker compose ps`.

Expected seven services — six long-running plus the one-shot migration.
`qradar-mcp` is gated behind the `mcp` profile and is not part of the default
set.

| Service | State |
|---|---|
| `postgres` | Up (healthy) |
| `redis` | Up (healthy) |
| `backend` | Up (healthy) — published on `127.0.0.1:8000` |
| `celery-worker` | Up (healthy) |
| `celery-beat` | Up (healthy) |
| `frontend` | Up (healthy) — published on `127.0.0.1:3000` |
| `migrate` | Exited (0) |

- Backend `/api/v1/health/live` → `200`; `/api/v1/health/ready` → `200`
  (`{"status":"ready","database":"ok"}`).
- Frontend `/api/health` → `200`; `/behavior`, `/behavior/sources` and
  `/anomalies` each → `200`.
- Migration state accepted by the running application: Alembic upgraded to
  revision `0004`.
- Restart count `0` for every service — nothing is crash-looping.
- No task failure, error or traceback in the backend or Celery logs during the
  verification window.

No volume was removed, no database reset, no prune was run, and no QRadar write
was issued.

## 10. Known non-blocking items

- **Screenshots are fixture-based, not live.** This is deliberate — it makes the
  images reproducible and keeps live telemetry out of the repository. Live
  behavior is recorded separately in the lab documents.
- **Dependency advisories** remain outstanding and require major upgrades. They
  were deliberately not touched; `npm audit fix --force` was not run.
- **`next lint` deprecation.** Next.js reports that `next lint` is removed in
  Next 16 and suggests migrating to the ESLint CLI. The gate passes today.
- **Phase B (WAF) and later phases** are not started; the roadmap in the
  architecture document is unchanged.
- **Four commits remain unpushed** (§11).

## 11. Publication state

- Local `HEAD`: `34e056d`
- `origin/main`: `9e6bd25`
- Unpushed: `34e056d` only — the final verification commit.
- **No push was performed by this workstream**, and no commit was amended,
  rebased, squashed or force-pushed.

`origin/main` stood at `1a76dac` when this workstream began and at `9e6bd25`
when it ended. The three intervening commits — `443d509`, `6956a29` and
`9e6bd25` — were published by a push issued outside this workstream while it was
running; the ref log records it as `update by push`. The commits are unchanged
by it, and the verification recorded here was performed against exactly that
tree.
