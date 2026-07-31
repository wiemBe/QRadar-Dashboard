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
