# Phase 3.5 Lab Demo Results

**Status:** automated and bounded live validation complete on 2026-08-01. This file contains no
credential, SEC header, raw event payload, or production identity.

## Environment

- QRadar: 7.6.0 FP1 (`7.6.0.0`), API 29.0
- QRadar access from the application: read-only REST/Ariel with pinned-certificate TLS verification
- Syslog target: isolated lab listener on UDP/514
- Generator: `tools/qradar_lab_loggen.py`, default Universal LEEF, stable synthetic identities
- Dashboard routes: `/anomalies`, `/alerts`, `/offenses`, `/rules`, `/coverage`

## Automated evidence

- Deterministic generation, stable source identity, required LEEF fields, vendor payloads, Linux
  tag regression, count/duration bounds, invalid argument/rate rejection, dry-run and file output.
- UDP and TCP/reconnect tests use local ephemeral loopback receivers; tests never send to QRadar.
- Correlation tests cover brute-force, password spray, failure→success, port scan, repeated payload,
  malformed parsing payload, timestamp delay, and fixed source controls.
- LAB_MODE tests prove production defaults remain intact, lab values are exact, and production
  activation is rejected.

## Bounded live commands and results

All addresses below are documentation-only RFC 5737 addresses. The initial run used the requested
1 EPS and 20 events. Later scenarios remained at or below 20 EPS, below the 50 EPS initial-live
limit and far below the generator's protected 100 EPS boundary. Raw generated logs are not
committed.

| Scenario | Sanitized command parameters | Sent | QRadar arrived/routed | Application result |
|---|---|---:|---|---|
| Initial LEEF | `ips`, seed 42, 1 EPS; then five marker events at 1 EPS | 25 | `ips-gw-01` route; included in exact IPS total | metric bucket collected |
| Normal baseline | `ips`, seed 5101, 5 EPS, fixed `203.0.113.101` | 20 | `ips-gw-01`; aggregate arrival confirmed | metric bucket collected |
| Volume spike | `ips`, seed 5102, 20 EPS, fixed `203.0.113.102` | 40 | `ips-gw-01`; aggregate arrival confirmed | latest bucket changed; no reliable baseline yet |
| Authentication | failure-to-success, seed 5103, 5 EPS, eight failures | 9 | `DC-LAB-01`; 9/9 occurrences | no generated offense in bounded window |
| WAF SQLi | SQL injection burst, seed 5104, 5 EPS | 12 | `waf-prod-01`; 12/12 occurrences | metric bucket collected |
| Repeated payload | `ips`, seed 5105, 5 EPS | 10 | `ips-gw-01`; aggregate arrival confirmed | coalesced by QRadar; no reliable baseline yet |
| Parsing degradation | valid envelope/incomplete body, seed 5106, 1 EPS | 5 | `ips-gw-01`; aggregate arrival confirmed | malformed body not exposed as an application ratio |
| Silence | generator stopped after the bounded runs | 0 | no further generator traffic | `NO_EVENTS` not attributable on shared source |

The read-only Ariel proof searched only payloads containing the generator-specific
`SyntheticLab` signature. QRadar returned exactly **121 occurrences**: 100 routed to
`McAfee Network Security Platform @ ips-gw-01`, 9 to `DC-LAB-01@windows`, and 12 to
`F5FirePass @ waf-prod-01`. QRadar coalesced the 100 IPS occurrences into eight Ariel records;
therefore `SUM(eventcount)`, not raw `COUNT(*)`, is the accurate arrival check.

One marker search also confirmed normalized source and destination addresses. The selected McAfee
DSM did not normalize `usrName`; the returned username was null.

## Application collection results

The Phase 3 collectors were run once through the existing manual sync entrypoint after arrival.
Results were:

- 36 log sources seen and updated; none created.
- One completed five-minute metric interval collected, with five source samples.
- 14 stored offense snapshots aggregated; the live offense endpoint returned no new offense.
- 352 rules and 219 building blocks synchronized.
- Two RuleMetric buckets written from 14 stored offense contributions. Provenance remained
  `offense_contribution` and completeness remained `incomplete`.
- 133 rules evaluated: 1 `HEALTHY`, 94 `INSUFFICIENT_DATA`, and 38 `DISABLED`.
- Detection coverage evaluated zero techniques because no SOC-owned technique mappings exist.
- Anomaly evaluation processed all 36 monitored sources and opened 0 / resolved 0 alerts.

Baseline rebuild produced 124 cells, but zero reliable cells. The largest cell had seven samples;
the unchanged production minimum is eight. Consequently no honest volume, repeated-payload,
parsing-degradation, or silence anomaly could open during this bounded session, and there was no
alert to resolve. No database row was fabricated to simulate those lifecycle transitions.

## Manually configured QRadar objects

No QRadar object was created or modified during this session. The emitted hostnames matched
pre-existing routes for `ips-gw-01`, `waf-prod-01`, and `DC-LAB-01`; the other documented lab
sources still require operator/UI setup and validation. No rule was created through the API.

## Parser limitations and remaining gaps

- QRadar coalesces identical events. Arrival accounting must sum Ariel `eventcount`; raw record
  counts understate the true volume.
- The current log-source metric AQL uses raw record count, so its event count/EPS under-reports
  coalesced generator traffic. Correcting that Phase 3 collector behavior is deliberately left as
  a separately reviewed follow-up rather than silently changing the completed Phase 3 contract.
- The McAfee route parsed source/destination addresses but not the LEEF `usrName` field.
- The current metric provider does not populate parsed-field ratios, distinct counts, or payload
  signatures from live QRadar. Parsing-degradation and repeated-payload evidence therefore cannot
  be demonstrated end to end yet.
- `ips-gw-01`, `waf-prod-01`, and `DC-LAB-01` receive other lab traffic. Stopping this generator
  alone cannot prove source-level `NO_EVENTS`; use an isolated manually configured source.
- A full OPEN-to-RESOLVED anomaly demo needs at least eight production samples, or a deliberately
  isolated stack started with documented `LAB_MODE=true` long enough to build four lab samples.
- No bounded scenario generated a QRadar offense. Existing offense history, top-entity views,
  contributing-rule evidence, and RuleMetric provenance remain populated by the 14 stored live
  offense snapshots.

# Phase A live validation (2026-08-02)

**Status:** in progress. This section records only verified live facts.

## Live log source

| Property | Value |
|---|---|
| QRadar log-source ID | 227 |
| Name | LAB Phase A Firewall |
| Identifier | `lab-fw-volume-01` |
| Parser type | **Netgate pfSense** (native parsing; not Universal LEEF) |
| Event collector | 7 |
| Enabled | true |

## Ingestion smoke test

Two bounded runs were sent before any scenario:

| Run ID | Format | Attempted | Sent | Ariel |
|---|---|---:|---:|---|
| `labrun-smoke-20260802T122807Z` | leef | 20 | 20 | — |
| `labrun-smoke2-20260802T124705Z` | pfsense | 20 | 20 | 20 in the 12:47Z minute, all routed to source 227 |

Read-only Ariel confirms source 227 received exactly **20 events**, all within `12:47Z`, with
`sourceip` parsed natively (top value `203.0.113.50`, 6 events).

## Defect found and fixed: unscoped Ariel searches

The scheduled baseline-spike-recovery run did **not** execute — no manifest, no process, and Ariel
shows no source-227 traffic outside the 12:47Z smoke minute. While confirming that, a blocking
defect was found in the Ariel query builders.

Both `get_log_source_metrics` and `get_dimension_aggregates` bounded their window with a `WHERE`
clause on `starttime` alone and carried no explicit Ariel time range. QRadar scopes such a search to
a recent default window and intersects it with the `WHERE` clause, so any query for an interval
older than that default returns zero rows.

Measured on the lab appliance, identical query, window `11:50Z–17:40Z`:

| Query form | Result |
|---|---|
| `WHERE starttime >= .. AND starttime < ..` only | **0 rows, 0 total** |
| same query plus `START <ms> STOP <ms>` | **28 log sources**, including `{logsourceid: 227, count: 20}` |

The failure is silent and directionally wrong:

- metric collection recorded an uncollected backlog as *no events*, which the detector cannot
  distinguish from a source that genuinely fell silent;
- contributor evidence for a past anomaly interval — always the Phase A case, since an interval is
  explained after it ends — returned empty rather than `UNAVAILABLE`, so an anomaly would be
  reported as unexplained instead of unexplainable.

Fixed in `dc9aa34` by appending `START <ms> STOP <ms>` to both builders. The `WHERE` clause remains
authoritative for the half-open bucket bound; `START/STOP` only scopes the search. Bucket boundary
semantics were verified live to be exact: the `12:47–12:48Z` bucket returns 20 events for source
227 and the adjacent `12:48–12:49Z` bucket returns 0.

Two regression tests were added and both fail against the pre-fix builders. Post-fix, the running
worker returns 12 log-source samples for a 4.5-hour-old bucket, including source 227 at 20 events.

## Collection gap, explained

Metric buckets existed only for `12:48Z–12:54Z`. This had two independent causes, both now
understood:

1. the unscoped-search defect above, which silently zeroed every historical bucket; and
2. a real lab outage — the host suspended, leaving a Celery beat gap from `15:47:10Z` to
   `17:33:44Z`. Read-only Ariel confirms **zero events across all sources** in `16:35Z–16:48Z`, so
   the collector's `samples: 0` for that span was correct rather than defective.

Background lab traffic resumed at roughly `17:33Z` and is flowing at about 8,300 events/minute
across other log sources. Source 227 remains at zero outside the smoke minute, so it is clean for
scenario work.

The `12:55Z–17:33Z` gap is a **real host-suspension interval**, not a collection failure and not a
symptom of the query-builder defect. The two are independent and must not be conflated: the defect
zeroed historical buckets that did contain events, whereas this window contained no events to
collect. No event was fabricated and the gap was not reinterpreted.

No metric, anomaly, or evidence row was deleted at any point. The `log_source_metric` watermark was
reset backward to `12:54Z` to re-collect the window the defect had skipped; re-collection is an
idempotent upsert on `(log_source_id, bucket_start)`. Letting that re-collection run to completion
validates `dc9aa34` across both historical empty and historical non-empty intervals.

### Operational limitation: watermark advancement is last-write-wins

The manual watermark reset was **overwritten twice** by in-flight `collect_metrics` runs before it
took effect. The collector reads `watermark_at` into its session at the start of a run and writes it
back at the end, so a concurrent external write is silently lost. The per-instance advisory lock
stops two collectors from overlapping but does not protect the row from a writer that never takes
the lock.

Recorded as a follow-up in `docs/PHASE-A-SOURCE-VOLUME-ANOMALY.md` §11.1, with compare-and-set,
row-level locking, a version column, a maintenance-mode pause, and a lock-acquiring administrative
reset command as the candidate fixes. Deliberately not redesigned during live validation.

Until that follow-up lands, a watermark must not be reset while collection tasks are active. Any
future reset must pause Beat or worker consumption, wait for in-flight collection to drain, reset
through a documented path, resume collection, and verify the next window.

## Acceptance Test 1 — baseline / spike / recovery (PASSED)

Run ID `labrun-a1-20260802T182658Z`, scenario `baseline-spike-recovery`, format pfsense, UDP/514,
source `lab-fw-volume-01` (QRadar log source 227). Peak rate 6 EPS, far below the 50 EPS limit.

Sanitized command parameters: `--scenario baseline-spike-recovery --format pfsense --baseline-eps 2
--baseline-duration 480 --anomaly-eps 6 --anomaly-duration 240 --recovery-duration 240`.

**Actual** phase timestamps from the run manifest — 2,880 of 2,880 events sent, zero errors:

| Phase | EPS | Actual start | Actual end | Sent |
|---|---:|---|---|---:|
| baseline | 2 | 18:27:00.585Z | 18:35:00.085Z | 960 |
| anomaly | 6 | 18:35:00.585Z | 18:39:00.419Z | 1,440 |
| recovery | 2 | 18:39:00.585Z | 18:43:00.085Z | 480 |

### Complete buckets

15 buckets, every one `COMPLETE`, no partial or unfinished bucket used as evidence:

| Window | Events | EPS | Phase |
|---|---:|---:|---|
| 18:27–18:35Z (8 buckets) | 119 each | 1.983 | baseline |
| 18:35–18:36Z | 357 | 5.950 | anomaly |
| 18:36–18:39Z (3 buckets) | 359 each | 5.983 | anomaly |
| 18:39–18:40Z | 121 | 2.017 | recovery |
| 18:40–18:42Z (2 buckets) | 119 each | 1.983 | recovery |

### Baseline

Rebuilt through the normal Celery path (`rebuild_baselines`, task `14531c88…`, 0.152 s, 57 sources
/ 76 cells) at 18:36:38Z, from 8 eligible COMPLETE buckets.

| Metric | Median | MAD | p05 | p95 | Samples | Reliable | Version |
|---|---:|---:|---:|---:|---:|---|---:|
| `average_eps` | 1.9833 | **0** | 1.9833 | 1.9833 | 8 | yes | 1 |
| `event_count` | 119 | **0** | 119 | 119 | 8 | yes | 1 |

Cell weekday 7 / hour 18. Zero samples excluded (`off_hours`, `incomplete`, `unfinished`,
`maintenance`, `known_anomaly` all 0). MAD is 0 because the generator is deterministic, so this run
exercised the **zero-MAD deterministic fallback** rather than the robust-z path.

### Detection

| Field | Value |
|---|---|
| Detector | `VOLUME_SPIKE` |
| Observed EPS | 5.983 |
| Expected EPS | 1.983 |
| Ratio | 3.0168 (min 2.0) |
| Absolute delta | 240 (min 100) |
| Robust z | 20.168 (zero-MAD fallback) |
| Confidence | 0.457 |
| Severity | MEDIUM |
| Baseline version | 1 |
| Detection policy version | 1 |

### Lifecycle (audited transitions)

| From | To | Actual timestamp | Reason |
|---|---|---|---|
| `INSUFFICIENT_DATA` | `CANDIDATE` | 18:36:39.586Z | first abnormal bucket |
| `CANDIDATE` | `OPEN` | 18:37:39.611Z | 2 consecutive abnormal bucket(s) reached the confirmation threshold of 2 |
| `OPEN` | `RECOVERING` | 18:40:39.603Z | normal bucket observed |
| `RECOVERING` | `RESOLVED` | 18:41:39.608Z | 2 consecutive normal bucket(s) reached the recovery threshold of 2 |

Anomaly interval `18:35:00Z – 18:39:00Z`. Opened 18:37:39.606Z, resolved 18:41:39.608Z.
**One** incident total, **zero** active afterwards — no duplicate active incident.

### Explanation evidence

Enqueued through the normal Celery path (`collect_anomaly_explanations`, task `819b5905…`) at
18:37:52Z, immediately after OPEN was observed; succeeded in 3.279 s with `explained: 1`. No service
internal was called and no evidence row was inserted by hand.

Package status **PARTIAL**. Baseline window `18:29:00–18:35:00Z`, anomaly window
`18:35:00–18:37:00Z`, completed 18:37:56.823Z, no error.

| Dimension | Availability | Base card | Anom card | Card ratio | Base top share | Anom top share | New | Disappeared |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `action` | AVAILABLE | 1 | 1 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| `category` | AVAILABLE | 2 | 2 | 1.000 | 0.730 | 0.752 | 0 | 0 |
| `destination_ip` | AVAILABLE | 3 | 3 | 1.000 | 0.350 | 0.766 | 0 | 0 |
| `destination_port` | AVAILABLE | 5 | 5 | 1.000 | 0.211 | 0.736 | 0 | 0 |
| `event_name` | AVAILABLE | 2 | 2 | 1.000 | 0.730 | 0.752 | 0 | 0 |
| `protocol` | AVAILABLE | 1 | 1 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| `qid` | AVAILABLE | 2 | 2 | 1.000 | 0.730 | 0.752 | 0 | 0 |
| `source_ip` | AVAILABLE | 5 | 5 | 1.000 | 0.228 | 0.722 | 0 | 0 |
| `source_port` | **TRUNCATED** | 20 | 20 | 1.000 | 0.083 | 0.077 | 20 | 20 |
| `username` | **UNAVAILABLE** | — | — | — | — | — | 0 | 0 |

`username` carries the detail *"field is not populated for this log source"* and is reported as
UNAVAILABLE, not as zero or clean. `source_port` is TRUNCATED at the 20-value cap because ephemeral
ports exceed it; its new/disappeared counts are an artifact of that truncation, not evidence.

Top contributors — **actual QRadar values**, not the generator plan:

| Dimension | Value | Baseline | Anomaly | Delta | % delta | Anom share | Contribution | Rank b→a |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `destination_port` | `445` | 47 | 528 | +481 | 10.2 | 0.736 | 0.959 | 5→1 |
| `event_name` | `Firewall - Deny` | 65 | 539 | +474 | 7.3 | 0.752 | 0.994 | 2→1 |
| `destination_ip` | `10.10.10.20` | 76 | 549 | +473 | 6.2 | 0.766 | 0.974 | 3→1 |
| `source_ip` | `203.0.113.50` | 55 | 518 | +463 | 8.5 | 0.722 | 0.959 | 1→1 |
| `action` | `R2L` | 240 | 717 | +477 | 2.0 | 1.000 | 1.000 | 1→1 |

Note that QRadar normalizes the firewall action to **`R2L`**, not `DENY`. The reported value is what
Ariel returned; the generator's intended `DENY` label does not appear as an action value.

The rank shifts (5→1, 3→1, 2→1) and the top-share jumps are the substantive answer to *what changed
during the anomalous interval*: the spike concentrated on one source IP reaching one destination IP
on port 445 with denied traffic, while permitted traffic stayed flat (`Firewall - Permit` 175 → 178).

### Lag and health

Ingestion lag sub-minute; events for a bucket were queryable in Ariel within the same minute.
Collection lag was **40–41 s after bucket end**, stable across the run. Metric collection durations
52–62 ms. Watermark ended at 18:41:00Z with 39 s lag and zero consecutive failures. Zero task
failures, retries, or tracebacks in the worker across the entire run. Zero duplicate natural keys.

## Authenticated API and frontend verification (live)

**Correction to an earlier note in this file:** an empty `app_user` table was *not* a blocker.
`AppUser` has no references in application code outside its model definition; it is not the
authentication path. Requests resolve through `app/security/auth.py::get_principal`, and with
`AUTH_PROVIDER=local` in a non-production environment:

- no bearer token → admin principal (`admin:*`);
- any bearer token → read-only principal (`read:*`), which satisfies `read:anomalies` through the
  wildcard rule in `app/security/rbac.py`.

No user was created, no row inserted, no permission weakened, and OIDC was left untouched (it still
returns 501). Verification used the **bearer** path deliberately, because it is the more restrictive
principal and matches the least privilege needed to read Phase A data.

Lab-only command — no setup step is required:

```
curl -H "Authorization: Bearer lab-readonly" http://127.0.0.1:8000/api/v1/anomalies
```

| Endpoint | Result |
|---|---|
| `GET /api/v1/anomalies` | 200, one live item |
| `GET /api/v1/anomalies/summary` | 200, incident under `recently_resolved`; 57 monitored, 53 insufficient-data |
| `GET /api/v1/anomalies/{id}` | 200, full detection + explanation package |
| `GET /api/v1/behavior/sources` | 200, 57 sources including 227 |
| `GET /api/v1/behavior/sources/{uuid}` | 200, observed 1.983 / expected 1.983, band [1.983, 1.983], NORMAL, 8 samples |

`GET /api/v1/behavior/sources/227` returns **422**: the route takes the internal UUID, not the
QRadar ID. That is the route contract, not a defect.

The detail response carries `robust_score_status: "DEGENERATE"` and the reason string *"robust score
unavailable (MAD=0)"*, and its query provenance exposes the live AQL including the `START/STOP`
scoping from `dc9aa34`, per-query row counts, per-query truncation flags, and the username error.

Frontend routes `/behavior`, `/behavior/sources/{uuid}`, `/anomalies` and `/anomalies/{id}` all
return 200 and render the live values — source name, `VOLUME_SPIKE`, `RESOLVED`, 5.98 / 1.98, ratio
3.02, delta 240, `PARTIAL`, the four contributor values, `R2L`, `UNAVAILABLE`, `TRUNCATED`, `MAD=0`,
`DEGENERATE`, and all four transition reasons. No fixture was used as proof.

### Truncated evidence is no longer presented as a finding

`DimensionSummary` and `ContributorTable` used one flag for two different questions, so the live
`source_port` dimension rendered a definitive **"20 new, 20 disappeared"**. Under a value cap those
counts are an artifact: a value looks new only because it fell below the cap in the other window.

The evidence model already distinguished `AVAILABLE` / `TRUNCATED` / `UNAVAILABLE` / `FAILED`, so no
schema or API change was needed — the defect was presentation only. Fixed in `404bc39` by splitting
`contributorsAreShowable` (AVAILABLE + TRUNCATED) from `newAndDisappearedAreDeterminate` (AVAILABLE
only). Contributor rows still render for a truncated dimension, because those rows are genuinely
observed; only the counts are withheld. Four regression tests were added, two of which fail without
the component change.

## Drop test — baseline / drop / recovery (PASSED)

Run ID `labrun-drop-20260802T200126Z`, scenario `baseline-drop-recovery`, source 227
(`lab-fw-volume-01`), pfSense, UDP/514. Started on a clean minute boundary at **20:02:00Z** so the
whole run fell inside the weekday-7 / hour-20 seasonal cell, leaving the hour-18 Acceptance Test 1
cell untouched.

Sanitized parameters: `--baseline-eps 5 --baseline-duration 480 --anomaly-eps 1 --anomaly-duration
240 --recovery-duration 240`. **3,840 of 3,840 events sent, zero errors.**

| Phase | EPS | Actual start | Actual end | Sent |
|---|---:|---|---|---:|
| baseline | 5 | 20:02:00.986Z | 20:10:00.786Z | 2,400 |
| drop | 1 | 20:10:00.986Z | 20:13:59.986Z | 240 |
| recovery | 5 | 20:14:00.986Z | 20:18:00.786Z | 1,200 |

15 buckets, **every one `COMPLETE`**; 8 baseline at 299 events (20:02 at 296, first partial second),
4 drop at 63/60/60/60, and recovery back to 296/299/299.

Baseline rebuilt through Celery (`rebuild_baselines`, task `2630249a…`) at 20:11:04Z:

| Cell | Metric | Median | MAD | Samples | Reliable | Version |
|---|---|---:|---:|---:|---|---:|
| wd 7 / hr 20 | `average_eps` | 4.9833 | **0** | 8 | yes | 1 |
| wd 7 / hr 20 | `event_count` | 299 | **0** | 8 | yes | 1 |
| wd 7 / hr 18 | `average_eps` | 1.9833 | 0 | 9 | yes | 2 |

The hour-18 median is unchanged at 1.9833 / 119 after the rebuild, confirming that the Acceptance
Test 1 anomaly buckets were excluded rather than folded into the baseline.

### Detection and lifecycle

| Field | Value |
|---|---|
| Detector | `VOLUME_DROP` |
| Severity | HIGH |
| Expected EPS | 4.983 |
| Observed EPS | 1.0 |
| Ratio | 0.2007 (threshold 0.5) |
| Absolute delta | −239 (min magnitude 100) |
| Robust z | −7.993 (zero-MAD fallback) |
| Confidence | 0.457 |
| Baseline / policy version | 1 / 1 |

| From | To | Actual timestamp | Reason |
|---|---|---|---|
| `INSUFFICIENT_DATA` | `CANDIDATE` | 20:12:39.616Z | first abnormal bucket |
| `CANDIDATE` | `OPEN` | 20:14:39.657Z | 2 consecutive abnormal bucket(s) reached the confirmation threshold of 2 |
| `OPEN` | `RECOVERING` | 20:16:39.608Z | normal bucket observed |
| `RECOVERING` | `RESOLVED` | 20:17:39.613Z | 2 consecutive normal bucket(s) reached the recovery threshold of 2 |

Anomaly interval `20:11:00Z – 20:15:00Z`. **Two incidents total on source 227** (the preserved spike
and this drop), **zero active**, no duplicate. The Acceptance Test 1 incident, its transitions and
its evidence are intact.

### Reduced and disappeared contributors

Explanation enqueued through Celery (`collect_anomaly_explanations`, task `96b3d410…`) at 20:14:47Z,
8 s after OPEN; succeeded in 1.260 s. Status **PARTIAL**, baseline window `20:02–20:11Z`, anomaly
window `20:11–20:14Z`, no error.

Reported only where the dimension is `AVAILABLE`:

| Dimension | Value | Baseline | Anomaly | Delta | % delta | Disappeared |
|---|---|---:|---:|---:|---:|---|
| `event_name` | `Firewall - Permit` | 631 | 180 | −451 | −71% | no |
| `destination_ip` | `10.10.10.20` | 278 | 0 | −278 | −100% | **yes** |
| `destination_ip` | `10.10.10.22` | 265 | 0 | −265 | −100% | **yes** |
| `event_name` | `Firewall - Deny` | 189 | 0 | −189 | −100% | **yes** |
| `destination_port` | `22` | 162 | 0 | −162 | −100% | **yes** |
| `source_ip` | `203.0.113.50` | 161 | 0 | −161 | −100% | **yes** |
| `destination_port` | `445` | 161 | 0 | −161 | −100% | **yes** |

Cardinality collapse in AVAILABLE dimensions: `destination_port` 5 → 1, `source_ip` 5 → 2,
`destination_ip` 3 → 1, `event_name` / `qid` / `category` 2 → 1. Concentration rose to 1.000 for
every collapsed dimension.

`source_port` is **TRUNCATED** (20/20 under the cap) and its 20 new / 20 disappeared counts are
**not** treated as disappearance evidence. `username` remains **UNAVAILABLE** with the reason *field
is not populated for this log source*.

### Lag and health

Ingestion sub-minute. Collection lag a steady **40–41 s after bucket end** for all 15 buckets. Zero
duplicate natural keys, zero non-COMPLETE buckets, final watermark 20:17:00Z at 39 s lag with zero
consecutive failures.

**One task failure occurred**, at 20:00:39Z — before the run started at 20:02:00Z:
`collect_metrics` raised `MissingGreenlet` ("greenlet_spawn has not been called"), an intermittent
connection-pool ping outside greenlet context. It has occurred 3 times in the retained log buffer.
It cost no data here: the watermark only advances on success, so the interval was re-collected on
the next cycle, and all 15 run buckets are present. Recorded as a follow-up below rather than fixed
during a live run.

## Follow-ups recorded, not addressed

1. **Local-auth principal asymmetry.** In development local-auth mode, *no* bearer token yields an
   `admin:*` principal while an arbitrary bearer value yields `read:*` — so an unauthenticated
   caller is more privileged than a token-bearing one. This is refused when `is_production` is true
   and did not affect validation, but it must be reviewed before any non-development deployment.
2. **`MissingGreenlet` in `collect_metrics`.** Intermittent; self-heals via the watermark, but it
   burns a collection cycle and would matter under a tighter lag budget.
3. **Contended watermark advancement** — see `docs/PHASE-A-SOURCE-VOLUME-ANOMALY.md` §11.1.

## Multi-source isolation test (PASSED)

Two additional log sources were created manually in the QRadar UI (never through REST) and verified
read-only before use:

| QRadar ID | Name | Identifier | Parser | Collector | Enabled |
|---:|---|---|---|---:|---|
| 227 | LAB Phase A Firewall | `lab-fw-volume-01` | Netgate pfSense | 7 | yes |
| 262 | LAB Phase A Firewall 2 | `lab-fw-volume-02` | Netgate pfSense | 7 | yes |
| 263 | LAB Phase A Firewall 3 | `lab-fw-volume-03` | Netgate pfSense | 7 | yes |

Synchronized into the application through the normal Celery task (`sync_log_sources`, task
`6f4a3b72…`): 49 seen, **2 created**, 47 updated.

**Source-level overrides: none were added.** Source 227 keeps its existing `{"open_after": 2}`.
Sources 262 and 263 were deliberately left on the global `open_after = 1`, because they are the
control sources — a *lower* confirmation threshold makes the isolation claim stricter, since a
false positive would surface a bucket sooner rather than later.

Run ID `labrun-multi-20260802T203138Z`, scenario `multi-source-single-spike`, started on a clean
minute boundary at **20:32:00Z**. **16,800 of 16,800 events sent, zero errors.** Peak combined rate
25 EPS (15 + 5 + 5), below the 50 EPS limit.

| Phase | Actual start | Actual end | Rates | Sent |
|---|---|---|---|---:|
| baseline | 20:32:00.465Z | 20:40:00.265Z | all three @ 5 EPS | 7,200 |
| anomaly | 20:40:00.465Z | 20:44:00.399Z | **01 @ 15 EPS**, 02/03 @ 5 EPS | 6,000 |
| recovery | 20:44:00.465Z | 20:48:00.265Z | all three @ 5 EPS | 3,600 |

### Per-source result

All 14 buckets `COMPLETE` for every source. Only source 227 departs from baseline:

| Bucket | 227 | 262 | 263 |
|---|---:|---:|---:|
| 20:32–20:39Z (8 baseline) | 299–300 | 299–300 | 299–300 |
| 20:40Z | **885** | 296 | 296 |
| 20:41Z | **901** | 300 | 300 |
| 20:42Z | **902** | 301 | 301 |
| 20:43Z | **901** | 300 | 300 |
| 20:44–20:45Z (recovery) | 311, 300 | 303, 300 | 303, 300 |

| Source | Baseline median | MAD | Samples | Version | Expected EPS | Observed EPS | Ratio | Incidents |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 227 | 299 | 0 | 9 | 3 | 4.983 | **15.033** | **3.0167** | **1** |
| 262 | 300 | 0 | 8 | 1 | 5.000 | 5.000 | 1.00 | **0** |
| 263 | 300 | 0 | 8 | 1 | 5.000 | 5.000 | 1.00 | **0** |

Detector states during the anomaly window: 227 `VOLUME_SPIKE` = **OPEN** (2 consecutive anomalous);
262 and 263 `VOLUME_SPIKE` = **NORMAL** (0 anomalous, 2 healthy). Every other detector type on all
three sources stayed `NORMAL`.

### Lifecycle — source 227 only

Incident `2f3a874c…`, `VOLUME_SPIKE`, MEDIUM. Observed 15.033 EPS vs expected 4.983, ratio 3.0167,
absolute delta **603**, robust z 20.167 (zero-MAD fallback), confidence 0.441, baseline version 3.

| From | To | Actual timestamp | Reason |
|---|---|---|---|
| `NORMAL` | `CANDIDATE` | 20:42:39.628Z | first abnormal bucket |
| `CANDIDATE` | `OPEN` | 20:43:39.632Z | 2 consecutive abnormal bucket(s) reached the confirmation threshold of 2 |
| `OPEN` | `RECOVERING` | 20:45:39.628Z | normal bucket observed |
| `RECOVERING` | `RESOLVED` | 20:46:39.635Z | 2 consecutive normal bucket(s) reached the recovery threshold of 2 |

Anomaly interval `20:41:00Z – 20:44:00Z`.

### Evidence belongs to the changed source

Explanation `61ec4980…` enqueued through Celery (`collect_anomaly_explanations`, task `1331f920…`)
at 20:43:56Z, 17 s after OPEN; succeeded in 1.125 s. Status **PARTIAL**, baseline window
`20:35–20:41Z`, anomaly window `20:41–20:43Z`.

Ownership was verified structurally, not assumed: all **20** provenance AQL queries reference
`logsourceid = 227` and no other source.

Top contributors (AVAILABLE dimensions only):

| Dimension | Value | Baseline | Anomaly | Delta | Contribution | Rank b→a |
|---|---|---:|---:|---:|---:|---|
| `destination_ip` | `10.10.10.20` | 388 | 1,407 | +1,019 | 0.986 | 1→1 |
| `event_name` | `Firewall - Deny` | 349 | 1,364 | +1,015 | 0.993 | 2→1 |
| `destination_port` | `445` | 319 | 1,331 | +1,012 | 0.967 | 1→1 |
| `source_ip` | `203.0.113.50` | 311 | 1,319 | +1,008 | 0.962 | 1→1 |
| `action` | `R2L` | 795 | 1,803 | +1,008 | 1.000 | 1→1 |

`source_port` is **TRUNCATED** and `username` **UNAVAILABLE**, as in every prior run.

### Two honest observations

**1. An unopened candidate remains in state `NORMAL` with `resolved_at` NULL.** Incident
`1f088461…` (`VOLUME_DROP`, source 227) was raised because source 227 was idle between the drop test
and this one: the 20:18Z bucket held 3 events and no bucket exists for 20:19–20:31Z. Its trail is
`NORMAL → CANDIDATE` (20:20:39Z, "first abnormal bucket") then `CANDIDATE → NORMAL` (20:34:39Z,
"returned to normal before opening"). It never opened, so nothing was ever resolved and
`resolved_at` is correctly NULL.

This is an artifact of test sequencing, not a defect and not a duplicate incident from this run. It
does mean **`resolved_at IS NULL` is the wrong test for "active"** — the product already uses state,
and `/api/v1/anomalies/summary` correctly reports `open_anomalies: 0`, `candidates: 0`,
`recovering: 0`.

**2. Two incidents opened on QRadar's own internal log sources**, outside the lab set and before
this run began: `System Notification-2` (`VOLUME_SPIKE`, 20:10Z) and `SIM Audit-2` (`VOLUME_SPIKE`,
20:14Z), both since RESOLVED. These are genuine detections against real appliance telemetry whose
volume actually varied — not false positives, and not from the lab generator. They do not bear on
the isolation claim, which concerns 227 versus 262/263 inside the 20:32–20:48Z window.

Across the whole database: 6 incidents, 5 RESOLVED and 1 NORMAL (the unopened candidate above), 20
transitions, zero duplicate natural keys, zero non-COMPLETE buckets.

## Still outstanding

Silence validation, `LAB_MODE` and source-override cleanup, and the final gate run.
