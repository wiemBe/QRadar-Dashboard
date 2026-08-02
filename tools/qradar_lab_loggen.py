#!/usr/bin/env python3
"""Deterministic synthetic syslog for the isolated QRadar lab.

This is a manual operator tool.  It is deliberately independent of the backend,
Celery and Compose so importing the application can never start a traffic
generator.  Addresses in event bodies come from RFC 5737 documentation ranges;
``--bind-address`` controls only the local socket and never spoofs packets.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import random
import re
import socket
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO

DEFAULT_TARGET = "192.168.122.50"
DEFAULT_PORT = 514
DEFAULT_EPS = 1.0
MAX_SAFE_EPS = 100.0

SYNTHETIC_USERS = (
    "administrator",
    "efe.lab",
    "test.user",
    "svc_backup",
    "svc_sql",
    "compromised.user",
)

PROFILE_ALIASES = {
    "linux": "linux_firewall",
    "windows": "windows_firewall",
}

DEFAULT_DEVICES: dict[str, tuple[str, str]] = {
    "ips": ("ips-gw-01", "10.10.10.10"),
    "waf": ("waf-prod-01", "10.20.30.10"),
    "linux_firewall": ("linux-fw-01", "172.16.20.10"),
    "windows_firewall": ("WIN-FW-01", "192.168.10.10"),
    "linux_auth": ("linux-auth-01", "172.16.20.20"),
    "windows_auth": ("DC-LAB-01", "192.168.10.5"),
    "windows_account": ("DC-LAB-01", "192.168.10.5"),
    "dns": ("dns-lab-01", "172.16.20.53"),
    "proxy": ("proxy-lab-01", "172.16.20.80"),
}

ALTERNATE_DEVICES: dict[str, tuple[tuple[str, str], ...]] = {
    key: (value,) for key, value in DEFAULT_DEVICES.items()
}
ALTERNATE_DEVICES.update(
    {
        "ips": (DEFAULT_DEVICES["ips"], ("ips-gw-02", "10.10.10.11")),
        "waf": (DEFAULT_DEVICES["waf"], ("waf-prod-02", "10.20.30.11")),
        "linux_firewall": (
            DEFAULT_DEVICES["linux_firewall"],
            ("linux-fw-02", "172.16.20.11"),
        ),
        "windows_firewall": (
            DEFAULT_DEVICES["windows_firewall"],
            ("WIN-FW-02", "192.168.10.11"),
        ),
        "linux_auth": (DEFAULT_DEVICES["linux_auth"], ("linux-auth-02", "172.16.20.21")),
        "windows_auth": (DEFAULT_DEVICES["windows_auth"], ("DC-LAB-02", "192.168.10.6")),
        "windows_account": (
            DEFAULT_DEVICES["windows_account"],
            ("DC-LAB-02", "192.168.10.6"),
        ),
        "dns": (DEFAULT_DEVICES["dns"], ("dns-lab-02", "172.16.20.54")),
        "proxy": (DEFAULT_DEVICES["proxy"], ("proxy-lab-02", "172.16.20.81")),
    }
)

PROFILES = tuple(DEFAULT_DEVICES)
SCENARIOS = (
    "normal",
    "brute-force",
    "password-spray",
    "failed-login-then-success",
    "privileged-group-add",
    "account-created",
    "suspicious-powershell",
    "port-scan",
    "ips-exploit-burst",
    "waf-sqli-burst",
    "waf-xss-burst",
    "firewall-deny-burst",
    "volume-spike",
    "repeated-payload",
    "parsing-degradation",
    "timestamp-delay",
    "cardinality-drop",
)

EVENT_IDS = {
    "ips_scan": "IPS-2100365",
    "ips_exploit": "IPS-2010935",
    "ips_sqli": "IPS-2024218",
    "ips_powershell": "IPS-2027757",
    "waf_sqli": "WAF-942100",
    "waf_xss": "WAF-941100",
    "waf_traversal": "WAF-930120",
    "linux_firewall": "LNX-FW-1001",
    "windows_firewall_allow": "WIN-5156",
    "windows_firewall_deny": "WIN-5157",
    "SSH_LOGIN_FAILED": "LNX-AUTH-1001",
    "SSH_LOGIN_SUCCESS": "LNX-AUTH-1002",
    "SUDO_COMMAND": "LNX-AUTH-1003",
    "USER_CREATED": "LNX-AUTH-1004",
    "USER_DELETED": "LNX-AUTH-1005",
    "SERVICE_STARTED": "LNX-AUTH-1006",
    "AUDIT_LOG_CLEARED": "LNX-AUTH-1102",
    "dns": "DNS-QUERY-1001",
    "proxy": "PROXY-HTTP-1001",
}

WINDOWS_EVENTS: dict[int, tuple[str, str, int]] = {
    4624: ("Successful logon", "success", 3),
    4625: ("Failed logon", "failure", 7),
    4720: ("User account created", "created", 7),
    4725: ("User account disabled", "disabled", 6),
    4726: ("User account deleted", "deleted", 7),
    4728: ("Member added to global security group", "added", 8),
    4729: ("Member removed from global security group", "removed", 6),
    4732: ("Member added to local security group", "added", 8),
    4733: ("Member removed from local security group", "removed", 6),
    1102: ("Audit log cleared", "cleared", 10),
    4688: ("Process created", "created", 6),
    7045: ("Service installed", "installed", 9),
}

DOCUMENTATION_PREFIXES = ("192.0.2", "198.51.100", "203.0.113")
PORT_SCAN_PORTS = (21, 22, 23, 25, 53, 80, 135, 139, 443, 445, 3389, 5985)

# ---------------------------------------------------------------- Phase A ---
# The Phase A layer below drives the source-volume anomaly demonstration. It is
# deliberately separate from the recipe scenarios above: those vary *content*,
# these vary *volume over time* against a stable source identity, which is the
# only thing the volume detectors judge.

GENERATOR_VERSION = "1.1.0"

PHASE_A_SCENARIOS = (
    "source-volume-baseline",
    "source-volume-spike",
    "source-volume-drop",
    "source-volume-silence",
    "baseline-spike-recovery",
    "baseline-drop-recovery",
    "multi-source-single-spike",
    "multi-source-single-drop",
)

#: Phase names carried on every Phase A event, so an ingested event can be
#: attributed to a generator phase without trusting wall-clock correlation.
PHASE_BASELINE = "baseline"
PHASE_ANOMALY = "anomaly"
PHASE_RECOVERY = "recovery"

#: Advisory only. The generator never reads application configuration; this is
#: used solely to print an estimated events-per-bucket line in the plan.
ADVISORY_BUCKET_SECONDS = 60.0


@dataclass(frozen=True)
class LabSource:
    """A stable synthetic log source identity.

    ``host`` is what lands in the RFC3164 hostname and must match the QRadar
    log source identifier; a scenario never changes it mid-run.
    """

    host: str
    device_ip: str
    kind: str
    product: str


PHASE_A_SOURCES: dict[str, LabSource] = {
    "lab-fw-volume-01": LabSource(
        "lab-fw-volume-01", "10.20.0.11", "firewall", "SyntheticFirewall"
    ),
    "lab-fw-volume-02": LabSource(
        "lab-fw-volume-02", "10.20.0.12", "firewall", "SyntheticFirewall"
    ),
    "lab-fw-volume-03": LabSource(
        "lab-fw-volume-03", "10.20.0.13", "firewall", "SyntheticFirewall"
    ),
    "lab-waf-volume-01": LabSource("lab-waf-volume-01", "10.20.0.21", "waf", "SyntheticWAF"),
    "lab-ips-volume-01": LabSource("lab-ips-volume-01", "10.20.0.31", "ips", "SyntheticIPS"),
}

#: Default single-source scenarios use the firewall source; silence uses its own
#: so a silence run never erases the history of the spike/drop source.
DEFAULT_PHASE_A_HOST = "lab-fw-volume-01"
DEFAULT_SILENCE_HOST = "lab-fw-volume-02"
DEFAULT_MULTI_HOSTS = ("lab-fw-volume-01", "lab-fw-volume-02", "lab-fw-volume-03")


@dataclass(frozen=True)
class EventTemplate:
    event_id: str
    event_name: str
    action: str
    severity: int


@dataclass(frozen=True)
class Mixture:
    """Dimension pools for one phase of one source kind.

    Baseline mixtures are deliberately wide so an explanation query has several
    contributors to rank; the concentrated and reduced mixtures narrow specific
    dimensions so the resulting evidence is predictable and checkable.
    """

    sources: tuple[str, ...]
    destinations: tuple[str, ...]
    ports: tuple[int, ...]
    templates: tuple[EventTemplate, ...]
    proto: str
    category: str


#: The deterministic contributors the spike concentrates on. Everything the
#: explanation evidence should surface as "what caused the increase" is here.
CONTRIBUTOR_SOURCE_IP = "203.0.113.50"
CONTRIBUTOR_DESTINATION_IP = "10.10.10.20"
CONTRIBUTOR_DESTINATION_PORT = 445
CONTRIBUTOR_ACTION = "DENY"

_FW_ALLOW = EventTemplate("FW_CONNECTION_ALLOWED", "Firewall Connection Allowed", "ALLOW", 3)
_FW_DENY = EventTemplate("FW_CONNECTION_DENIED", "Firewall Denied Connection", "DENY", 6)
_FW_CLOSE = EventTemplate("FW_SESSION_CLOSED", "Firewall Session Closed", "ALLOW", 2)
_WAF_PASS = EventTemplate("WAF_REQUEST_PASSED", "Web Request Passed", "ALLOW", 2)
_WAF_BLOCK = EventTemplate("WAF_REQUEST_BLOCKED", "Web Request Blocked", "DENY", 7)
_IPS_ALERT = EventTemplate("IPS_SIGNATURE_ALERT", "Intrusion Signature Alert", "ALLOW", 5)
_IPS_DROP = EventTemplate("IPS_SIGNATURE_DROP", "Intrusion Signature Drop", "DENY", 8)

BASELINE_MIXTURES: dict[str, Mixture] = {
    "firewall": Mixture(
        sources=("192.0.2.10", "192.0.2.11", "198.51.100.20", "198.51.100.21", "203.0.113.50"),
        destinations=("10.10.10.20", "10.10.10.21", "10.10.10.22"),
        ports=(22, 80, 443, 445, 3389),
        templates=(_FW_ALLOW, _FW_ALLOW, _FW_CLOSE, _FW_DENY),
        proto="TCP",
        category="Firewall",
    ),
    "waf": Mixture(
        sources=("192.0.2.30", "198.51.100.30", "203.0.113.50"),
        destinations=("10.10.10.20", "10.10.10.23"),
        ports=(80, 443),
        templates=(_WAF_PASS, _WAF_PASS, _WAF_BLOCK),
        proto="TCP",
        category="Web",
    ),
    "ips": Mixture(
        sources=("192.0.2.40", "198.51.100.40", "203.0.113.50"),
        destinations=("10.10.10.20", "10.10.10.24"),
        ports=(22, 443, 445),
        templates=(_IPS_ALERT, _IPS_ALERT, _IPS_DROP),
        proto="TCP",
        category="Intrusion",
    ),
}

#: Where the *additional* spike volume goes: one source IP, one destination, one
#: port, one action, one event name. The baseline share keeps flowing through
#: the mixture above, so the increase is a change in share, not a new universe.
CONCENTRATED_MIXTURES: dict[str, Mixture] = {
    "firewall": Mixture(
        sources=(CONTRIBUTOR_SOURCE_IP,),
        destinations=(CONTRIBUTOR_DESTINATION_IP,),
        ports=(CONTRIBUTOR_DESTINATION_PORT,),
        templates=(_FW_DENY,),
        proto="TCP",
        category="Firewall",
    ),
    "waf": Mixture(
        sources=(CONTRIBUTOR_SOURCE_IP,),
        destinations=(CONTRIBUTOR_DESTINATION_IP,),
        ports=(443,),
        templates=(_WAF_BLOCK,),
        proto="TCP",
        category="Web",
    ),
    "ips": Mixture(
        sources=(CONTRIBUTOR_SOURCE_IP,),
        destinations=(CONTRIBUTOR_DESTINATION_IP,),
        ports=(CONTRIBUTOR_DESTINATION_PORT,),
        templates=(_IPS_DROP,),
        proto="TCP",
        category="Intrusion",
    ),
}

#: The drop phase keeps sending valid events at a lower rate from a narrowed
#: pool. Every contributor removed here is a value the explanation evidence
#: should report as disappeared -- never a malformed event.
REDUCED_MIXTURES: dict[str, Mixture] = {
    "firewall": Mixture(
        sources=("192.0.2.10", "192.0.2.11"),
        destinations=("10.10.10.21",),
        ports=(443,),
        templates=(_FW_ALLOW,),
        proto="TCP",
        category="Firewall",
    ),
    "waf": Mixture(
        sources=("192.0.2.30",),
        destinations=("10.10.10.23",),
        ports=(443,),
        templates=(_WAF_PASS,),
        proto="TCP",
        category="Web",
    ),
    "ips": Mixture(
        sources=("192.0.2.40",),
        destinations=("10.10.10.24",),
        ports=(443,),
        templates=(_IPS_ALERT,),
        proto="TCP",
        category="Intrusion",
    ),
}

MIXTURE_MODES = {
    "mixed": BASELINE_MIXTURES,
    "concentrated": CONCENTRATED_MIXTURES,
    "reduced": REDUCED_MIXTURES,
}

DEFAULT_BASELINE_EPS = 2.0
#: Drop scenarios need a baseline tall enough that halving it still clears an
#: absolute-delta guard. 5 EPS is 300 events per 60s bucket; dropping to 1 EPS
#: leaves a delta of 240. This is a generator default, not an application one.
DEFAULT_DROP_BASELINE_EPS = 5.0
DEFAULT_SPIKE_MULTIPLIER = 3.0
DEFAULT_DROP_MULTIPLIER = 0.2
DEFAULT_BASELINE_DURATION = 360.0
DEFAULT_ANOMALY_DURATION = 180.0
DEFAULT_RECOVERY_DURATION = 240.0
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


@dataclass(frozen=True)
class Options:
    target: str = DEFAULT_TARGET
    port: int = DEFAULT_PORT
    protocol: str = "udp"
    eps: float = DEFAULT_EPS
    profiles: tuple[str, ...] = PROFILES
    output_format: str = "leef"
    scenario: str = "normal"
    count: int = 0
    duration: float | None = None
    seed: int | None = None
    stdout: bool = False
    dry_run: bool = False
    output_file: Path | None = None
    fixed_source_ip: str | None = None
    fixed_destination_ip: str | None = None
    fixed_username: str | None = None
    fixed_host: str | None = None
    bind_address: str | None = None
    multiple_devices: bool = False
    allow_high_rate: bool = False
    attempt_count: int = 10
    timestamp_delay: float = 3600.0


@dataclass(frozen=True)
class Event:
    profile: str
    timestamp: datetime
    host: str
    device_ip: str
    event_id: str
    category: str
    action: str
    severity: int
    signature: str
    src: str
    dst: str
    src_port: int
    dst_port: int
    proto: str
    username: str
    request_method: str = "-"
    request: str = "-"
    status_code: int = 0
    rule_id: str = "-"
    vendor_message: str | None = None


def _rfc3164(value: datetime) -> str:
    # UTC keeps seeded fixtures reproducible across operator workstations;
    # devTime also carries the explicit UTC designator.
    return value.astimezone(UTC).strftime("%b %d %H:%M:%S")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _leef_escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def syslog_body(message: str) -> str:
    """Return the payload after the RFC3164 envelope (useful for validation)."""
    return message.split(" ", 4)[4]


class EventGenerator:
    def __init__(
        self,
        options: Options,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.options = options
        # Reproducibility is intentional; this generator never creates secrets.
        self.rng = random.Random(options.seed)  # noqa: S311
        self.clock = clock or (lambda: datetime.now(UTC))
        self._repeated: Event | None = None

    def generate(self, index: int) -> str:
        event = self._event(index)
        if self.options.scenario == "parsing-degradation":
            body = (
                "LEEF:2.0|SyntheticLab|Malformed|1.0|PARSE-DEGRADED|0x09|"
                f"devTime={_iso(event.timestamp)}\tbrokenField\tsrc"
            )
        elif self.options.output_format == "leef":
            body = self._leef(event)
        else:
            body = event.vendor_message or self._vendor(event)
        return f"<134>{_rfc3164(event.timestamp)} {event.host} {body}"

    def _event(self, index: int) -> Event:
        scenario = self.options.scenario
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if scenario == "timestamp-delay":
            now -= timedelta(seconds=self.options.timestamp_delay)
        if scenario == "repeated-payload" and self._repeated is not None:
            return self._repeated

        if scenario == "normal" or scenario in {
            "volume-spike",
            "cardinality-drop",
            "repeated-payload",
            "parsing-degradation",
            "timestamp-delay",
        }:
            profile = self.rng.choice(self.options.profiles)
            event = self._normal_event(profile, now, index)
            if scenario == "volume-spike":
                event = replace(event, category="volume-spike", signature="Synthetic volume spike")
            elif scenario == "cardinality-drop":
                event = replace(
                    event,
                    src=self.options.fixed_source_ip or "198.51.100.88",
                    username=self.options.fixed_username or "svc_backup",
                    category="cardinality-drop",
                )
            if scenario == "repeated-payload":
                self._repeated = event
            return event
        return self._scenario_event(scenario, now, index)

    def _identity(self, profile: str) -> tuple[str, str]:
        devices = (
            ALTERNATE_DEVICES[profile]
            if self.options.multiple_devices
            else (DEFAULT_DEVICES[profile],)
        )
        host, address = self.rng.choice(devices)
        return self.options.fixed_host or host, address

    def _public_ip(self) -> str:
        if self.options.fixed_source_ip:
            return self.options.fixed_source_ip
        return f"{self.rng.choice(DOCUMENTATION_PREFIXES)}.{self.rng.randint(1, 254)}"

    def _destination(self, device_ip: str) -> str:
        return self.options.fixed_destination_ip or device_ip

    def _username(self) -> str:
        return self.options.fixed_username or self.rng.choice(SYNTHETIC_USERS)

    def _base(
        self,
        profile: str,
        now: datetime,
        *,
        event_id: str,
        category: str,
        action: str,
        severity: int,
        signature: str,
        src: str | None = None,
        dst_port: int = 0,
        proto: str = "TCP",
        username: str | None = None,
    ) -> Event:
        host, device_ip = self._identity(profile)
        return Event(
            profile=profile,
            timestamp=now,
            host=host,
            device_ip=device_ip,
            event_id=event_id,
            category=category,
            action=action,
            severity=severity,
            signature=signature,
            src=src or self._public_ip(),
            dst=self._destination(device_ip),
            src_port=self.rng.randint(1024, 65535),
            dst_port=dst_port,
            proto=proto,
            username=username or self._username(),
            rule_id=event_id,
        )

    def _normal_event(self, profile: str, now: datetime, index: int) -> Event:
        if profile == "ips":
            key, signature, severity = self.rng.choice(
                (
                    ("ips_scan", "ET SCAN Nmap SYN Scan", 7),
                    ("ips_exploit", "ET EXPLOIT Apache Struts Attempt", 9),
                    ("ips_sqli", "ET WEB_SERVER SQL Injection Attempt", 8),
                    ("ips_powershell", "ET MALWARE PowerShell Download", 10),
                )
            )
            return self._base(
                profile,
                now,
                event_id=EVENT_IDS[key],
                category="intrusion",
                action="blocked",
                severity=severity,
                signature=signature,
                dst_port=self.rng.choice((22, 80, 443, 445)),
            )
        if profile == "waf":
            key, signature, request = self.rng.choice(
                (
                    ("waf_sqli", "SQL Injection Attack Detected", "/login?id=1%27OR%271%27=%271"),
                    ("waf_xss", "XSS Attack Detected", "/search?q=%3Cscript%3Elab%3C/script%3E"),
                    ("waf_traversal", "Path Traversal Attack", "/download?file=../../etc/passwd"),
                )
            )
            event = self._base(
                profile,
                now,
                event_id=EVENT_IDS[key],
                category="web-attack",
                action="blocked",
                severity=9,
                signature=signature,
                dst_port=443,
            )
            return replace(event, request_method="GET", request=request, status_code=403)
        if profile == "linux_firewall":
            event = self._base(
                profile,
                now,
                event_id=EVENT_IDS["linux_firewall"],
                category="firewall",
                action=self.rng.choice(("blocked", "blocked", "allowed")),
                severity=6,
                signature="Linux firewall decision",
                dst_port=self.rng.choice((22, 80, 443, 445, 5432)),
            )
            return replace(event, vendor_message=self._linux_firewall_vendor(event))
        if profile == "windows_firewall":
            denied = self.rng.choice((False, False, True))
            event_id = EVENT_IDS["windows_firewall_deny" if denied else "windows_firewall_allow"]
            event = self._base(
                profile,
                now,
                event_id=event_id,
                category="firewall",
                action="blocked" if denied else "allowed",
                severity=6 if denied else 3,
                signature="Windows Filtering Platform connection",
                dst_port=self.rng.choice((53, 80, 443, 445, 3389)),
            )
            return replace(
                event, vendor_message=self._windows_vendor(event, int(event_id.split("-")[1]))
            )
        if profile == "linux_auth":
            kind = self.rng.choice(tuple(k for k in EVENT_IDS if k.isupper()))
            action = "failure" if kind == "SSH_LOGIN_FAILED" else "success"
            event = self._base(
                profile,
                now,
                event_id=EVENT_IDS[kind],
                category="authentication",
                action=action,
                severity=7 if "FAILED" in kind or "CLEARED" in kind else 4,
                signature=kind.replace("_", " ").title(),
                dst_port=22,
            )
            return replace(event, vendor_message=self._linux_auth_vendor(event, kind))
        if profile == "windows_auth":
            win_event_id = self.rng.choice((4624, 4625, 4625))
            return self._windows_event(profile, now, win_event_id)
        if profile == "windows_account":
            win_event_id = self.rng.choice(
                (4720, 4725, 4726, 4728, 4729, 4732, 4733, 1102, 4688, 7045)
            )
            return self._windows_event(profile, now, win_event_id)
        if profile == "dns":
            event = self._base(
                profile,
                now,
                event_id=EVENT_IDS["dns"],
                category="dns",
                action="resolved",
                severity=3,
                signature="Synthetic DNS query",
                dst_port=53,
                proto="UDP",
            )
            return replace(event, request="lab.example.test", status_code=0)
        event = self._base(
            "proxy",
            now,
            event_id=EVENT_IDS["proxy"],
            category="web-proxy",
            action="allowed",
            severity=3,
            signature="Synthetic proxy request",
            dst_port=443,
        )
        return replace(
            event, request_method="GET", request="https://portal.example.test/lab", status_code=200
        )

    def _windows_event(self, profile: str, now: datetime, event_id: int) -> Event:
        signature, action, severity = WINDOWS_EVENTS[event_id]
        event = self._base(
            profile,
            now,
            event_id=f"WIN-{event_id}",
            category="windows-security",
            action=action,
            severity=severity,
            signature=signature,
            dst_port=445,
        )
        return replace(event, vendor_message=self._windows_vendor(event, event_id))

    def _scenario_event(self, scenario: str, now: datetime, index: int) -> Event:
        stable_src = self.options.fixed_source_ip or "198.51.100.44"
        stable_user = self.options.fixed_username or "compromised.user"
        if scenario == "brute-force":
            return self._auth_scenario(now, 4625, stable_src, stable_user)
        if scenario == "password-spray":
            username = self.options.fixed_username or SYNTHETIC_USERS[index % len(SYNTHETIC_USERS)]
            return self._auth_scenario(now, 4625, stable_src, username)
        if scenario == "failed-login-then-success":
            event_id = (
                4624
                if index % (self.options.attempt_count + 1) == self.options.attempt_count
                else 4625
            )
            return self._auth_scenario(now, event_id, stable_src, stable_user)
        if scenario == "privileged-group-add":
            return self._auth_scenario(
                now, 4728, stable_src, stable_user, profile="windows_account"
            )
        if scenario == "account-created":
            return self._auth_scenario(
                now, 4720, stable_src, stable_user, profile="windows_account"
            )
        if scenario == "suspicious-powershell":
            event = self._auth_scenario(
                now, 4688, stable_src, stable_user, profile="windows_account"
            )
            return replace(event, request="powershell.exe -NoProfile -EncodedCommand LABONLY")
        if scenario == "port-scan":
            event = self._base(
                "linux_firewall",
                now,
                event_id=EVENT_IDS["linux_firewall"],
                category="port-scan",
                action="blocked",
                severity=8,
                signature="Synthetic sequential port scan",
                src=stable_src,
                dst_port=PORT_SCAN_PORTS[index % len(PORT_SCAN_PORTS)],
                username=stable_user,
            )
            return replace(event, vendor_message=self._linux_firewall_vendor(event))
        if scenario == "ips-exploit-burst":
            return self._base(
                "ips",
                now,
                event_id=EVENT_IDS["ips_exploit"],
                category="intrusion",
                action="blocked",
                severity=9,
                signature="ET EXPLOIT Apache Struts Attempt",
                src=stable_src,
                dst_port=443,
                username=stable_user,
            )
        if scenario in {"waf-sqli-burst", "waf-xss-burst"}:
            sqli = scenario == "waf-sqli-burst"
            event = self._base(
                "waf",
                now,
                event_id=EVENT_IDS["waf_sqli" if sqli else "waf_xss"],
                category="web-attack",
                action="blocked",
                severity=9,
                signature="SQL Injection Attack Detected" if sqli else "XSS Attack Detected",
                src=stable_src,
                dst_port=443,
                username=stable_user,
            )
            request = (
                "/login?id=1%27OR%271%27=%271" if sqli else "/search?q=%3Cscript%3Elab%3C/script%3E"
            )
            return replace(event, request_method="GET", request=request, status_code=403)
        if scenario == "firewall-deny-burst":
            event = self._base(
                "linux_firewall",
                now,
                event_id=EVENT_IDS["linux_firewall"],
                category="firewall",
                action="blocked",
                severity=7,
                signature="Linux firewall deny burst",
                src=stable_src,
                dst_port=445,
                username=stable_user,
            )
            return replace(event, vendor_message=self._linux_firewall_vendor(event))
        raise ValueError(f"unsupported scenario: {scenario}")

    def _auth_scenario(
        self,
        now: datetime,
        event_id: int,
        src: str,
        username: str,
        *,
        profile: str = "windows_auth",
    ) -> Event:
        event = self._windows_event(profile, now, event_id)
        return replace(event, src=src, username=username)

    @staticmethod
    def _leef(event: Event) -> str:
        fields = {
            "devTime": _iso(event.timestamp),
            "src": event.src,
            "dst": event.dst,
            "srcPort": event.src_port,
            "dstPort": event.dst_port,
            "proto": event.proto,
            "usrName": event.username,
            "action": event.action,
            "severity": event.severity,
            "category": event.category,
            "eventId": event.event_id,
            "deviceHostName": event.host,
            "deviceAddress": event.device_ip,
            "requestMethod": event.request_method,
            "request": event.request,
            "statusCode": event.status_code,
            "ruleId": event.rule_id,
            "signature": event.signature,
        }
        extension = "\t".join(f"{key}={_leef_escape(value)}" for key, value in fields.items())
        return f"LEEF:2.0|SyntheticLab|QRadarLab|1.0|{event.event_id}|0x09|{extension}"

    def _vendor(self, event: Event) -> str:
        if event.profile in {"ips", "waf"}:
            return (
                f"CEF:0|SyntheticLab|{event.profile}|1.0|{event.event_id}|{event.signature}|"
                f"{event.severity}|rt={_iso(event.timestamp)} src={event.src} dst={event.dst} "
                f"spt={event.src_port} dpt={event.dst_port} proto={event.proto} "
                f"suser={event.username} act={event.action} requestMethod={event.request_method} "
                f"request={event.request} outcome={event.status_code}"
            )
        if event.profile == "linux_firewall":
            return self._linux_firewall_vendor(event)
        if event.profile.startswith("windows"):
            return self._windows_vendor(event, int(event.event_id.split("-")[1]))
        if event.profile == "linux_auth":
            return (
                event.vendor_message
                or f"sshd: authentication {event.action} for {event.username} from {event.src}"
            )
        if event.profile == "dns":
            return f"named: client {event.src}#{event.src_port}: query: {event.request} IN A +E"
        return (
            f"proxy: action={event.action} src={event.src} user={event.username} "
            f"method={event.request_method} url={event.request} status={event.status_code}"
        )

    @staticmethod
    def _linux_firewall_vendor(event: Event) -> str:
        decision = "UFW BLOCK" if event.action == "blocked" else "UFW ALLOW"
        return (
            f"kernel: [{decision}] IN=ens160 OUT= SRC={event.src} DST={event.dst} "
            f"PROTO={event.proto} SPT={event.src_port} DPT={event.dst_port}"
        )

    @staticmethod
    def _linux_auth_vendor(event: Event, kind: str) -> str:
        if kind == "SSH_LOGIN_FAILED":
            return (
                f"sshd: Failed password for {event.username} from {event.src} "
                f"port {event.src_port} ssh2"
            )
        if kind == "SSH_LOGIN_SUCCESS":
            return (
                f"sshd: Accepted password for {event.username} from {event.src} "
                f"port {event.src_port} ssh2"
            )
        if kind == "SUDO_COMMAND":
            return f"sudo: {event.username} : COMMAND=/usr/bin/id"
        if kind == "USER_CREATED":
            return f"useradd: new user: name={event.username}, UID=2001"
        if kind == "USER_DELETED":
            return f"userdel: delete user '{event.username}'"
        if kind == "SERVICE_STARTED":
            return "systemd: Started Synthetic Lab Service."
        return f"auditd: audit log cleared by {event.username}"

    @staticmethod
    def _windows_vendor(event: Event, event_id: int) -> str:
        signature = WINDOWS_EVENTS.get(event_id, (event.signature, event.action, event.severity))[0]
        extra = ""
        if event_id == 4688:
            extra = " NewProcessName=C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
        elif event_id in {4728, 4732}:
            extra = " GroupName=Lab Administrators"
        elif event_id == 7045:
            extra = " ServiceName=LabSyntheticService"
        return (
            f"WinEventLog: Microsoft-Windows-Security-Auditing EventID={event_id} "
            f"Computer={event.host} Message={signature} SubjectUserName={event.username} "
            f"IpAddress={event.src}{extra}"
        )


@dataclass(frozen=True)
class SourceRate:
    """One source's requested rate and dimension mode inside one phase."""

    host: str
    eps: float
    mode: str = "mixed"


@dataclass(frozen=True)
class PhaseSpec:
    name: str
    duration: float
    sources: tuple[SourceRate, ...]

    @property
    def total_eps(self) -> float:
        return sum(rate.eps for rate in self.sources)

    @property
    def expected_events(self) -> int:
        return sum(int(rate.eps * self.duration) for rate in self.sources)


@dataclass(frozen=True)
class ScenarioPlan:
    run_id: str
    scenario: str
    hosts: tuple[str, ...]
    phases: tuple[PhaseSpec, ...]

    @property
    def expected_events(self) -> int:
        return sum(phase.expected_events for phase in self.phases)

    @property
    def duration(self) -> float:
        return sum(phase.duration for phase in self.phases)


@dataclass(frozen=True)
class PhaseAOptions:
    """Options for the volume scenarios. Kept apart from `Options` so the two
    families cannot silently inherit each other's defaults."""

    scenario: str
    target: str = DEFAULT_TARGET
    port: int = DEFAULT_PORT
    protocol: str = "udp"
    output_format: str = "leef"
    run_id: str | None = None
    seed: int | None = None
    baseline_eps: float | None = None
    baseline_duration: float = DEFAULT_BASELINE_DURATION
    anomaly_eps: float | None = None
    anomaly_multiplier: float | None = None
    anomaly_duration: float = DEFAULT_ANOMALY_DURATION
    recovery_duration: float = DEFAULT_RECOVERY_DURATION
    count: int = 0
    fixed_host: str | None = None
    fixed_source_ip: str | None = None
    fixed_destination_ip: str | None = None
    fixed_destination_port: int | None = None
    fixed_action: str | None = None
    bind_address: str | None = None
    allow_high_rate: bool = False
    stdout: bool = False
    dry_run: bool = False
    output_file: Path | None = None
    summary_json: Path | None = None


@dataclass(frozen=True)
class LabEvent:
    timestamp: datetime
    source: LabSource
    event_id: str
    event_name: str
    src: str
    dst: str
    src_port: int
    dst_port: int
    proto: str
    action: str
    severity: int
    category: str
    run_id: str
    scenario: str
    phase: str
    #: Device-shaped detail fields for the source kind. Ordered so a seeded run
    #: renders byte-for-byte identically.
    extras: tuple[tuple[str, str], ...] = ()


def is_phase_a(scenario: str) -> bool:
    return scenario in PHASE_A_SCENARIOS


def _is_drop_scenario(scenario: str) -> bool:
    return "drop" in scenario


def default_run_id(now: datetime) -> str:
    return f"labrun-{now.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def resolve_rates(options: PhaseAOptions) -> tuple[float, float]:
    """Return (baseline EPS, anomaly EPS) for the scenario.

    `--anomaly-eps` wins over `--anomaly-multiplier`; with neither, the
    direction of the scenario picks the default multiplier.
    """
    drop = _is_drop_scenario(options.scenario)
    baseline = options.baseline_eps
    if baseline is None:
        baseline = DEFAULT_DROP_BASELINE_EPS if drop else DEFAULT_BASELINE_EPS
    if options.anomaly_eps is not None:
        return baseline, options.anomaly_eps
    multiplier = options.anomaly_multiplier
    if multiplier is None:
        multiplier = DEFAULT_DROP_MULTIPLIER if drop else DEFAULT_SPIKE_MULTIPLIER
    return baseline, baseline * multiplier


def scenario_hosts(options: PhaseAOptions) -> tuple[str, ...]:
    scenario = options.scenario
    if scenario.startswith("multi-source"):
        if options.fixed_host:
            raise ValueError("--fixed-host cannot be used with a multi-source scenario")
        return DEFAULT_MULTI_HOSTS
    if options.fixed_host:
        return (options.fixed_host,)
    if scenario == "source-volume-silence":
        return (DEFAULT_SILENCE_HOST,)
    return (DEFAULT_PHASE_A_HOST,)


def build_plan(options: PhaseAOptions, *, now: datetime | None = None) -> ScenarioPlan:
    """Expand a scenario name into explicit per-phase, per-source rates."""
    validate_phase_a_options(options)
    hosts = scenario_hosts(options)
    for host in hosts:
        if host not in PHASE_A_SOURCES:
            raise ValueError(f"unknown Phase A source: {host}")
    baseline_eps, anomaly_eps = resolve_rates(options)
    anomaly_mode = "reduced" if _is_drop_scenario(options.scenario) else "concentrated"
    run_id = options.run_id or default_run_id(now or datetime.now(UTC))

    def steady(name: str, duration: float) -> PhaseSpec:
        return PhaseSpec(
            name=name,
            duration=duration,
            sources=tuple(SourceRate(host, baseline_eps) for host in hosts),
        )

    def changed(duration: float) -> PhaseSpec:
        # Only the first source changes; the rest hold baseline. That is the
        # whole point of the multi-source scenarios: detector isolation.
        rates = [SourceRate(hosts[0], anomaly_eps, anomaly_mode)]
        rates += [SourceRate(host, baseline_eps) for host in hosts[1:]]
        return PhaseSpec(name=PHASE_ANOMALY, duration=duration, sources=tuple(rates))

    scenario = options.scenario
    phases: tuple[PhaseSpec, ...]
    if scenario in {"source-volume-baseline", "source-volume-silence"}:
        phases = (steady(PHASE_BASELINE, options.baseline_duration),)
    elif scenario in {"source-volume-spike", "source-volume-drop"}:
        phases = (changed(options.anomaly_duration),)
    else:
        phases = (
            steady(PHASE_BASELINE, options.baseline_duration),
            changed(options.anomaly_duration),
            steady(PHASE_RECOVERY, options.recovery_duration),
        )

    plan = ScenarioPlan(run_id=run_id, scenario=scenario, hosts=hosts, phases=phases)
    _validate_plan_rates(plan, options)
    return plan


def _validate_plan_rates(plan: ScenarioPlan, options: PhaseAOptions) -> None:
    for phase in plan.phases:
        for rate in phase.sources:
            if rate.eps <= 0:
                raise ValueError(f"phase {phase.name} resolved to a non-positive EPS")
        if phase.total_eps > MAX_SAFE_EPS and not options.allow_high_rate:
            raise ValueError(
                f"phase {phase.name} needs {phase.total_eps:g} aggregate EPS; "
                "rates over 100 EPS require --allow-high-rate"
            )


def validate_phase_a_options(options: PhaseAOptions) -> None:
    if options.scenario not in PHASE_A_SCENARIOS:
        raise ValueError(f"unsupported Phase A scenario: {options.scenario}")
    if not 1 <= options.port <= 65535:
        raise ValueError("--port must be between 1 and 65535")
    if options.count < 0:
        raise ValueError("--count must be zero or greater")
    if options.baseline_eps is not None and options.baseline_eps <= 0:
        raise ValueError("--baseline-eps must be greater than 0")
    if options.anomaly_eps is not None and options.anomaly_eps <= 0:
        raise ValueError("--anomaly-eps must be greater than 0")
    if options.anomaly_multiplier is not None and options.anomaly_multiplier <= 0:
        raise ValueError("--anomaly-multiplier must be greater than 0")
    for name, value in (
        ("--baseline-duration", options.baseline_duration),
        ("--anomaly-duration", options.anomaly_duration),
        ("--recovery-duration", options.recovery_duration),
    ):
        if value <= 0:
            raise ValueError(f"{name} must be greater than 0")
    if options.run_id is not None and not RUN_ID_PATTERN.fullmatch(options.run_id):
        raise ValueError("--run-id must be 1-64 characters of [A-Za-z0-9._-]")
    port = options.fixed_destination_port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("--fixed-destination-port must be between 1 and 65535")
    if options.fixed_action is not None and options.fixed_action not in {"ALLOW", "DENY"}:
        raise ValueError("--fixed-action must be ALLOW or DENY")
    if options.fixed_host is not None and options.fixed_host not in PHASE_A_SOURCES:
        raise ValueError(
            "--fixed-host must be one of: " + ", ".join(sorted(PHASE_A_SOURCES))
        )
    for flag, address in (
        ("--fixed-source-ip", options.fixed_source_ip),
        ("--fixed-destination-ip", options.fixed_destination_ip),
        ("--bind-address", options.bind_address),
    ):
        if address is not None:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError as exc:
                raise ValueError(f"{flag} must be a valid IPv4 address") from exc
            if parsed.version != 4:
                raise ValueError(f"{flag} must be an IPv4 address")


class PhaseAGenerator:
    """Builds Phase A events for a plan.

    The RNG is drawn in scheduled order, so a seed reproduces the whole run's
    field selection; only timestamps follow the wall clock.
    """

    def __init__(
        self,
        options: PhaseAOptions,
        plan: ScenarioPlan,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.options = options
        self.plan = plan
        self.rng = random.Random(options.seed)  # noqa: S311
        self.clock = clock or (lambda: datetime.now(UTC))
        baseline_eps, _ = resolve_rates(options)
        self._baseline_eps = baseline_eps
        self._emitted: dict[tuple[str, str], int] = {}

    def _mode_for(self, rate: SourceRate) -> str:
        """Split a concentrated source between background and contributor mix.

        The baseline share of the elevated rate keeps its normal spread; only
        the *additional* volume concentrates. Deterministic, not probabilistic,
        so a seeded run and a live run distribute identically.
        """
        if rate.mode != "concentrated":
            return rate.mode
        share = min(1.0, self._baseline_eps / rate.eps) if rate.eps > 0 else 1.0
        key = (rate.host, "concentrated")
        seen = self._emitted.get(key, 0)
        self._emitted[key] = seen + 1
        background = int((seen + 1) * share) - int(seen * share) > 0
        return "mixed" if background else "concentrated"

    def build(self, rate: SourceRate, phase: str) -> LabEvent:
        source = PHASE_A_SOURCES[rate.host]
        mixture = MIXTURE_MODES[self._mode_for(rate)][source.kind]
        template = self.rng.choice(mixture.templates)
        src = self.options.fixed_source_ip or self.rng.choice(mixture.sources)
        dst = self.options.fixed_destination_ip or self.rng.choice(mixture.destinations)
        dst_port = self.options.fixed_destination_port or self.rng.choice(mixture.ports)
        src_port = self.rng.randint(1024, 65535)
        action = self.options.fixed_action or template.action
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        extras = self._extras(source.kind, action)
        return LabEvent(
            extras=extras,
            timestamp=now,
            source=source,
            event_id=template.event_id,
            event_name=template.event_name,
            src=src,
            dst=dst,
            src_port=src_port,
            dst_port=dst_port,
            proto=mixture.proto,
            action=action,
            severity=template.severity,
            category=mixture.category,
            run_id=self.plan.run_id,
            scenario=self.plan.scenario,
            phase=phase,
        )

    def _extras(self, kind: str, action: str) -> tuple[tuple[str, str], ...]:
        """Device-shaped detail fields.

        Volume detection never reads these, but an analyst looking at a raw
        event does, and a DSM mapped against a payload with three fields is not
        a DSM that will survive contact with a real appliance.
        """
        rng = self.rng
        if kind == "firewall":
            flags = "SYN" if action == "DENY" else rng.choice(("SYN ACK", "ACK", "PSH ACK"))
            allowed = action == "ALLOW"
            return (
                ("direction", "inbound"),
                ("ifName", "ens160"),
                ("policyId", f"FW-{rng.randint(100, 199)}"),
                ("ruleName", "lab-perimeter-in" if allowed else "lab-perimeter-drop"),
                ("tcpFlags", flags),
                ("ttl", str(rng.randint(48, 128))),
                ("pktLen", str(rng.choice((40, 52, 60, 72, 84, 128)))),
                ("bytesIn", str(rng.randint(64, 4096) if allowed else 0)),
                ("bytesOut", str(rng.randint(64, 16384) if allowed else 0)),
                ("sessionId", str(rng.randint(1_000_000, 9_999_999))),
            )
        if kind == "waf":
            blocked = action == "DENY"
            url, rule_id = (
                ("/api/v1/report?id=1%27+OR+%271%27%3D%271", "942100")
                if blocked
                else (rng.choice(("/", "/portal/login", "/api/v1/status", "/static/app.css")),
                      "-")
            )
            return (
                ("httpMethod", rng.choice(("GET", "GET", "POST"))),
                ("url", url),
                ("virtualHost", "portal.lab.test"),
                ("userAgent", rng.choice(
                    (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "curl/8.7.1",
                        "python-requests/2.32",
                    )
                ) if not blocked else "sqlmap/1.8"),
                ("responseCode", "403" if blocked else rng.choice(("200", "200", "304", "404"))),
                ("bytesIn", str(rng.randint(120, 2048))),
                ("bytesOut", str(0 if blocked else rng.randint(200, 48000))),
                ("ruleId", rule_id),
            )
        dropped = action == "DENY"
        return (
            ("sigId", "2010935" if dropped else str(rng.choice((2100365, 2034647, 2013504)))),
            ("classification", "attempted-admin" if dropped else "policy-violation"),
            ("priority", str(1 if dropped else rng.choice((2, 3)))),
            ("flowId", str(rng.randint(1_000_000_000, 9_999_999_999))),
            ("pktCount", str(rng.randint(1, 250))),
            ("direction", "inbound"),
            ("ifName", "ens160"),
        )

    def render(self, event: LabEvent) -> str:
        body = (
            render_phase_a_leef(event)
            if self.options.output_format == "leef"
            else render_phase_a_vendor(event)
        )
        return f"<134>{_rfc3164(event.timestamp)} {event.source.host} {body}"


def render_phase_a_leef(event: LabEvent) -> str:
    fields = {
        # Epoch milliseconds, not ISO text: QRadar reads it without a
        # devTimeFormat and without assuming a log-source timezone, so a
        # generated event can never land in the wrong metric bucket.
        "devTime": int(event.timestamp.timestamp() * 1000),
        "eventId": event.event_id,
        "eventName": event.event_name,
        "deviceHostName": event.source.host,
        "deviceAddress": event.source.device_ip,
        "src": event.src,
        "dst": event.dst,
        "srcPort": event.src_port,
        "dstPort": event.dst_port,
        "proto": event.proto,
        "action": event.action,
        "severity": event.severity,
        "category": event.category,
        "runId": event.run_id,
        "scenario": event.scenario,
        "phase": event.phase,
        # `sev` and `cat` are the keys the Universal LEEF DSM maps without a
        # custom property; the spelled-out names above stay for the operator
        # reading a raw payload.
        "sev": event.severity,
        "cat": event.category,
    }
    fields.update(dict(event.extras))
    extension = "\t".join(f"{key}={_leef_escape(value)}" for key, value in fields.items())
    header = f"LEEF:2.0|QRadarLab|{_leef_header(event.source.product)}|1.0"
    return f"{header}|{_leef_header(event.event_id)}|0x09|{extension}"


def _leef_header(value: str) -> str:
    """Header fields are pipe-delimited, so a literal pipe must be escaped."""
    return str(value).replace("\\", "\\\\").replace("|", "\\|")


def render_phase_a_vendor(event: LabEvent) -> str:
    """Vendor-shaped text for parser testing. One syslog tag, never two."""
    extra = dict(event.extras)
    trailer = f"runId={event.run_id} scenario={event.scenario} phase={event.phase}"
    if event.source.kind == "firewall":
        decision = "UFW BLOCK" if event.action == "DENY" else "UFW ALLOW"
        return (
            f"kernel: [{decision}] IN={extra.get('ifName', 'ens160')} OUT= "
            f"SRC={event.src} DST={event.dst} LEN={extra.get('pktLen', '60')} "
            f"TOS=0x00 PREC=0x00 TTL={extra.get('ttl', '64')} DF "
            f"PROTO={event.proto} SPT={event.src_port} DPT={event.dst_port} "
            f"WINDOW=64240 RES=0x00 {extra.get('tcpFlags', 'SYN')} URGP=0 {trailer}"
        )
    if event.source.kind == "waf":
        return (
            f"ModSecurity: [client {event.src}] ModSecurity: "
            f"{'Access denied with code 403' if event.action == 'DENY' else 'Warning'}. "
            f"[file \"/etc/modsecurity/crs.conf\"] [id \"{extra.get('ruleId', '-')}\"] "
            f"[msg \"{event.event_name}\"] [severity \"{event.severity}\"] "
            f"[hostname \"{extra.get('virtualHost', event.dst)}\"] "
            f"[uri \"{extra.get('url', '/')}\"] [unique_id \"{extra.get('bytesIn', '0')}\"] "
            f"{trailer}"
        )
    return (
        f"suricata: [{'Drop' if event.action == 'DENY' else 'Alert'}] [**] "
        f"[1:{extra.get('sigId', '0')}:1] {event.event_name} [**] "
        f"[Classification: {extra.get('classification', '-')}] "
        f"[Priority: {extra.get('priority', '3')}] {{{event.proto}}} "
        f"{event.src}:{event.src_port} -> {event.dst}:{event.dst_port} {trailer}"
    )


def build_schedule(plan: ScenarioPlan) -> list[tuple[float, str, SourceRate]]:
    """Absolute offsets, in seconds from run start, for every planned event.

    Offsets are computed once from the plan rather than accumulated during the
    run, so a slow send can never make the schedule drift.
    """
    entries: list[tuple[float, str, SourceRate]] = []
    phase_start = 0.0
    for phase in plan.phases:
        for rate in phase.sources:
            total = int(rate.eps * phase.duration)
            interval = 1.0 / rate.eps
            for index in range(total):
                entries.append((phase_start + index * interval, phase.name, rate))
        phase_start += phase.duration
    entries.sort(key=lambda item: (item[0], item[2].host))
    return entries


def format_plan(options: PhaseAOptions, plan: ScenarioPlan) -> str:
    """Sanitized pre-flight plan. Never contains a credential."""
    lines = [
        "qradar-lab-loggen Phase A plan",
        f"  run-id       {plan.run_id}",
        f"  scenario     {plan.scenario}",
        f"  target       {options.target}:{options.port}/{options.protocol}"
        + (" (dry-run, no packets)" if options.dry_run else ""),
        f"  format       {options.output_format}",
        f"  seed         {options.seed if options.seed is not None else 'unseeded'}",
        f"  sources      {', '.join(plan.hosts)}",
    ]
    for phase in plan.phases:
        rates = ", ".join(f"{r.host}@{r.eps:g}eps/{r.mode}" for r in phase.sources)
        lines.append(
            f"  phase {phase.name:<9} duration={phase.duration:g}s "
            f"events={phase.expected_events} rates={rates}"
        )
    lines.append(f"  total        {plan.expected_events} events over {plan.duration:g}s")
    baseline = next((p for p in plan.phases if p.name == PHASE_BASELINE), None)
    anomaly = next((p for p in plan.phases if p.name == PHASE_ANOMALY), None)
    if baseline is not None and anomaly is not None:
        changed = anomaly.sources[0]
        base_rate = next(r for r in baseline.sources if r.host == changed.host)
        per_bucket_base = base_rate.eps * ADVISORY_BUCKET_SECONDS
        per_bucket_anom = changed.eps * ADVISORY_BUCKET_SECONDS
        ratio = per_bucket_anom / per_bucket_base if per_bucket_base else float("inf")
        lines.append(
            f"  advisory     at {ADVISORY_BUCKET_SECONDS:g}s buckets {changed.host} moves "
            f"{per_bucket_base:.0f} -> {per_bucket_anom:.0f} events/bucket "
            f"(ratio {ratio:.2f}, delta {abs(per_bucket_anom - per_bucket_base):.0f})"
        )
    if options.count:
        lines.append(f"  count cap    {options.count} events")
    return "\n".join(lines)


@dataclass
class PhaseReport:
    name: str
    requested_eps: dict[str, float]
    attempted: int = 0
    sent: int = 0
    started_at: str | None = None
    ended_at: str | None = None


def run_phase_a(
    options: PhaseAOptions,
    *,
    plan: ScenarioPlan | None = None,
    sender_factory: Callable[..., SyslogSender] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> dict:
    """Run one Phase A scenario and return its sanitized manifest."""
    if sender_factory is None:
        sender_factory = SyslogSender
    wall = clock or (lambda: datetime.now(UTC))
    plan = plan or build_plan(options, now=wall())
    generator = PhaseAGenerator(options, plan, clock=wall)
    schedule = build_schedule(plan)

    print(format_plan(options, plan), file=stderr, flush=True)

    reports: dict[str, PhaseReport] = {}
    for phase in plan.phases:
        reports[phase.name] = PhaseReport(
            name=phase.name,
            requested_eps={rate.host: rate.eps for rate in phase.sources},
        )
    errors: list[str] = []

    sender: SyslogSender | None = None
    file_handle: TextIO | None = None
    if options.output_file:
        options.output_file.parent.mkdir(parents=True, exist_ok=True)
        file_handle = options.output_file.open("a", encoding="utf-8")
    if not options.dry_run:
        sender = sender_factory(
            options.target,
            options.port,
            options.protocol,
            bind_address=options.bind_address,
        )

    started_wall = wall()
    started = monotonic()
    emitted = 0
    try:
        for offset, phase_name, rate in schedule:
            if options.count and emitted >= options.count:
                break
            delay = started + offset - monotonic()
            if delay > 0:
                sleeper(delay)
            report = reports[phase_name]
            if report.started_at is None:
                report.started_at = _iso(wall())
            event = generator.build(rate, phase_name)
            message = generator.render(event)
            report.attempted += 1
            emitted += 1
            try:
                if sender is not None:
                    sender.send(message)
                    # A dry run attempts events but sends none, and the
                    # manifest must not claim otherwise.
                    report.sent += 1
            except OSError as exc:
                # A transport error must not abort a timed scenario: the
                # remaining phases still carry evidence, and the manifest
                # records exactly how many events were lost.
                errors.append(f"{phase_name}: {type(exc).__name__}: {exc}")
            if options.stdout:
                print(message, file=stdout, flush=True)
            if file_handle is not None:
                file_handle.write(message + "\n")
                file_handle.flush()
            report.ended_at = _iso(wall())
    except KeyboardInterrupt:
        errors.append("interrupted by operator")
    finally:
        if sender is not None:
            sender.close()
        if file_handle is not None:
            file_handle.close()

    manifest = {
        "generator_version": GENERATOR_VERSION,
        "run_id": plan.run_id,
        "scenario": plan.scenario,
        "sources": list(plan.hosts),
        "format": options.output_format,
        "target": f"{options.target}:{options.port}",
        "protocol": options.protocol,
        "dry_run": options.dry_run,
        "seed": options.seed,
        "started_at": _iso(started_wall),
        "ended_at": _iso(wall()),
        "events_attempted": sum(r.attempted for r in reports.values()),
        "events_sent": sum(r.sent for r in reports.values()),
        "phases": [
            {
                "phase": report.name,
                "requested_eps": report.requested_eps,
                "attempted": report.attempted,
                "sent": report.sent,
                "started_at": report.started_at,
                "ended_at": report.ended_at,
            }
            for report in reports.values()
        ],
        "errors": errors,
    }
    if options.summary_json:
        options.summary_json.parent.mkdir(parents=True, exist_ok=True)
        options.summary_json.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"run {plan.run_id} finished: attempted={manifest['events_attempted']} "
        f"sent={manifest['events_sent']} errors={len(errors)}",
        file=stderr,
        flush=True,
    )
    return manifest


class SyslogSender:
    """Small UDP/TCP sender with one safe TCP reconnect attempt."""

    def __init__(
        self,
        target: str,
        port: int,
        protocol: str,
        *,
        bind_address: str | None = None,
        timeout: float = 3.0,
    ) -> None:
        self.target = target
        self.port = port
        self.protocol = protocol
        self.bind_address = bind_address
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._connect()

    def _connect(self) -> None:
        self.close()
        sock_type = socket.SOCK_DGRAM if self.protocol == "udp" else socket.SOCK_STREAM
        sock = socket.socket(socket.AF_INET, sock_type)
        sock.settimeout(self.timeout)
        if self.bind_address:
            sock.bind((self.bind_address, 0))
        if self.protocol == "tcp":
            sock.connect((self.target, self.port))
        self.sock = sock

    def send(self, message: str) -> None:
        payload = (message + "\n").encode("utf-8", errors="replace")
        for attempt in range(2):
            try:
                if self.sock is None:
                    self._connect()
                assert self.sock is not None
                if self.protocol == "udp":
                    self.sock.sendto(payload, (self.target, self.port))
                else:
                    self.sock.sendall(payload)
                return
            except (BrokenPipeError, ConnectionResetError, ConnectionRefusedError, OSError):
                if self.protocol != "tcp" or attempt == 1:
                    raise
                self._connect()

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def __enter__(self) -> SyslogSender:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def validate_options(options: Options) -> None:
    if options.eps <= 0:
        raise ValueError("--eps must be greater than 0")
    if options.eps > MAX_SAFE_EPS and not options.allow_high_rate:
        raise ValueError("rates over 100 EPS require --allow-high-rate")
    if not 1 <= options.port <= 65535:
        raise ValueError("--port must be between 1 and 65535")
    if options.count < 0:
        raise ValueError("--count must be zero or greater")
    if options.duration is not None and options.duration <= 0:
        raise ValueError("--duration must be greater than 0")
    if options.attempt_count < 1:
        raise ValueError("--attempt-count must be at least 1")
    if options.timestamp_delay < 0:
        raise ValueError("--timestamp-delay must be zero or greater")
    for name, value in (
        ("--fixed-source-ip", options.fixed_source_ip),
        ("--fixed-destination-ip", options.fixed_destination_ip),
        ("--bind-address", options.bind_address),
    ):
        if value is not None:
            try:
                parsed = ipaddress.ip_address(value)
            except ValueError as exc:
                raise ValueError(f"{name} must be a valid IPv4 address") from exc
            if parsed.version != 4:
                raise ValueError(f"{name} must be an IPv4 address")
    if options.fixed_host and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", options.fixed_host
    ):
        raise ValueError("--fixed-host must be a valid synthetic hostname")
    if options.fixed_username and options.fixed_username not in SYNTHETIC_USERS:
        raise ValueError("--fixed-username must be a synthetic username")


def run(
    options: Options,
    *,
    sender_factory: Callable[..., SyslogSender] = SyslogSender,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    validate_options(options)
    generator = EventGenerator(options, clock=clock)
    sender: SyslogSender | None = None
    if not options.dry_run:
        sender = sender_factory(
            options.target,
            options.port,
            options.protocol,
            bind_address=options.bind_address,
        )
    started = monotonic()
    next_send = started
    generated = 0
    file_handle: TextIO | None = None
    if options.output_file:
        options.output_file.parent.mkdir(parents=True, exist_ok=True)
        file_handle = options.output_file.open("a", encoding="utf-8")
    print(
        f"qradar-lab-loggen profiles={','.join(options.profiles)} scenario={options.scenario} "
        f"format={options.output_format} rate={options.eps:g}eps "
        f"destination={options.target}:{options.port}/{options.protocol}"
        + (" dry-run" if options.dry_run else ""),
        file=stderr,
    )
    try:
        while True:
            now = monotonic()
            if options.count and generated >= options.count:
                break
            if options.duration is not None and now - started >= options.duration:
                break
            message = generator.generate(generated)
            if sender is not None:
                sender.send(message)
            if options.stdout:
                print(message, file=stdout, flush=True)
            if file_handle is not None:
                file_handle.write(message + "\n")
                file_handle.flush()
            generated += 1
            if options.count and generated >= options.count:
                continue
            next_send += 1.0 / options.eps
            delay = next_send - monotonic()
            if delay > 0:
                sleeper(delay)
            else:
                next_send = monotonic()
    except KeyboardInterrupt:
        print(f"stopped after {generated} events", file=stderr)
    finally:
        if sender is not None:
            sender.close()
        if file_handle is not None:
            file_handle.close()
    return generated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate bounded synthetic security syslog for a QRadar lab."
    )
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--protocol", choices=("udp", "tcp"), default="udp")
    parser.add_argument("--eps", type=float, default=DEFAULT_EPS)
    parser.add_argument("--allow-high-rate", action="store_true")
    parser.add_argument(
        "--types",
        "--profiles",
        dest="profiles",
        nargs="+",
        choices=(*PROFILES, *PROFILE_ALIASES),
        default=list(PROFILES),
    )
    parser.add_argument(
        "--format", dest="output_format", choices=("leef", "vendor"), default="leef"
    )
    parser.add_argument("--scenario", choices=(*SCENARIOS, *PHASE_A_SCENARIOS), default="normal")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--attempt-count", type=int, default=10)
    parser.add_argument("--timestamp-delay", type=float, default=3600.0)
    parser.add_argument("--fixed-source-ip")
    parser.add_argument("--fixed-destination-ip")
    parser.add_argument("--fixed-username", choices=SYNTHETIC_USERS)
    parser.add_argument("--fixed-host")
    parser.add_argument("--bind-address")
    parser.add_argument("--multiple-devices", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stdout", "--print", dest="stdout", action="store_true")
    parser.add_argument("--output-file", type=Path)

    phase_a = parser.add_argument_group(
        "Phase A volume scenarios",
        "Only consumed by the source-volume / baseline-*-recovery / multi-source scenarios.",
    )
    phase_a.add_argument("--run-id", help="stable identifier carried on every event")
    phase_a.add_argument("--baseline-eps", type=float)
    phase_a.add_argument("--baseline-duration", type=float, default=DEFAULT_BASELINE_DURATION)
    phase_a.add_argument("--anomaly-eps", type=float)
    phase_a.add_argument(
        "--anomaly-multiplier",
        type=float,
        help="anomaly rate as a multiple of baseline; ignored when --anomaly-eps is given",
    )
    phase_a.add_argument("--anomaly-duration", type=float, default=DEFAULT_ANOMALY_DURATION)
    phase_a.add_argument("--recovery-duration", type=float, default=DEFAULT_RECOVERY_DURATION)
    phase_a.add_argument("--fixed-destination-port", type=int)
    phase_a.add_argument("--fixed-action", choices=("ALLOW", "DENY"))
    phase_a.add_argument("--summary-json", type=Path, help="write a sanitized run manifest")
    return parser


def phase_a_options_from_args(namespace: argparse.Namespace) -> PhaseAOptions:
    return PhaseAOptions(
        scenario=namespace.scenario,
        target=namespace.target,
        port=namespace.port,
        protocol=namespace.protocol,
        output_format=namespace.output_format,
        run_id=namespace.run_id,
        seed=namespace.seed,
        baseline_eps=namespace.baseline_eps,
        baseline_duration=namespace.baseline_duration,
        anomaly_eps=namespace.anomaly_eps,
        anomaly_multiplier=namespace.anomaly_multiplier,
        anomaly_duration=namespace.anomaly_duration,
        recovery_duration=namespace.recovery_duration,
        count=namespace.count,
        fixed_host=namespace.fixed_host,
        fixed_source_ip=namespace.fixed_source_ip,
        fixed_destination_ip=namespace.fixed_destination_ip,
        fixed_destination_port=namespace.fixed_destination_port,
        fixed_action=namespace.fixed_action,
        bind_address=namespace.bind_address,
        allow_high_rate=namespace.allow_high_rate,
        stdout=namespace.stdout,
        dry_run=namespace.dry_run,
        output_file=namespace.output_file,
        summary_json=namespace.summary_json,
    )


def options_from_args(namespace: argparse.Namespace) -> Options:
    profiles = tuple(PROFILE_ALIASES.get(value, value) for value in namespace.profiles)
    count = namespace.count
    # Correlated authentication recipes are bounded by their semantic attempt
    # count unless the operator explicitly supplies --count or --duration.
    if count == 0 and namespace.duration is None:
        if namespace.scenario == "brute-force":
            count = namespace.attempt_count
        elif namespace.scenario == "failed-login-then-success":
            count = namespace.attempt_count + 1
    return Options(
        target=namespace.target,
        port=namespace.port,
        protocol=namespace.protocol,
        eps=namespace.eps,
        profiles=profiles,
        output_format=namespace.output_format,
        scenario=namespace.scenario,
        count=count,
        duration=namespace.duration,
        seed=namespace.seed,
        stdout=namespace.stdout,
        dry_run=namespace.dry_run,
        output_file=namespace.output_file,
        fixed_source_ip=namespace.fixed_source_ip,
        fixed_destination_ip=namespace.fixed_destination_ip,
        fixed_username=namespace.fixed_username,
        fixed_host=namespace.fixed_host,
        bind_address=namespace.bind_address,
        multiple_devices=namespace.multiple_devices,
        allow_high_rate=namespace.allow_high_rate,
        attempt_count=namespace.attempt_count,
        timestamp_delay=namespace.timestamp_delay,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    namespace = parser.parse_args(argv)
    try:
        if is_phase_a(namespace.scenario):
            run_phase_a(phase_a_options_from_args(namespace))
        else:
            run(options_from_args(namespace))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
