"""Synthetic lab generator guardrails; all transport stays on loopback."""

from __future__ import annotations

import importlib.util
import io
import socket
import struct
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

TOOL_PATH = Path(__file__).parents[3] / "tools" / "qradar_lab_loggen.py"
SPEC = importlib.util.spec_from_file_location("qradar_lab_loggen", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
loggen = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = loggen
SPEC.loader.exec_module(loggen)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def fields(message: str) -> dict[str, str]:
    body = loggen.syslog_body(message)
    parts = body.split("|", 6)
    assert parts[5] == "0x09"
    extension = parts[6]
    return dict(item.split("=", 1) for item in extension.split("\t"))


def messages(options, count: int) -> list[str]:
    generator = loggen.EventGenerator(options, clock=lambda: NOW)
    return [generator.generate(index) for index in range(count)]


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class RecordingSender:
    def __init__(self, *_args, **_kwargs) -> None:
        self.messages: list[str] = []
        self.closed = False

    def send(self, message: str) -> None:
        self.messages.append(message)

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("profile,identity", loggen.DEFAULT_DEVICES.items())
def test_each_profile_uses_one_stable_default_identity(
    profile: str, identity: tuple[str, str]
) -> None:
    generated = messages(loggen.Options(profiles=(profile,), seed=2), 8)
    assert {fields(message)["deviceHostName"] for message in generated} == {identity[0]}
    assert {fields(message)["deviceAddress"] for message in generated} == {identity[1]}


def test_multiple_devices_require_explicit_option() -> None:
    single = messages(loggen.Options(profiles=("ips",), seed=4), 30)
    multiple = messages(
        loggen.Options(profiles=("ips",), seed=4, multiple_devices=True), 30
    )
    assert len({fields(message)["deviceHostName"] for message in single}) == 1
    assert len({fields(message)["deviceHostName"] for message in multiple}) > 1


def test_seed_is_deterministic_apart_from_timestamps() -> None:
    one = messages(loggen.Options(seed=8675309), 20)
    two = messages(loggen.Options(seed=8675309), 20)
    assert one == two


def test_fixed_source_controls_override_payload_fields_without_spoofing() -> None:
    event = messages(
        loggen.Options(
            profiles=("proxy",),
            fixed_source_ip="203.0.113.9",
            fixed_destination_ip="192.0.2.9",
            fixed_username="svc_sql",
            fixed_host="proxy-fixed-01",
            seed=1,
        ),
        1,
    )[0]
    parsed = fields(event)
    assert parsed["src"] == "203.0.113.9"
    assert parsed["dst"] == "192.0.2.9"
    assert parsed["usrName"] == "svc_sql"
    assert parsed["deviceHostName"] == "proxy-fixed-01"


def test_required_linux_and_windows_event_catalogues_are_present() -> None:
    assert {key for key in loggen.EVENT_IDS if key.isupper()} == {
        "SSH_LOGIN_FAILED",
        "SSH_LOGIN_SUCCESS",
        "SUDO_COMMAND",
        "USER_CREATED",
        "USER_DELETED",
        "SERVICE_STARTED",
        "AUDIT_LOG_CLEARED",
    }
    assert set(loggen.WINDOWS_EVENTS) == {
        4624, 4625, 4720, 4725, 4726, 4728, 4729, 4732, 4733, 1102, 4688, 7045
    }


@pytest.mark.parametrize("profile", loggen.PROFILES)
def test_leef_has_required_fields(profile: str) -> None:
    event = messages(loggen.Options(profiles=(profile,), seed=3), 1)[0]
    assert "LEEF:2.0|SyntheticLab|QRadarLab|1.0|" in event
    assert "|0x09|devTime=" in event
    required = {
        "devTime", "src", "dst", "srcPort", "dstPort", "proto", "usrName",
        "action", "severity", "category", "eventId", "deviceHostName",
        "deviceAddress", "requestMethod", "request", "statusCode", "ruleId",
        "signature",
    }
    assert required <= fields(event).keys()


@pytest.mark.parametrize("profile", loggen.PROFILES)
def test_vendor_output_is_not_leef(profile: str) -> None:
    event = messages(
        loggen.Options(profiles=(profile,), output_format="vendor", seed=9), 1
    )[0]
    assert "LEEF:" not in event
    assert loggen.DEFAULT_DEVICES[profile][0] in event


def test_linux_vendor_tag_is_not_duplicated() -> None:
    event = messages(
        loggen.Options(profiles=("linux_firewall",), output_format="vendor", seed=1), 1
    )[0]
    assert "kernel: kernel:" not in event
    assert " kernel: [UFW " in event


def test_count_terminates_and_dry_run_never_constructs_sender() -> None:
    fake = FakeTime()
    stdout = io.StringIO()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("dry-run constructed a network sender")

    count = loggen.run(
        loggen.Options(count=3, dry_run=True, stdout=True, seed=1),
        sender_factory=forbidden,
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
        clock=lambda: NOW,
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert count == 3
    assert len(stdout.getvalue().splitlines()) == 3


def test_duration_terminates_at_first_elapsed_boundary() -> None:
    fake = FakeTime()
    count = loggen.run(
        loggen.Options(duration=2.5, eps=2, dry_run=True, seed=1),
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
        clock=lambda: NOW,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert count == 5
    assert fake.value == 2.5


@pytest.mark.parametrize(
    "overrides,error",
    (
        ({"eps": 0}, "greater than 0"),
        ({"port": 0}, "between 1 and 65535"),
        ({"count": -1}, "zero or greater"),
        ({"duration": 0}, "greater than 0"),
        ({"bind_address": "not-an-ip"}, "valid IPv4"),
        ({"fixed_source_ip": "2001:db8::1"}, "IPv4"),
        ({"fixed_username": "real.user"}, "synthetic username"),
        ({"fixed_host": "bad host"}, "synthetic hostname"),
    ),
)
def test_invalid_options_are_rejected(overrides: dict, error: str) -> None:
    # fixed_username normally cannot bypass argparse choices; validate_options
    # also protects programmatic callers.
    options = loggen.Options(**overrides)
    if "fixed_username" in overrides:
        with pytest.raises(ValueError, match=error):
            loggen.validate_options(options)
    else:
        with pytest.raises(ValueError, match=error):
            loggen.validate_options(options)


def test_high_rate_needs_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="allow-high-rate"):
        loggen.validate_options(loggen.Options(eps=100.1))
    loggen.validate_options(loggen.Options(eps=100.1, allow_high_rate=True))


def test_correlated_cli_attempt_count_is_bounded_by_default() -> None:
    parser = loggen.build_parser()
    brute = loggen.options_from_args(
        parser.parse_args(["--scenario", "brute-force", "--attempt-count", "7"])
    )
    transition = loggen.options_from_args(
        parser.parse_args(
            ["--scenario", "failed-login-then-success", "--attempt-count", "4"]
        )
    )
    assert brute.count == 7
    assert transition.count == 5


def test_output_file_is_appended(tmp_path: Path) -> None:
    fake = FakeTime()
    output = tmp_path / "nested" / "events.log"
    assert loggen.run(
        loggen.Options(count=2, dry_run=True, output_file=output, seed=1),
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
        clock=lambda: NOW,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    ) == 2
    assert len(output.read_text().splitlines()) == 2


def test_udp_transmission_and_bind_address() -> None:
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(2)
    sender = loggen.SyslogSender(
        "127.0.0.1", receiver.getsockname()[1], "udp", bind_address="127.0.0.1"
    )
    try:
        sender.send("udp-lab-event")
        payload, address = receiver.recvfrom(4096)
    finally:
        sender.close()
        receiver.close()
    assert payload == b"udp-lab-event\n"
    assert address[0] == "127.0.0.1"


def test_tcp_transmission_reconnects_after_reset() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)
    listener.settimeout(3)
    received: list[bytes] = []
    first_closed = threading.Event()

    def receive_twice() -> None:
        for index in range(2):
            connection, _ = listener.accept()
            connection.settimeout(2)
            received.append(connection.recv(4096))
            if index == 0:
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                connection.close()
                first_closed.set()
            else:
                connection.close()

    thread = threading.Thread(target=receive_twice, daemon=True)
    thread.start()
    sender = loggen.SyslogSender("127.0.0.1", listener.getsockname()[1], "tcp")
    try:
        sender.send("tcp-one")
        assert first_closed.wait(2)
        time.sleep(0.02)
        sender.send("tcp-two")
        thread.join(3)
    finally:
        sender.close()
        listener.close()
    assert received == [b"tcp-one\n", b"tcp-two\n"]


def test_brute_force_correlation() -> None:
    generated = messages(
        loggen.Options(scenario="brute-force", fixed_username="efe.lab", seed=5), 6
    )
    parsed = [fields(message) for message in generated]
    assert {item["src"] for item in parsed} == {"198.51.100.44"}
    assert {item["usrName"] for item in parsed} == {"efe.lab"}
    assert {item["eventId"] for item in parsed} == {"WIN-4625"}


def test_password_spray_varies_only_synthetic_usernames() -> None:
    parsed = [fields(message) for message in messages(
        loggen.Options(scenario="password-spray", seed=5), len(loggen.SYNTHETIC_USERS)
    )]
    assert {item["src"] for item in parsed} == {"198.51.100.44"}
    assert [item["usrName"] for item in parsed] == list(loggen.SYNTHETIC_USERS)


def test_failed_login_then_success_ordering() -> None:
    parsed = [fields(message) for message in messages(
        loggen.Options(scenario="failed-login-then-success", attempt_count=3, seed=5), 4
    )]
    assert [item["eventId"] for item in parsed] == ["WIN-4625"] * 3 + ["WIN-4624"]
    assert len({item["src"] for item in parsed}) == 1
    assert len({item["usrName"] for item in parsed}) == 1


def test_port_scan_varies_destination_ports_only() -> None:
    parsed = [fields(message) for message in messages(
        loggen.Options(scenario="port-scan", seed=5), 6
    )]
    assert len({item["src"] for item in parsed}) == 1
    assert len({item["dst"] for item in parsed}) == 1
    assert len({item["dstPort"] for item in parsed}) == 6


def test_repeated_payload_is_byte_for_byte_equal() -> None:
    generated = messages(loggen.Options(scenario="repeated-payload", seed=5), 4)
    assert len(set(generated)) == 1


def test_parsing_degradation_keeps_valid_syslog_envelope_and_malformed_body() -> None:
    event = messages(loggen.Options(scenario="parsing-degradation", seed=5), 1)[0]
    assert event.startswith("<134>Aug 01 12:00:00 ")
    assert "PARSE-DEGRADED" in event
    assert "brokenField\tsrc" in event
    assert "\taction=" not in event


def test_timestamp_delay_changes_envelope_and_devtime() -> None:
    event = messages(
        loggen.Options(
            profiles=("ips",), scenario="timestamp-delay", timestamp_delay=3600, seed=5
        ),
        1,
    )[0]
    assert event.startswith("<134>Aug 01 11:00:00 ")
    assert fields(event)["devTime"] == "2026-08-01T11:00:00.000Z"
