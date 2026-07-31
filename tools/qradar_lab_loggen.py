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
        self.rng = random.Random(options.seed)
        self.clock = clock or (lambda: datetime.now(UTC))
        self._repeated: Event | None = None

    def generate(self, index: int) -> str:
        event = self._event(index)
        if self.options.scenario == "parsing-degradation":
            body = f"LEEF:2.0|SyntheticLab|Malformed|1.0|PARSE-DEGRADED|devTime={_iso(event.timestamp)}\tbrokenField\tsrc"
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
        devices = ALTERNATE_DEVICES[profile] if self.options.multiple_devices else (DEFAULT_DEVICES[profile],)
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
                profile, now, event_id=EVENT_IDS[key], category="intrusion", action="blocked",
                severity=severity, signature=signature, dst_port=self.rng.choice((22, 80, 443, 445)),
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
                profile, now, event_id=EVENT_IDS[key], category="web-attack", action="blocked",
                severity=9, signature=signature, dst_port=443,
            )
            return replace(event, request_method="GET", request=request, status_code=403)
        if profile == "linux_firewall":
            event = self._base(
                profile, now, event_id=EVENT_IDS["linux_firewall"], category="firewall",
                action=self.rng.choice(("blocked", "blocked", "allowed")), severity=6,
                signature="Linux firewall decision", dst_port=self.rng.choice((22, 80, 443, 445, 5432)),
            )
            return replace(event, vendor_message=self._linux_firewall_vendor(event))
        if profile == "windows_firewall":
            denied = self.rng.choice((False, False, True))
            event_id = EVENT_IDS["windows_firewall_deny" if denied else "windows_firewall_allow"]
            event = self._base(
                profile, now, event_id=event_id, category="firewall",
                action="blocked" if denied else "allowed", severity=6 if denied else 3,
                signature="Windows Filtering Platform connection",
                dst_port=self.rng.choice((53, 80, 443, 445, 3389)),
            )
            return replace(event, vendor_message=self._windows_vendor(event, int(event_id.split("-")[1])))
        if profile == "linux_auth":
            kind = self.rng.choice(tuple(k for k in EVENT_IDS if k.isupper()))
            action = "failure" if kind == "SSH_LOGIN_FAILED" else "success"
            event = self._base(
                profile, now, event_id=EVENT_IDS[kind], category="authentication",
                action=action, severity=7 if "FAILED" in kind or "CLEARED" in kind else 4,
                signature=kind.replace("_", " ").title(), dst_port=22,
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
                profile, now, event_id=EVENT_IDS["dns"], category="dns", action="resolved",
                severity=3, signature="Synthetic DNS query", dst_port=53, proto="UDP",
            )
            return replace(event, request="lab.example.test", status_code=0)
        event = self._base(
            "proxy", now, event_id=EVENT_IDS["proxy"], category="web-proxy", action="allowed",
            severity=3, signature="Synthetic proxy request", dst_port=443,
        )
        return replace(event, request_method="GET", request="https://portal.example.test/lab", status_code=200)

    def _windows_event(self, profile: str, now: datetime, event_id: int) -> Event:
        signature, action, severity = WINDOWS_EVENTS[event_id]
        event = self._base(
            profile, now, event_id=f"WIN-{event_id}", category="windows-security", action=action,
            severity=severity, signature=signature, dst_port=445,
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
            event_id = 4624 if index % (self.options.attempt_count + 1) == self.options.attempt_count else 4625
            return self._auth_scenario(now, event_id, stable_src, stable_user)
        if scenario == "privileged-group-add":
            return self._auth_scenario(now, 4728, stable_src, stable_user, profile="windows_account")
        if scenario == "account-created":
            return self._auth_scenario(now, 4720, stable_src, stable_user, profile="windows_account")
        if scenario == "suspicious-powershell":
            event = self._auth_scenario(now, 4688, stable_src, stable_user, profile="windows_account")
            return replace(event, request="powershell.exe -NoProfile -EncodedCommand LABONLY")
        if scenario == "port-scan":
            event = self._base(
                "linux_firewall", now, event_id=EVENT_IDS["linux_firewall"], category="port-scan",
                action="blocked", severity=8, signature="Synthetic sequential port scan",
                src=stable_src, dst_port=PORT_SCAN_PORTS[index % len(PORT_SCAN_PORTS)],
                username=stable_user,
            )
            return replace(event, vendor_message=self._linux_firewall_vendor(event))
        if scenario == "ips-exploit-burst":
            return self._base(
                "ips", now, event_id=EVENT_IDS["ips_exploit"], category="intrusion",
                action="blocked", severity=9, signature="ET EXPLOIT Apache Struts Attempt",
                src=stable_src, dst_port=443, username=stable_user,
            )
        if scenario in {"waf-sqli-burst", "waf-xss-burst"}:
            sqli = scenario == "waf-sqli-burst"
            event = self._base(
                "waf", now, event_id=EVENT_IDS["waf_sqli" if sqli else "waf_xss"],
                category="web-attack", action="blocked", severity=9,
                signature="SQL Injection Attack Detected" if sqli else "XSS Attack Detected",
                src=stable_src, dst_port=443, username=stable_user,
            )
            request = "/login?id=1%27OR%271%27=%271" if sqli else "/search?q=%3Cscript%3Elab%3C/script%3E"
            return replace(event, request_method="GET", request=request, status_code=403)
        if scenario == "firewall-deny-burst":
            event = self._base(
                "linux_firewall", now, event_id=EVENT_IDS["linux_firewall"], category="firewall",
                action="blocked", severity=7, signature="Linux firewall deny burst",
                src=stable_src, dst_port=445, username=stable_user,
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
        return f"LEEF:2.0|SyntheticLab|QRadarLab|1.0|{event.event_id}|{extension}"

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
            return event.vendor_message or f"sshd: authentication {event.action} for {event.username} from {event.src}"
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
            return f"sshd: Failed password for {event.username} from {event.src} port {event.src_port} ssh2"
        if kind == "SSH_LOGIN_SUCCESS":
            return f"sshd: Accepted password for {event.username} from {event.src} port {event.src_port} ssh2"
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
    if options.fixed_host and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", options.fixed_host):
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
    parser = argparse.ArgumentParser(description="Generate bounded synthetic security syslog for a QRadar lab.")
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--protocol", choices=("udp", "tcp"), default="udp")
    parser.add_argument("--eps", type=float, default=DEFAULT_EPS)
    parser.add_argument("--allow-high-rate", action="store_true")
    parser.add_argument(
        "--types", "--profiles", dest="profiles", nargs="+",
        choices=(*PROFILES, *PROFILE_ALIASES), default=list(PROFILES),
    )
    parser.add_argument("--format", dest="output_format", choices=("leef", "vendor"), default="leef")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
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
    return parser


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
    options = options_from_args(namespace)
    try:
        run(options)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
