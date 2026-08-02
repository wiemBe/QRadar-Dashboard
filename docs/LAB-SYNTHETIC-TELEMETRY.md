# QRadar Lab Synthetic Telemetry

This guide is for the isolated QRadar lab only. The generator is a manual tool: the backend,
Celery, Beat, Compose startup, and automated tests never invoke it. QRadar configuration remains a
manual console operation; the application does not create log sources or rules through the API.

## Safety contract

- Default destination is `192.168.122.50:514/UDP`, default rate is **1 EPS**, and default format is
  Universal LEEF.
- Payload addresses use RFC 5737 documentation networks. They are fields inside the message, not
  spoofed packet addresses. `--bind-address` selects a real local interface address only.
- Rates above 100 EPS are rejected unless `--allow-high-rate` is explicit. Keep initial validation
  at or below 50 EPS.
- Only synthetic usernames are accepted: `administrator`, `efe.lab`, `test.user`, `svc_backup`,
  `svc_sql`, and `compromised.user`.
- Use `--dry-run --stdout` before every new recipe. Do not commit generated output files.

## Generator quick start

The maintained entrypoint is `tools/qradar_lab_loggen.py`; `lloggen.py` at repository root is a
backward-compatible wrapper.

```bash
# Reproducible preview; no packet is sent
python tools/qradar_lab_loggen.py --dry-run --stdout --seed 42 --count 5 \
  --types linux_auth --format leef

# Initial bounded live check
python tools/qradar_lab_loggen.py --target 192.168.122.50 --port 514 \
  --protocol udp --format leef --types ips --seed 42 --eps 1 --count 20 --stdout

# A correlated authentication sequence: six failures, then one success
python tools/qradar_lab_loggen.py --scenario failed-login-then-success \
  --attempt-count 6 --seed 42 --eps 1 --stdout
```

`--count` and `--duration` may be combined; the first bound reached stops execution. A brute-force
scenario with neither bound stops after `--attempt-count` failures. Failed-login-then-success stops
after that many failures plus the final success. Other scenarios remain continuous unless bounded.

Stable default identities are used unless `--multiple-devices` is explicit:

| Profile | Default identity |
|---|---|
| `ips` | `ips-gw-01` |
| `waf` | `waf-prod-01` |
| `linux_firewall` (`linux` alias) | `linux-fw-01` |
| `windows_firewall` (`windows` alias) | `WIN-FW-01` |
| `linux_auth` | `linux-auth-01` |
| `windows_auth`, `windows_account` | `DC-LAB-01` |
| `dns` | `dns-lab-01` |
| `proxy` | `proxy-lab-01` |

## Manual QRadar log-source setup

Create these in the QRadar UI under **Admin → Data Sources → Events → Log Sources**. Use protocol
type **Syslog**, listen port **514**, and the exact case-sensitive identifier below. Deploy changes
from the UI. Do not use the QRadar API for this operation.

| Display name | Syslog hostname / log-source identifier | Recommended type | Protocol |
|---|---|---|---|
| LAB IPS | `ips-gw-01` | Universal LEEF | Syslog UDP/514 |
| LAB WAF | `waf-prod-01` | Universal LEEF | Syslog UDP/514 |
| LAB Linux Firewall | `linux-fw-01` | Universal LEEF | Syslog UDP/514 |
| LAB Windows Firewall | `WIN-FW-01` | Universal LEEF | Syslog UDP/514 |
| LAB Linux Authentication | `linux-auth-01` | Universal LEEF | Syslog UDP/514 |
| LAB Windows Security | `DC-LAB-01` | Universal LEEF | Syslog UDP/514 |
| LAB DNS | `dns-lab-01` | Universal LEEF | Syslog UDP/514 |
| LAB Proxy | `proxy-lab-01` | Universal LEEF | Syslog UDP/514 |

If testing `--format vendor`, clone the lab source and deliberately select a suitable DSM (for
example Suricata/CEF, ModSecurity/CEF, Linux OS, or Windows Security Event Log). Do not change a
working LEEF source in place; parser comparisons need separate identifiers.

Every normal LEEF event contains `devTime`, `src`, `dst`, ports, protocol, username, action,
severity, category, stable event/rule IDs, device identity, request fields, status, and signature.
The compact samples below omit no required key.

### LAB IPS

Sample:

```text
<134>Aug 01 12:00:00 ips-gw-01 LEEF:2.0|SyntheticLab|QRadarLab|1.0|IPS-2010935|0x09|devTime=2026-08-01T12:00:00.000Z src=198.51.100.44 dst=10.10.10.10 srcPort=41000 dstPort=443 proto=TCP usrName=compromised.user action=blocked severity=9 category=intrusion eventId=IPS-2010935 deviceHostName=ips-gw-01 deviceAddress=10.10.10.10 requestMethod=- request=- statusCode=0 ruleId=IPS-2010935 signature=ET EXPLOIT Apache Struts Attempt
```

Validation search:

```sql
SELECT starttime, sourceip, destinationip, eventname,
       LOGSOURCENAME(logsourceid) AS "Log Source"
FROM events
WHERE LOGSOURCENAME(logsourceid) = 'LAB IPS'
LAST 15 MINUTES
```

Troubleshooting: confirm the payload begins with `LEEF:2.0`, identifier is exactly `ips-gw-01`,
and `eventId` is stable. If it lands in SIM Generic Log, verify the deployed identifier.

### LAB WAF

Sample payload body: `LEEF:2.0|SyntheticLab|QRadarLab|1.0|WAF-942100|0x09|...` with
`requestMethod=GET`, a synthetic `/login?id=...` request, `statusCode=403`,
`ruleId=WAF-942100`, and `signature=SQL Injection Attack Detected`.

```sql
SELECT starttime, sourceip, URL, eventname, LOGSOURCENAME(logsourceid) AS "Log Source"
FROM events WHERE LOGSOURCENAME(logsourceid) = 'LAB WAF' LAST 15 MINUTES
```

Troubleshooting: URL/request may require a Universal LEEF custom-property mapping on some DSM
versions. Validate raw payload before concluding the generator omitted it.

### LAB Linux Firewall

Sample payload body: `LEEF:2.0|SyntheticLab|QRadarLab|1.0|LNX-FW-1001|0x09|...` with
`action=blocked`, `category=firewall`, and `deviceHostName=linux-fw-01`. Vendor mode emits exactly
one `kernel:` tag followed by `[UFW BLOCK]` or `[UFW ALLOW]`.

```sql
SELECT starttime, sourceip, destinationip, destinationport, eventname
FROM events WHERE LOGSOURCENAME(logsourceid) = 'LAB Linux Firewall' LAST 15 MINUTES
```

Troubleshooting: `kernel: kernel:` means an obsolete generator is being used. Run the maintained
tool and verify the exact syslog identifier.

### LAB Windows Firewall

Sample payload body uses stable IDs `WIN-5156` (allow) or `WIN-5157` (deny), hostname
`WIN-FW-01`, and normalized source/destination/port fields.

```sql
SELECT starttime, sourceip, destinationip, destinationport, eventname
FROM events WHERE LOGSOURCENAME(logsourceid) = 'LAB Windows Firewall' LAST 15 MINUTES
```

Troubleshooting: this is LEEF over syslog, not WinCollect. Do not configure the lab source as a
WinCollect agent unless testing the separate vendor format.

### LAB Linux Authentication

Stable event IDs cover `SSH_LOGIN_FAILED`, `SSH_LOGIN_SUCCESS`, `SUDO_COMMAND`, `USER_CREATED`,
`USER_DELETED`, `SERVICE_STARTED`, and `AUDIT_LOG_CLEARED`.

```sql
SELECT starttime, username, sourceip, eventname
FROM events WHERE LOGSOURCENAME(logsourceid) = 'LAB Linux Authentication' LAST 15 MINUTES
```

Troubleshooting: a username of `N/A` with `usrName` visible in raw payload indicates a DSM custom
property/parsing limitation, not missing generator data.

### LAB Windows Security

The source covers Security/System IDs 4624, 4625, 4720, 4725, 4726, 4728, 4729, 4732, 4733,
1102, 4688, and 7045 using `windows_auth` and `windows_account` profiles.

```sql
SELECT starttime, username, sourceip, eventname
FROM events WHERE LOGSOURCENAME(logsourceid) = 'LAB Windows Security' LAST 15 MINUTES
```

Troubleshooting: LEEF event IDs are `WIN-<WindowsEventID>`. Raw vendor-style Windows messages need
a separate Windows DSM source; do not mix them into the Universal LEEF source.

### LAB DNS

Sample payload body uses `DNS-QUERY-1001`, UDP/53, `category=dns`, and a reserved
`lab.example.test` query.

```sql
SELECT starttime, sourceip, destinationip, eventname
FROM events WHERE LOGSOURCENAME(logsourceid) = 'LAB DNS' LAST 15 MINUTES
```

Troubleshooting: if the query is not normalized, inspect the `request` LEEF field and add a
lab-specific custom property manually.

### LAB Proxy

Sample payload body uses `PROXY-HTTP-1001`, `requestMethod=GET`, a reserved
`https://portal.example.test/lab` URL, and `statusCode=200`.

```sql
SELECT starttime, username, sourceip, URL, eventname
FROM events WHERE LOGSOURCENAME(logsourceid) = 'LAB Proxy' LAST 15 MINUTES
```

Troubleshooting: verify URL parsing separately from source routing. The event can route correctly
while a QRadar version lacks the desired custom-property mapping.

## Controlled scenarios

```bash
python tools/qradar_lab_loggen.py --scenario normal --types linux_auth --seed 10 --eps 1 --count 240
python tools/qradar_lab_loggen.py --scenario volume-spike --types linux_auth --seed 10 --eps 20 --count 120
python tools/qradar_lab_loggen.py --scenario brute-force --attempt-count 10 --seed 11 --eps 2
python tools/qradar_lab_loggen.py --scenario password-spray --seed 12 --eps 2 --count 12
python tools/qradar_lab_loggen.py --scenario failed-login-then-success --attempt-count 6 --seed 13 --eps 1
python tools/qradar_lab_loggen.py --scenario waf-sqli-burst --seed 14 --eps 5 --count 25
python tools/qradar_lab_loggen.py --scenario repeated-payload --types proxy --seed 15 --eps 5 --count 20
python tools/qradar_lab_loggen.py --scenario parsing-degradation --types proxy --seed 16 --eps 2 --count 20
python tools/qradar_lab_loggen.py --scenario timestamp-delay --timestamp-delay 3600 --seed 17 --count 10
python tools/qradar_lab_loggen.py --scenario cardinality-drop --types dns --seed 18 --count 20
```

## Phase A volume scenarios

The scenarios above vary event *content*. The eight Phase A scenarios vary *volume over time*
against a stable source identity, which is the only thing the source-volume detectors judge. They
are the end-to-end exercise for: generator → QRadar syslog → bounded Ariel aggregation → metric
buckets → seasonal baseline → spike/drop/silence detection → lifecycle → explanation evidence →
behavioral frontend.

| Scenario | Phases | What it demonstrates |
|---|---|---|
| `source-volume-baseline` | baseline | Builds baseline samples for one source |
| `source-volume-spike` | anomaly | An immediate high-volume interval |
| `source-volume-drop` | anomaly | An immediate materially lower interval |
| `source-volume-silence` | baseline, then clean exit | `NO_EVENTS` after the grace period |
| `baseline-spike-recovery` | baseline → anomaly → recovery | Full `VOLUME_SPIKE` lifecycle |
| `baseline-drop-recovery` | baseline → anomaly → recovery | Full `VOLUME_DROP` lifecycle |
| `multi-source-single-spike` | baseline → anomaly → recovery | Detector isolation; only one source rises |
| `multi-source-single-drop` | baseline → anomaly → recovery | Detector isolation; only one source falls |

### Stable Phase A source identities

A Phase A scenario never changes hostname mid-run, and the RFC3164 hostname is exactly the QRadar
log-source identifier.

| Host / log-source identifier | Device address | Kind | Live QRadar ID |
|---|---|---|---:|
| `lab-fw-volume-01` | `10.20.0.11` | firewall (default for single-source scenarios) | **227** |
| `lab-fw-volume-02` | `10.20.0.12` | firewall (default for `source-volume-silence`) | **262** |
| `lab-fw-volume-03` | `10.20.0.13` | firewall | **263** |
| `lab-waf-volume-01` | `10.20.0.21` | WAF |
| `lab-ips-volume-01` | `10.20.0.31` | IPS |

Multi-source scenarios always use `lab-fw-volume-01/02/03`, and only the first one changes rate.
Silence defaults to `lab-fw-volume-02` so a silence run never erases the history of the source used
for spike and drop. Do not run silence and a multi-source scenario at the same time.

The three firewall identities are configured on the lab appliance as **Netgate pfSense** log sources
on event collector 7 — not Universal LEEF. pfSense gives native parsing of source and destination
address, ports, protocol, event name, category and severity, which is what makes contributor
evidence usable. Two consequences show up in live evidence and are documented rather than papered
over: QRadar normalizes the firewall action to **`R2L`**, not `DENY`, and the DSM populates no
`username`, so that dimension is always `UNAVAILABLE` for these sources.

### Seasonal-cell alignment when scheduling a run

Baselines are per `(weekday, hour)` cell, so a run that crosses an hour boundary splits its samples
across two cells and can leave the second one below the sample minimum. Start a scenario on a clean
minute boundary and size it to finish inside the same UTC hour. Where a source already has history
in the current cell at a different rate, either match that rate or move to the next clean hour —
2026-08-02 validation used hour 18 for spike, hour 20 for drop and multi-source, and hour 21 for
silence, precisely to keep each cell coherent.

Note that `NO_EVENTS` needs a **reliable cell that expects traffic**. A brand-new seasonal cell has
no baseline, so silence in it yields `INSUFFICIENT_DATA`, not an anomaly. Build the cell with real
traffic and rebuild the baseline *while traffic is still flowing*, before stopping — otherwise the
zero buckets that silence produces are themselves folded into the median.

### Event format

LEEF 2.0 is the default. The header is
`LEEF:2.0|QRadarLab|<product>|1.0|<eventId>|0x09|`, the extension is **tab**-delimited, and the
RFC3164 hostname always equals `deviceHostName`.

`devTime` is **epoch milliseconds**, so QRadar needs no `devTimeFormat` and makes no assumption
about the log source's timezone. ISO text would be read against the configured log-source timezone,
and an event parsed a few hours late lands in the wrong metric bucket — which would silently
invalidate a whole validation run.

Firewall event (`lab-fw-volume-01`, wrapped here for readability; on the wire it is one line and
the field separator is a tab):

```text
<134>Aug 01 12:00:00 lab-fw-volume-01 LEEF:2.0|QRadarLab|SyntheticFirewall|1.0|FW_CONNECTION_DENIED|0x09|
devTime=1785671418867	eventId=FW_CONNECTION_DENIED	eventName=Firewall Denied Connection
deviceHostName=lab-fw-volume-01	deviceAddress=10.20.0.11	src=203.0.113.50	dst=10.10.10.20
srcPort=41000	dstPort=445	proto=TCP	action=DENY	severity=6	category=Firewall
runId=labrun-a1	scenario=baseline-spike-recovery	phase=anomaly	sev=6	cat=Firewall
direction=inbound	ifName=ens160	policyId=FW-191	ruleName=lab-perimeter-drop	tcpFlags=SYN
ttl=108	pktLen=84	bytesIn=0	bytesOut=0	sessionId=8991880
```

WAF event (`lab-waf-volume-01`) replaces the firewall detail block with:

```text
httpMethod=POST	url=/api/v1/report?id=1%27+OR+%271%27%3D%271	virtualHost=portal.lab.test
userAgent=sqlmap/1.8	responseCode=403	bytesIn=1360	bytesOut=0	ruleId=942100
```

IPS event (`lab-ips-volume-01`) replaces it with:

```text
sigId=2010935	classification=attempted-admin	priority=1	flowId=3276503845	pktCount=216
direction=inbound	ifName=ens160
```

#### Fields and how QRadar sees them

| LEEF key | Example | Mapping |
|---|---|---|
| `devTime` | `1785671418867` | Standard. Epoch ms; no `devTimeFormat` required |
| `src`, `dst` | `203.0.113.50`, `10.10.10.20` | Standard → Source IP / Destination IP |
| `srcPort`, `dstPort` | `41000`, `445` | Standard → Source Port / Destination Port |
| `proto` | `TCP` | Standard → Protocol |
| `sev` | `6` | Standard → Severity (`severity` is the same value, spelled out) |
| `cat` | `Firewall` | Standard → Category (`category` is the same value, spelled out) |
| `eventId` | `FW_CONNECTION_DENIED` | LEEF header field 5; drives the QID mapping |
| `eventName` | `Firewall Denied Connection` | Custom property |
| `action` | `DENY` / `ALLOW` | Custom property — used as explanation evidence |
| `deviceHostName`, `deviceAddress` | `lab-fw-volume-01`, `10.20.0.11` | Custom property |
| `runId` | `labrun-a1` | Custom property — correlates events with a generator run |
| `scenario` | `baseline-spike-recovery` | Custom property |
| `phase` | `baseline` / `anomaly` / `recovery` | Custom property — the generator phase |

The extension is tab-delimited, so every custom property is the same regex shape. Create these as
**Custom Event Properties** (Admin → Data Sources → Custom Event Properties), regex, capture group
1, against the Universal LEEF log source:

| Property name | Regex |
|---|---|
| LabRunId | `runId=([^\t]+)` |
| LabScenario | `scenario=([^\t]+)` |
| LabPhase | `phase=([^\t]+)` |
| LabAction | `action=([^\t]+)` |
| LabEventName | `eventName=([^\t]+)` |
| LabDeviceHostName | `deviceHostName=([^\t]+)` |

Only `LabRunId` and `LabPhase` are needed to verify a run; the rest make the raw event readable in
the log activity view. Phase A explanation evidence uses the *standard* properties (source IP,
destination IP, destination port, event name, category), so a spike is explainable even if none of
the custom properties above are created.

#### Event IDs to map

The generator emits exactly seven event IDs. Map them to QIDs (or let them land as unknown — the
volume detectors do not care, but `UNKNOWN_EVENT_SPIKE` will notice):

| `eventId` | `eventName` | Source |
|---|---|---|
| `FW_CONNECTION_ALLOWED` | Firewall Connection Allowed | firewall |
| `FW_CONNECTION_DENIED` | Firewall Denied Connection | firewall |
| `FW_SESSION_CLOSED` | Firewall Session Closed | firewall |
| `WAF_REQUEST_PASSED` | Web Request Passed | WAF |
| `WAF_REQUEST_BLOCKED` | Web Request Blocked | WAF |
| `IPS_SIGNATURE_ALERT` | Intrusion Signature Alert | IPS |
| `IPS_SIGNATURE_DROP` | Intrusion Signature Drop | IPS |

#### pfSense filterlog format

`--format pfsense` renders the firewall sources as pfSense `filterlog` lines, which the **Netgate
pfSense** DSM parses natively. This is what the lab's `LAB Phase A Firewall` source (QRadar ID 227)
actually uses:

```text
<134>Aug 02 12:47:22 lab-fw-volume-01 filterlog[41231]: 5,,,1000000005,em0,match,pass,in,4,0x0,,112,15431,0,DF,6,tcp,72,203.0.113.50,10.10.10.21,30097,443,922,SA,6896606,6896607,64240,,mss;nop;wscale;sackOK;TS
```

Field order is the documented IPv4/TCP layout: rule, sub-rule, anchor, tracker, interface, reason,
action, direction, IP version, then the IPv4 header block (TOS, ECN, TTL, id, offset, flags,
protocol id, protocol text, length, source, destination), then the TCP block (source port,
destination port, data length, flags, sequence, ack, window, urgent, options).

What QRadar extracts without any custom property:

| QRadar property | Source |
|---|---|
| Source IP / Destination IP | filterlog fields 19 / 20 |
| Source Port / Destination Port | fields 21 / 22 |
| Protocol | field 17 (`tcp`) |
| Event name / QID / Category | the `pass`/`block` action → `Firewall - Permit` (qid 114500044, cat 4002) or `Firewall - Deny` (qid 114500042, cat 4003) |

Every Phase A explanation dimension is therefore a native property, which is a better position than
Universal LEEF would have given.

The trade-off: **filterlog has no field for a run identifier**, so `runId` and `scenario` are not
carried in the payload. The generator phase rides on the matched rule number instead — `5` baseline,
`6` anomaly, `7` recovery, each with tracker `1000000000 + rule` — and run correlation is by log
source plus the phase windows recorded in the run manifest. Verify a run by time window and log
source, not by text-searching a run ID.

`--format pfsense` is rejected for the WAF and IPS sources and for the content recipe scenarios: a
filterlog line from a web application firewall would be a lie the DSM would happily parse.

⚠️ Text-searching a run ID in Ariel also matches SIM Audit's record of *your own* search. Always
group by log source rather than trusting a raw count.

#### Vendor format

`--format vendor` keeps a parser-testing shape instead, for a separately-created log source with a
real DSM. One syslog tag, never two:

```text
<134>Aug 01 12:00:00 lab-fw-volume-01 kernel: [UFW BLOCK] IN=ens160 OUT= SRC=203.0.113.50 DST=10.10.10.20 LEN=84 TOS=0x00 PREC=0x00 TTL=108 DF PROTO=TCP SPT=41000 DPT=445 WINDOW=64240 RES=0x00 SYN URGP=0 runId=labrun-a1 scenario=baseline-spike-recovery phase=anomaly
```

WAF uses a ModSecurity `[client …] [id …] [msg …] [uri …]` line and IPS a Suricata
`[Drop] [**] [1:2010935:1] … [Classification: …] [Priority: 1] {TCP} src:port -> dst:port` line.
Both carry the same `runId`/`scenario`/`phase` trailer. Do not point a vendor run at a working LEEF
source; parser comparisons need separate identifiers.

### Explanation-aware distribution

During `baseline` and `recovery` the generator spreads events across several source IPs,
destination IPs, destination ports, both `ALLOW` and `DENY`, and several event names, so an
explanation query has real contributors to rank.

During a **spike**, only the *additional* volume concentrates — the baseline share keeps its normal
spread — on deterministic contributors: `src=203.0.113.50`, `dst=10.10.10.20`, `dstPort=445`,
`action=DENY`, `eventName=Firewall Denied Connection`. The increase should therefore be explainable
as a change in share.

During a **drop**, the pool narrows to `192.0.2.10/11 → 10.10.10.21:443 ALLOW`, so
`203.0.113.50`, port `445` and `DENY` disappear entirely and can be tested as disappeared-value
evidence. A drop is always valid events at a lower rate, never malformed events.

Expected percentages are never hardcoded anywhere in the application; every share reported by the
investigation view is computed from what QRadar actually ingested.

### Flags

```bash
# Preview a full lifecycle without sending a packet
python tools/qradar_lab_loggen.py --scenario baseline-spike-recovery \
  --run-id labrun-preview --seed 42 --dry-run --stdout \
  --baseline-duration 60 --anomaly-duration 30 --recovery-duration 30

# The 2 -> 6 -> 2 EPS acceptance run (see the live checklist below for durations).
# Start it just after a clock hour so the whole run fills one baseline cell.
python tools/qradar_lab_loggen.py --scenario baseline-spike-recovery \
  --target 192.168.122.50 --port 514 --protocol udp --format pfsense \
  --run-id labrun-a1 --seed 42 \
  --baseline-eps 2 --baseline-duration 420 \
  --anomaly-multiplier 3 --anomaly-duration 240 \
  --recovery-duration 300 \
  --summary-json lab-runs/labrun-a1.summary.json
```

| Flag | Meaning |
|---|---|
| `--scenario` | One of the eight names above (or a content recipe) |
| `--run-id` | Stable identifier stamped on every event; defaults to `labrun-<UTC timestamp>` |
| `--seed` | Reproduces field selection exactly; timestamps still follow the wall clock |
| `--format` | `leef` (default), `pfsense` (firewall sources only) or `vendor` |
| `--baseline-eps` | Baseline rate per source; default 2, or 5 for drop scenarios |
| `--baseline-duration` | Seconds of baseline; default 360 |
| `--anomaly-eps` | Absolute anomaly rate; wins over `--anomaly-multiplier` |
| `--anomaly-multiplier` | Anomaly rate as a multiple of baseline; default 3 (spike) or 0.2 (drop) |
| `--anomaly-duration` | Seconds of anomaly; default 180 |
| `--recovery-duration` | Seconds of recovery; default 240 |
| `--fixed-host` | Pin a single-source scenario to one of the Phase A hosts |
| `--fixed-source-ip`, `--fixed-destination-ip`, `--fixed-destination-port`, `--fixed-action` | Pin a payload dimension (`ALLOW`/`DENY` for action) |
| `--bind-address` | Local socket address only; never spoofs a packet source |
| `--allow-high-rate` | Required when *aggregate* EPS across sources exceeds 100 |
| `--summary-json` | Write a sanitized run manifest |
| `--count` | Hard cap on total events, applied on top of the timed plan |

The generator prints a sanitized plan before sending: run ID, target, protocol, format, seed,
source identities, per-phase rates and durations, expected event count, and an advisory line
showing events per 60-second bucket with the resulting ratio and absolute delta. It never prints a
credential.

### Run manifests

`--summary-json` writes run ID, scenario, sources, format, target, protocol, seed, generator
version, start/end times, per-phase start/end times, requested EPS per phase, events attempted and
sent per phase, and any transport errors. It contains no token, header or credential. Manifests are
Git-ignored (`lab-runs/`, `*.summary.json`); paste sanitized excerpts into
`docs/LAB-DEMO-RESULTS.md` instead of committing the files.

A transport error does not abort a timed scenario — the later phases still carry evidence, and the
manifest records exactly how many events were lost.

### Manual QRadar log sources for Phase A

Create these by hand in the console; the application never creates or modifies a log source through
the API.

| Display name | Log source identifier | Type | Generator format | Protocol |
|---|---|---|---|---|
| LAB Phase A Firewall | `lab-fw-volume-01` | Netgate pfSense | `--format pfsense` | Syslog UDP/514 |
| LAB Phase A Firewall 2 | `lab-fw-volume-02` | Netgate pfSense | `--format pfsense` | Syslog UDP/514 |
| LAB Phase A Firewall 3 | `lab-fw-volume-03` | Netgate pfSense | `--format pfsense` | Syslog UDP/514 |
| LAB Phase A WAF | `lab-waf-volume-01` | Universal LEEF | `--format leef` | Syslog UDP/514 |
| LAB Phase A IPS | `lab-ips-volume-01` | Universal LEEF | `--format leef` | Syslog UDP/514 |

The firewall sources use the pfSense DSM (see the filterlog section above); the format must match
the type or events fall through to SIM Generic Log DSM as `Unknown log event`.

Only `lab-fw-volume-01` is required for the first ingestion smoke test. The multi-source acceptance
test needs `lab-fw-volume-02` and `-03` as well, and the silence test needs `lab-fw-volume-02`.

### Timing the phases against LAB_MODE

Every duration below is derived from the values in the LAB_MODE table further down, not chosen for
convenience. At a 60-second bucket:

| Phase | Requirement | Minimum | Use |
|---|---|---:|---:|
| baseline | `BASELINE_MIN_SAMPLES` = 4 complete buckets | 240 s | 420 s |
| anomaly | confirmation buckets (see below) | 60–120 s | 240 s |
| recovery | `ANOMALY_RESOLVE_AFTER_INTERVALS` = 2 complete buckets | 120 s | 300 s |
| silence | `ANOMALY_SILENCE_GRACE_BUCKETS` = 1 bucket, plus evaluation | 120 s | 180 s |

Two things are easy to get wrong:

- **Baseline cells are keyed `(metric, ISO weekday, hour)`.** At 60-second buckets four samples
  accumulate inside a single clock hour, but a run that straddles an hour boundary starts filling a
  *different* cell. Start a baseline phase soon after the hour, not at `:57`.
- **A partial bucket is not evidence.** The bucket containing "now" is still accumulating and is
  excluded from baselining. Always let the current bucket close before reading a result.

With `LAB_MODE=true` the confirmation count is 1, and `lifecycle.next_state` promotes the first
abnormal bucket straight from `NORMAL` to `OPEN` — `CANDIDATE` is never persisted. To observe
`CANDIDATE → OPEN` without touching a production default or a detection threshold, set the
confirmation count on the lab source alone:

```sql
-- one lab log source only; production defaults and thresholds untouched
UPDATE log_source SET custom_thresholds = '{"open_after": 2}'::jsonb
WHERE identifier = 'lab-fw-volume-01';
```

`ThresholdResolver` merges `custom_thresholds` over the resolved profile, so the source needs two
consecutive abnormal buckets: `CANDIDATE` on the first, `OPEN` on the second. Budget one extra
anomaly bucket when you do this.

### Guard arithmetic

The volume guards are deliberately **not** part of LAB_MODE — a lab that demonstrates a detector
production does not run is worse than no lab. Scenario rates must therefore clear the production
guards on merit:

| Run | Expected/bucket | Observed/bucket | Ratio | Absolute delta | Guards |
|---|---:|---:|---:|---:|---|
| spike, 2 → 6 EPS | 120 | 360 | 3.00 | 240 | ratio ≥ 2.0 ✓, delta ≥ 100 ✓, observed ≥ 50 ✓ |
| drop, 5 → 1 EPS | 300 | 60 | 0.20 | 240 | ratio ≤ 0.5 ✓, delta ≥ 100 ✓, expected ≥ 50 ✓ |

This is why drop scenarios default to a 5 EPS baseline: a 2 EPS baseline dropping to 0.4 EPS is a
delta of 96 and would be correctly refused by the absolute-delta guard.

## Manual lab-only QRadar rule recipes

Create and deploy these only in the QRadar UI. Scope every rule to the named LAB log source so no
production source is affected.

1. **Multiple login failures:** events from LAB Windows Security or LAB Linux Authentication;
   event ID `WIN-4625` or `LNX-AUTH-1001`; same source IP and username; at least 6 in 5 minutes.
2. **Failures followed by success:** the same source IP and username produces the failure event at
   least 4 times, followed by `WIN-4624` or `LNX-AUTH-1002` within 10 minutes.
3. **SQL injection burst:** LAB WAF, event ID `WAF-942100`, at least 5 events from one source in
   2 minutes.
4. **IPS exploit:** LAB IPS, event ID `IPS-2010935`, at least 2 events from one source in 2 minutes.
5. **Privileged group modification:** LAB Windows Security, event IDs `WIN-4728` or `WIN-4732`.
6. **Audit log cleared:** LAB Windows Security `WIN-1102` or LAB Linux Authentication
   `LNX-AUTH-1102`; one event is sufficient in the isolated lab.

Choose a lab-only offense response and a distinctive rule name prefix such as `LAB:`. Never add
reference-set mutations, automated response actions, or production log-source tests.

## Opt-in application LAB_MODE

`LAB_MODE=false` is the default. Production startup rejects `LAB_MODE=true`. When explicitly
enabled in a non-production `.env`, the profile applies exactly:

| Setting | Production default | Lab value |
|---|---:|---:|
| `COLLECTION_INTERVAL_SECONDS` | 300 | 60 |
| `BASELINE_MIN_SAMPLES` | 8 | 4 |
| `ANOMALY_OPEN_AFTER_INTERVALS` | criticality matrix 1–3 | 1 |
| `ANOMALY_RESOLVE_AFTER_INTERVALS` | criticality matrix 2–4 | 2 |
| `OFFENSE_COLLECTION_INTERVAL_SECONDS` | 300 | 60 |

Four completed metric buckets are still required for a reliable baseline, an anomaly needs one
confirmed anomalous bucket, and recovery needs two healthy buckets. This keeps the demo short
without treating a single observation as a baseline or a single healthy interval as recovery.

Everything else keeps its production value. LAB_MODE may shorten *timing* only; it never lowers a
detection threshold:

| Setting | Value in both production and LAB_MODE |
|---|---:|
| `ANOMALY_SPIKE_RATIO` | 2.0 |
| `ANOMALY_DROP_RATIO` | 0.5 |
| `ANOMALY_MIN_ABSOLUTE_DELTA_EVENTS` | 100 |
| `ANOMALY_MIN_BUCKET_EVENTS` | 50 |
| `ANOMALY_DEVIATION_THRESHOLD` | 3.5 |
| `ANOMALY_SILENCE_GRACE_BUCKETS` | 1 |
| `EXPLANATION_COLLECTION_INTERVAL_SECONDS` | 300 |

`rebuild_baselines` runs on a 24-hour beat, so a lab run must trigger it explicitly rather than
wait for it:

```bash
docker compose exec celery-worker \
  celery -A app.workers.celery_app call rebuild_baselines
docker compose exec celery-worker \
  celery -A app.workers.celery_app call collect_metrics
docker compose exec celery-worker \
  celery -A app.workers.celery_app call evaluate_anomalies
docker compose exec celery-worker \
  celery -A app.workers.celery_app call collect_anomaly_explanations
```

After changing `LAB_MODE`, rebuild/restart backend, worker, and Beat. Confirm the loaded schedule
with Celery inspection; do not infer it from the `.env` file alone.

## Common troubleshooting

- Use `tcpdump` or QRadar's packet capture tools only according to local policy; packet arrival is
  distinct from parsing and source routing.
- Check firewalls for UDP/514. For TCP testing, create a separate TCP syslog protocol source and
  use `--protocol tcp`.
- Search raw events by the exact RFC3164 hostname when a display-name search returns nothing.
- Clock skew scenarios intentionally use an old timestamp. Compare receipt time and `devTime`.
- Parsing-degradation intentionally omits required fields; it should keep a valid syslog envelope
  while failing or partially populating DSM properties.
- An empty offense result is valid when no manual LAB rule is deployed. Do not insert database rows
  to simulate an offense.
