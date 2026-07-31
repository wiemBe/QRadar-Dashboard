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
