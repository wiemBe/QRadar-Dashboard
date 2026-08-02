# Phase A — Log-Source Volume Anomaly and Explainable Investigation

Implementation reference for the first behavioral detector. Concepts and the
long-term roadmap live in [BEHAVIORAL-ANALYTICS-ARCHITECTURE.md](./BEHAVIORAL-ANALYTICS-ARCHITECTURE.md).

Status: **complete and live-validated.** Spike, drop, multi-source isolation and
NO_EVENTS have all been observed end to end against a real QRadar appliance —
see §11 and `docs/LAB-DEMO-RESULTS.md`.

---

## 1. Flow

```
Ariel bounded aggregate  ->  metric bucket  ->  seasonal baseline
   ->  spike/drop/silence detection  ->  anomaly lifecycle
   ->  bounded explanation evidence  ->  contributor comparison  ->  API
```

## 2. Metric buckets

`log_source_metric`, a TimescaleDB hypertable keyed `(log_source_id, bucket_start)`.

| Field | Meaning |
|---|---|
| `bucket_start` / `bucket_seconds` | Half-open UTC interval `[start, start+seconds)` |
| `event_count`, `average_eps`, `peak_eps` | Observed volume |
| `first_event_at`, `last_event_at` | Bounds of observed traffic |
| `completeness` | `COMPLETE` / `PARTIAL` / `MISSING` |
| `collection_source` | `ariel_aggregate` or `mock` |
| `query_provenance` | AQL shape, window, row counts. Non-secret |
| `collection_duration_ms`, `collected_at`, `watermark_at` | Collection provenance |

Bucket identity is `floor(epoch / bucket_seconds) * bucket_seconds`, so every
worker agrees which bucket an observation belongs to. Upserts on the composite
key make re-collection idempotent.

**Only `COMPLETE` buckets enter a baseline or produce a verdict.** A partially
collected interval is indistinguishable from a volume drop; treating one as a
real observation turns a collector outage into a fleet-wide false alarm.

Collection is guarded by a per-instance advisory lock, resumes from a watermark,
and is capped by `COLLECTION_MAX_BACKFILL_INTERVALS`. The watermark advances only
past intervals that completed.

## 3. Baseline

`log_source_baseline`, one row per `(log_source, metric, weekday, hour)` —
**168 seasonal cells**, ISO weekday 1–7 × hour 0–23.

Median and MAD, not mean and standard deviation: SIEM volume is heavy-tailed and
one incident would inflate a standard deviation enough to blind the detector for
weeks. p05/p95 are stored as the displayed expected band.

**Excluded from every cell:** maintenance windows, off-hours buckets on
business-hours-only sources, buckets inside a known anomaly span, `PARTIAL`/
`MISSING` buckets, and the current unfinished bucket. Exclusions are counted by
reason in `exclusion_counts`, and `completeness` records the surviving fraction —
a cell built from 4 of 28 buckets is far weaker than one built from 26 even when
both clear the sample minimum.

Below `BASELINE_MIN_SAMPLES` a cell is stored, marked `is_reliable=False`, and
**must not drive a verdict**. The detector then returns `INSUFFICIENT_DATA`, never
a misleading `NORMAL`.

## 4. Detection

### 4.1 Spike / drop

Conjunctive. Every guard must pass:

```
adequate baseline
AND complete metric bucket
AND volume above the source minimum
AND ratio guard satisfied
AND absolute-delta guard satisfied
AND ( robust z-score threshold satisfied
      OR robust score is degenerate (MAD = 0)
         AND the deterministic expected-bound fallback is satisfied )
AND required consecutive buckets satisfied
```

The **absolute-delta and minimum-volume guards** exist to kill the classic false
positive where 0.2 EPS becomes 0.4 EPS: a clean 2× ratio and a large robust
z-score on a change of no operational significance.

### 4.2 Zero MAD

MAD is exactly zero whenever more than half the retained samples are identical —
the normal case for a steady low-volume source. The scale is floored at
`max(|median| × BASELINE_MAD_FLOOR_RATIO, ε)`, so nothing divides by zero.

Crucially, a degenerate score is **not** read as evidence of normality. Doing so
would let a perfectly steady baseline suppress a real, material deviation that
has already cleared the volume, ratio and delta guards. The detector instead
falls back to the p05/p95 expected band, records
`robust_score_status=DEGENERATE`, and caps confidence at 0.7.

### 4.3 Silence

`NO_EVENTS` fires only when the bucket is `COMPLETE`, the source is on-schedule,
its seasonal cell has an adequate baseline expecting traffic, and the empty
streak exceeds `ANOMALY_SILENCE_GRACE_BUCKETS`. A normally-idle hour, an
off-schedule hour and an incomplete bucket each yield healthy or
`INSUFFICIENT_DATA` — never a false outage.

## 5. Lifecycle

`INSUFFICIENT_DATA → NORMAL → CANDIDATE → OPEN → RECOVERING → RESOLVED`, plus
`SUPPRESSED` from any state.

- One abnormal bucket creates `CANDIDATE` (persisted, not yet an alert).
- `ANOMALY_OPEN_AFTER_INTERVALS` consecutive abnormal buckets promote to `OPEN`.
- A `CANDIDATE` that recovers returns to `NORMAL` — it was never an incident.
- One normal bucket after `OPEN` moves to `RECOVERING`; a relapse reopens the
  **same** incident.
- `ANOMALY_RESOLVE_AFTER_INTERVALS` consecutive normal buckets resolve it.
- **An UNKNOWN or incomplete bucket holds state.** Missing data is not recovery,
  and a collection failure can never resolve an anomaly.
- One active incident per `(log source, detector type)`. A recurrence after
  `RESOLVED` opens a **new** incident, so durations stay meaningful.

Confirmation and recovery counts resolve global → per-criticality → per-source.
The per-criticality matrix (CRITICAL opens after 1, LOW after 3) overrides the
global default in production.

Every transition writes an `anomaly_state_transition` row with from-state,
to-state, reason, triggering bucket and actor.

## 6. Explanation evidence

When a spike or drop reaches `OPEN`, `evidence_status` becomes `PENDING` and the
`collect_anomaly_explanations` task builds a bounded package.

**Windows.** The anomaly window is the abnormal telemetry itself, clamped to
`EXPLANATION_MAX_WINDOW_SECONDS`. The comparison window ends exactly where the
anomaly begins and is `EXPLANATION_BASELINE_WINDOW_MULTIPLE` times as long.
Counts are compared on a **rate basis**; comparing a 10-minute anomaly against a
30-minute baseline by raw count would show every dimension collapsing.

**Dimensions.** `qid`, `event_name`, `source_ip`, `destination_ip`,
`destination_port`, `source_port`, `username`, `action`, `category`, `protocol`.
One bounded aggregate Ariel query per dimension per window, `GROUP BY … ORDER BY
COUNT(*) DESC LIMIT n`. No raw events are retrieved.

**Per contributor:** baseline count, anomaly count, absolute delta, percent
delta, anomaly share, baseline share, signed contribution share, both ranks,
new/disappeared flags.

**Per dimension:** distinct counts, cardinality ratio, top-value concentration
before and after, new and disappeared counts.

### 6.1 Honesty rules (enforced by tests)

| Situation | Recorded as | Never |
|---|---|---|
| Value absent from baseline | `percent_delta = null`, `is_new = true` | A fabricated percentage |
| Baseline cardinality 0 | `cardinality_ratio = null` | Infinity, or 0 |
| Field absent from the DSM | dimension row with `UNAVAILABLE` | Omitted, or a count of 0 |
| Query failed | dimension row with `FAILED` + sanitized reason | `UNAVAILABLE` |
| Result hit the cap | `TRUNCATED`, `truncated = true` | Silently presented as complete |

Package status: `COMPLETE` (all dimensions usable, untruncated), `PARTIAL` (some
unusable or truncated), `UNAVAILABLE` (none usable), `FAILED`, `PENDING`,
`NOT_REQUESTED`.

The engine reports **what changed**, never why. Permitted wording: "largest
observed contributor", "largest volume change", "behavior is consistent with",
"evidence suggests". **No LLM is involved in Phase A.**

Never stored: raw events, payloads, credentials, tokens, request headers.

## 7. Configuration

Production defaults are unchanged from Phase 3 where they existed.

| Setting | Default | Notes |
|---|---|---|
| `COLLECTION_INTERVAL_SECONDS` | 300 | 60 under LAB_MODE |
| `BASELINE_MIN_SAMPLES` | 8 | 4 under LAB_MODE |
| `BASELINE_LOOKBACK_DAYS` | 28 | |
| `BASELINE_MAD_FLOOR_RATIO` | 0.1 | Degenerate-scale floor |
| `ANOMALY_DEVIATION_THRESHOLD` | **3.5** | Preserved; the spec's 4.0 was not adopted |
| `ANOMALY_SPIKE_RATIO` | 2.0 | New, conjunctive |
| `ANOMALY_DROP_RATIO` | 0.5 | New, conjunctive |
| `ANOMALY_MIN_ABSOLUTE_DELTA_EVENTS` | 100 | New; kills the 0.2→0.4 EPS case |
| `ANOMALY_MIN_BUCKET_EVENTS` | 50 | New |
| `ANOMALY_OPEN_AFTER_INTERVALS` | 2 | 1 under LAB_MODE |
| `ANOMALY_RESOLVE_AFTER_INTERVALS` | 3 | 2 under LAB_MODE |
| `ANOMALY_SILENCE_GRACE_BUCKETS` | 1 | |
| `ANOMALY_POLICY_VERSION` | 1 | Stamped on every anomaly |
| `EXPLANATION_ENABLED` | true | |
| `EXPLANATION_TOP_VALUES` | 20 | |
| `EXPLANATION_BASELINE_WINDOW_MULTIPLE` | 3.0 | |
| `EXPLANATION_MAX_WINDOW_SECONDS` | 21600 | 6h ceiling |
| `EXPLANATION_MAX_PER_RUN` | 5 | Bounds Ariel load |
| `EXPLANATION_COLLECTION_INTERVAL_SECONDS` | 300 | |

### LAB_MODE

Opt-in, refused in production. It may shorten **timing only**: bucket interval,
minimum sample count, confirmation count, recovery count, collection schedule.

**It never contains a detection threshold.** A lab that demonstrates a detector
production does not run is worse than no lab. The documented 2 → 6 EPS
acceptance scenario clears the production guards on merit: at 60s buckets that is
120 → 360 events, a delta of 240 against a floor of 100.

## 8. API

All routes are read-only, authenticated behind `read:anomalies` (satisfied by the
`read:*` wildcard), paginated and range-bounded by `API_MAX_RANGE_DAYS`.

| Route | Purpose |
|---|---|
| `GET /api/v1/anomalies` | Paged list; filters: `log_source_id`, `instance_id`, `anomaly_type`, `state`, `severity`, `evidence_status`, `active_only`, `since`, `until` |
| `GET /api/v1/anomalies/summary` | Open/spike/drop/silence counts, insufficient-data sources, pending/failed evidence, recently resolved, highest deviation |
| `GET /api/v1/anomalies/{id}` | Investigation detail: measurements, `detection` block, lifecycle history, evidence package, contributors by dimension |
| `GET /api/v1/behavior/sources` | Observed vs expected EPS, expected band, deviation ratio, state per source |
| `GET /api/v1/behavior/sources/{id}` | Same, one source |
| `GET /api/v1/behavior/sources/{id}/metrics` | Bucket history with completeness |
| `GET /api/v1/behavior/sources/{id}/baselines` | Seasonal cells with reliability and version |

**No QRadar mutation endpoint exists, and none may be added.** A test asserts
every route under `/anomalies` and `/behavior` accepts only `GET`/`HEAD`/`OPTIONS`.

List items carry `log_source_name` and a serialized `duration_seconds`. The
latter is a computed field rather than a plain property so it is actually
emitted: the still-running case (`anomaly_end` absent) is exactly where an
independent client reimplementation substitutes `now` and reports a duration
that grows on every page refresh. It is `null` until an end exists.

### 8.1 The `detection` block

`GET /api/v1/anomalies/{id}` carries a typed `detection` object: the expected
band, the robust-z threshold that had to be cleared, the baseline sample count
and completeness, the observed/expected EPS and event counts, the ratio and its
basis, and the robust-score status with its fallback bound.

Without these the verdict is an assertion the analyst cannot check. They are
projected from the detector's `details` JSONB by an **explicit whitelist**, not
serialized wholesale: that column is a detector-owned payload whose keys vary
per detector, and forwarding it verbatim would make every future internal fact a
silent, unreviewed addition to a public response. The projection also refuses to
coerce a bool into a numeric field (`bool` subclasses `int` in Python) and drops
an unrecognized `robust_score_status` rather than defaulting it to `OK`, so a
detector bug cannot reach the UI disguised as a measurement.

## 9. Background tasks

| Task | Cadence | Lock |
|---|---|---|
| `collect_metrics` | `COLLECTION_INTERVAL_SECONDS` | per-instance advisory |
| `evaluate_anomalies` | `COLLECTION_INTERVAL_SECONDS` | — |
| `rebuild_baselines` | daily | — |
| `collect_anomaly_explanations` | `EXPLANATION_COLLECTION_INTERVAL_SECONDS` | per-instance advisory |

14 registered tasks, 14 beat schedules.

Explanation collection is deliberately decoupled from detection: an unresponsive
appliance must delay evidence, never the alert that something is wrong.

## 10. Frontend

| Route | Purpose |
|---|---|
| `/behavior` | Fleet posture, source behavior table, highest deviation, recently resolved |
| `/behavior/sources/[id]` | One source's observed volume against its seasonal band, with anomaly overlay |
| `/anomalies` | Filterable, server-paginated list |
| `/anomalies/[id]` | Investigation detail |

Pages are server components; the API client is the existing `src/lib/api.ts`
(one `fetch` wrapper, no second request library). Presentation honesty rules
live in `src/lib/behavior.ts`, series construction in `src/lib/timeseries.ts`,
and query-parameter validation in `src/lib/anomalyQuery.ts` — each unit-tested
independently of the pages.

### 10.1 Investigation sections

Detection summary · timeline · lifecycle history · evidence completeness ·
contributor dimensions · dimension summary · query provenance.

### 10.2 Unavailable dimensions

Rendered explicitly, never hidden:

> Unavailable — this field was not exposed by the QRadar event schema or DSM for
> the selected interval.

A hidden unavailable dimension reads to an analyst as one that was checked and
found clean, which inverts the truth. Its `new_value_count` and
`disappeared_value_count` render as em dashes rather than the backend's default
zeroes, and the dimension summary names every unchecked dimension in one place
so the page's coverage is legible at a glance.

### 10.3 Evidence completeness rendering

All six states render with their operational meaning. Four of them —
`NOT_REQUESTED`, `PENDING`, `UNAVAILABLE`, `FAILED` — produce a page with no
contributors, visually identical to "we looked and nothing stood out", so the
stated status is the only thing distinguishing a queued job from a failed one
from a source whose DSM emits nothing. `COMPLETE` is the only state toned as
good news; `NOT_REQUESTED` and `UNAVAILABLE` are neutral rather than green.

### 10.4 Zero is not missing

`formatMetric` and its siblings render a measured `0` as `0` and an unmeasured
`null` as an em dash. In charts, a `PARTIAL` or `MISSING` bucket is plotted as
`null` with `connectNulls: false`, and an interval with no stored bucket gets an
explicit null point — a line drawn across an uncollected hour asserts traffic
nobody observed and turns a collector outage into an apparent source outage. An
unbaselined source gets no expected line at all, because a flat line at 0 would
invent an expectation and make any traffic look like a spike against it.

## 11. Status and remaining work

**Done and gated:** models, migration `0004`, baseline extension, detector
guards, lifecycle, Ariel dimension aggregation, explanation analysis and
persistence, Celery task, beat wiring, API, the `detection` projection, the four
frontend routes, and tests on both sides.

Gates (2026-08-02, after live validation): Ruff clean · Mypy 31 errors in 18
files (unchanged baseline — no new errors) · **1162 backend tests passed, 0
failed** (852 unit + 310 integration) · **280 frontend tests passed** · frontend
lint, typecheck and production build clean · `alembic upgrade head` +
`alembic check` clean at revision `0004` · full Compose stack healthy.

**Live validation is complete.** Spike, drop, multi-source isolation and
NO_EVENTS all passed against the real appliance; see `docs/LAB-DEMO-RESULTS.md`
for run IDs, actual lifecycle timestamps and evidence limitations.

Live validation found and fixed four defects that automated tests had missed,
each with regression coverage that fails without the fix:

| Commit | Defect |
|---|---|
| `dc9aa34` | Ariel metric and contributor searches carried no time range, so any query for a past interval silently returned zero. |
| `404bc39` | Truncated dimensions rendered new/disappeared counts as definitive findings. |
| `ec5d352` | `resolved_at IS NULL` used as the active-incident predicate. |
| `57ccd19` | Silence left no metric row at all, making NO_EVENTS unreachable in production. |
| `4892f8e` | Baseline exclusion spans bounded by `resolved_at`, deadlocking an open incident's own recovery. |

Three of these share one root cause: reading `resolved_at` as a lifecycle
signal. See §11.2.

**Remaining follow-ups (none blocking):**

1. **Watermark advancement is last-write-wins.** See §11.1.
2. **Active-incident predicates** — see §11.2 for the invariant to preserve.
3. **Intermittent `MissingGreenlet` in `collect_metrics`** — a connection-pool
   ping outside greenlet context. Self-heals, because the watermark only
   advances on success, but it burns a collection cycle. Seen 3 times on
   2026-08-02.
4. **Local-development auth privilege asymmetry** — see §11.3.

### 11.1 Follow-up: contended watermark advancement

`CollectionWatermark.watermark_at` is read into the collector's session at the
start of a run and written back at the end. Any concurrent write to that row —
including an operator correcting a watermark — is silently overwritten when the
in-flight run flushes. Observed live on 2026-08-02: a manual reset was clobbered
twice by in-flight `collect_metrics` runs before it took effect.

The per-instance advisory lock prevents two collectors from overlapping, but it
does not protect the row against a writer that never takes the lock, so this is
a real operational limitation rather than a theoretical race.

Not fixed here: correcting it is a change to the collection contract and does
not belong inside live Phase A validation. One of the following should be
chosen as a scoped follow-up:

- compare-and-set advancement (`UPDATE … WHERE watermark_at = <observed>`);
- `SELECT … FOR UPDATE` row-level locking around read-modify-write;
- a generation/version column with an optimistic-concurrency check;
- a maintenance mode that pauses collectors before administrative edits;
- an administrative reset command that acquires the same collection lock.

Until one exists, a watermark must never be reset while collection tasks are
active. The required manual procedure is: pause the Beat schedule or worker
consumption, wait for in-flight collection to drain, perform the reset, resume
collection, then verify the next window starts where expected.

### 11.2 Invariant: an active incident is defined by state

An incident is active while its state is `CANDIDATE`, `OPEN` or `RECOVERING` —
the `ACTIVE_ANOMALY_STATES` set. **`resolved_at IS NULL` is not a valid test.**

A `CANDIDATE` that returns to normal before opening never opened, so it was
never resolved: it ends in state `NORMAL` with `resolved_at` NULL permanently.
That shape is correct and expected, and was observed live on 2026-08-02 on
source 227 after an idle gap between scenarios.

Reading `resolved_at` as a lifecycle signal caused three separate live defects:
inflated active counts (`ec5d352`), and baseline exclusion spans that never
closed, which deadlocked an open incident's own recovery (`4892f8e`). When
adding any query that asks "is this incident live?", use the state set.

### 11.3 Follow-up: local-development auth privilege asymmetry

With `AUTH_PROVIDER=local` in a non-production environment, a request with **no**
bearer token resolves to an `admin:*` principal, while a request carrying an
arbitrary bearer value resolves to `read:*`. An unauthenticated caller is
therefore *more* privileged than a token-bearing one.

This is refused outright when `is_production` is true, and it did not affect
Phase A validation — the live API checks deliberately used the bearer path, the
less privileged of the two. It must nonetheless be reviewed before any
non-development deployment.
