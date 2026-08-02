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

## Live scenario results

Not yet run. Acceptance Test 1 (baseline-spike-recovery), multi-source isolation, drop, and silence
all remain outstanding, as do the API and frontend live checks and the `LAB_MODE` cleanup.
