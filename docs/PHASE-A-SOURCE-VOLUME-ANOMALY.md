# Phase A — Log-Source Volume Anomaly and Explainable Investigation

Implementation reference for the first behavioral detector. Concepts and the
long-term roadmap live in [BEHAVIORAL-ANALYTICS-ARCHITECTURE.md](./BEHAVIORAL-ANALYTICS-ARCHITECTURE.md).

Status: **backend complete and gated.** Frontend, generator scenarios and live
QRadar validation are the remaining work — see §10.

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
| `GET /api/v1/anomalies` | Paged list; filters: `log_source_id`, `anomaly_type`, `state`, `severity`, `active_only`, `since`, `until` |
| `GET /api/v1/anomalies/summary` | Open/spike/drop/silence counts, insufficient-data sources, recently resolved, highest deviation |
| `GET /api/v1/anomalies/{id}` | Investigation detail: measurements, lifecycle history, evidence package, contributors by dimension |
| `GET /api/v1/behavior/sources` | Observed vs expected EPS, expected band, deviation ratio, state per source |
| `GET /api/v1/behavior/sources/{id}` | Same, one source |
| `GET /api/v1/behavior/sources/{id}/metrics` | Bucket history with completeness |
| `GET /api/v1/behavior/sources/{id}/baselines` | Seasonal cells with reliability and version |

**No QRadar mutation endpoint exists, and none may be added.** A test asserts
every route under `/anomalies` and `/behavior` accepts only `GET`/`HEAD`/`OPTIONS`.

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

## 10. Status and remaining work

**Done and gated:** models, migration `0004`, baseline extension, detector
guards, lifecycle, Ariel dimension aggregation, explanation analysis and
persistence, Celery task, beat wiring, API, tests.

Gates: Ruff clean · Mypy 31 errors in 18 files (baseline 32/18 — no new errors) ·
**1060 tests passed, 0 failed, 0 skipped** · `alembic upgrade head` + `alembic
check` clean on a fresh database.

**Remaining:**

1. **Frontend** — behavioral overview, source behavior page with expected band,
   anomaly list, and the investigation detail page.
2. **Generator scenarios** — the eight Phase A scenarios and their flags in
   `tools/qradar_lab_loggen.py`.
3. **Live QRadar validation** — baseline / spike / investigation / recovery /
   drop / silence against the isolated lab. **Not yet performed.** All results
   reported so far come from automated tests against the mock provider and a
   real PostgreSQL/TimescaleDB, not from live QRadar telemetry.
