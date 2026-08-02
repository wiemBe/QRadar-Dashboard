"""Phase A volume scenarios of the lab generator.

Every transport assertion binds to loopback; nothing here may ever reach the
appliance. The generator is loaded by path because it is an operator tool that
deliberately lives outside the backend package.
"""

from __future__ import annotations

import importlib.util
import io
import json
import socket
import sys
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

TOOL_PATH = Path(__file__).parents[3] / "tools" / "qradar_lab_loggen.py"
SPEC = importlib.util.spec_from_file_location("qradar_lab_loggen_phase_a", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
loggen = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = loggen
SPEC.loader.exec_module(loggen)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
LAB_TARGET = "192.168.122.50"


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class RecordingSender:
    instances: ClassVar[list[RecordingSender]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.messages: list[str] = []
        self.closed = False
        RecordingSender.instances.append(self)

    def send(self, message: str) -> None:
        self.messages.append(message)

    def close(self) -> None:
        self.closed = True


def options(**overrides) -> loggen.PhaseAOptions:
    base = {
        "scenario": "baseline-spike-recovery",
        "run_id": "labrun-test",
        "seed": 20260801,
        "baseline_duration": 10.0,
        "anomaly_duration": 5.0,
        "recovery_duration": 5.0,
    }
    base.update(overrides)
    return loggen.PhaseAOptions(**base)


def emit(opts: loggen.PhaseAOptions) -> list[str]:
    """Run a scenario against a fake clock and return the rendered messages."""
    fake = FakeTime()
    RecordingSender.instances.clear()
    loggen.run_phase_a(
        opts,
        sender_factory=RecordingSender,
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
        clock=lambda: NOW,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    return RecordingSender.instances[-1].messages


def leef_fields(message: str) -> dict[str, str]:
    body = loggen.syslog_body(message)
    parts = body.split("|", 6)
    assert parts[0] == "LEEF:2.0"
    assert parts[5] == "0x09"
    return dict(item.split("=", 1) for item in parts[6].split("\t"))


def by_phase(messages: list[str]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for message in messages:
        parsed = leef_fields(message)
        grouped.setdefault(parsed["phase"], []).append(parsed)
    return grouped


# --------------------------------------------------------------- identities


@pytest.mark.parametrize("scenario", loggen.PHASE_A_SCENARIOS)
def test_every_scenario_keeps_stable_source_identities(scenario: str) -> None:
    messages = emit(options(scenario=scenario))
    parsed = [leef_fields(message) for message in messages]
    hosts = {item["deviceHostName"] for item in parsed}
    expected = set(loggen.scenario_hosts(options(scenario=scenario)))
    assert hosts == expected
    # The RFC3164 hostname must equal the log source identifier in the payload,
    # or QRadar routes the event to a different (or brand new) log source.
    for message in messages:
        envelope_host = message.split(" ", 4)[3]
        assert envelope_host == leef_fields(message)["deviceHostName"]
    for item in parsed:
        assert item["deviceAddress"] == loggen.PHASE_A_SOURCES[item["deviceHostName"]].device_ip


def test_multi_source_scenarios_use_three_separate_hosts() -> None:
    for scenario in ("multi-source-single-spike", "multi-source-single-drop"):
        hosts = loggen.scenario_hosts(options(scenario=scenario))
        assert len(set(hosts)) >= 3


def test_seed_reproduces_the_run_apart_from_timestamps() -> None:
    one = emit(options(seed=4242))
    two = emit(options(seed=4242))
    assert one == two
    assert emit(options(seed=99)) != one


def test_run_id_reaches_every_event_and_both_formats() -> None:
    leef = emit(options(run_id="labrun-alpha"))
    assert all(leef_fields(message)["runId"] == "labrun-alpha" for message in leef)
    vendor = emit(options(run_id="labrun-alpha", output_format="vendor"))
    assert all("runId=labrun-alpha" in message for message in vendor)


def test_default_run_id_is_derived_from_the_clock() -> None:
    assert loggen.default_run_id(NOW) == "labrun-20260801T120000Z"


# ------------------------------------------------------------------ formats


def test_leef_carries_every_required_phase_a_field() -> None:
    required = {
        "devTime",
        "eventId",
        "eventName",
        "deviceHostName",
        "deviceAddress",
        "src",
        "dst",
        "srcPort",
        "dstPort",
        "proto",
        "action",
        "severity",
        "category",
        "runId",
        "scenario",
        "phase",
    }
    for message in emit(options()):
        assert required <= set(leef_fields(message))


def test_devtime_is_epoch_milliseconds_so_qradar_needs_no_devtimeformat() -> None:
    # ISO text would be read against the log source's timezone; an event that
    # parses three hours late lands in the wrong metric bucket.
    parsed = leef_fields(emit(options(baseline_duration=2.0))[0])
    assert parsed["devTime"] == str(int(NOW.timestamp() * 1000))


def test_leef_carries_the_dsm_mapped_short_keys_alongside_the_readable_ones() -> None:
    parsed = leef_fields(emit(options(baseline_duration=2.0))[0])
    assert parsed["sev"] == parsed["severity"]
    assert parsed["cat"] == parsed["category"]


@pytest.mark.parametrize(
    "host,expected",
    (
        ("lab-fw-volume-01", {"direction", "ifName", "policyId", "ruleName", "tcpFlags",
                              "ttl", "pktLen", "bytesIn", "bytesOut", "sessionId"}),
        ("lab-waf-volume-01", {"httpMethod", "url", "virtualHost", "userAgent",
                               "responseCode", "bytesIn", "bytesOut", "ruleId"}),
        ("lab-ips-volume-01", {"sigId", "classification", "priority", "flowId",
                               "pktCount", "direction", "ifName"}),
    ),
)
def test_each_source_kind_carries_device_shaped_detail_fields(host: str, expected: set) -> None:
    for message in emit(options(scenario="source-volume-baseline", fixed_host=host,
                                baseline_duration=10.0)):
        assert expected <= set(leef_fields(message))


def test_denied_firewall_events_move_no_bytes_and_carry_a_syn_only_flag() -> None:
    denied = [
        parsed
        for parsed in (leef_fields(m) for m in emit(options(baseline_duration=60.0)))
        if parsed["action"] == "DENY" and parsed["deviceHostName"].startswith("lab-fw")
    ]
    assert denied
    for parsed in denied:
        assert parsed["bytesIn"] == "0"
        assert parsed["bytesOut"] == "0"
        assert parsed["tcpFlags"] == "SYN"
        assert parsed["ruleName"] == "lab-perimeter-drop"


def test_leef_values_escape_delimiters_and_headers_escape_pipes() -> None:
    assert loggen._leef_escape("a\tb") == "a\\tb"
    assert loggen._leef_escape("a\nb") == "a\\nb"
    assert loggen._leef_escape("a\\b") == "a\\\\b"
    assert loggen._leef_header("Synthetic|Firewall") == "Synthetic\\|Firewall"
    # No emitted value may contain a raw tab: a tab is the extension delimiter.
    for message in emit(options()):
        body = loggen.syslog_body(message)
        extension = body.split("|", 6)[6]
        for pair in extension.split("\t"):
            assert "\t" not in pair.split("=", 1)[1]


def test_vendor_output_is_not_leef_and_never_doubles_the_linux_tag() -> None:
    for message in emit(options(output_format="vendor")):
        assert "LEEF:" not in message
        assert "kernel: kernel:" not in message
        assert " kernel: [UFW " in message


def test_vendor_output_covers_waf_and_ips_sources() -> None:
    waf = emit(options(scenario="source-volume-baseline", fixed_host="lab-waf-volume-01",
                       output_format="vendor"))
    ips = emit(options(scenario="source-volume-baseline", fixed_host="lab-ips-volume-01",
                       output_format="vendor"))
    assert all(" ModSecurity: " in message for message in waf)
    assert all(" suricata: " in message for message in ips)


# ------------------------------------------------------------ phase timing


def test_baseline_scenario_is_one_phase_at_the_requested_rate() -> None:
    opts = options(scenario="source-volume-baseline", baseline_eps=2.0, baseline_duration=30.0)
    plan = loggen.build_plan(opts, now=NOW)
    assert [phase.name for phase in plan.phases] == ["baseline"]
    assert plan.expected_events == 60
    assert len(emit(opts)) == 60


def test_spike_phase_timing_and_rate() -> None:
    opts = options(baseline_eps=2.0, anomaly_multiplier=3.0)
    plan = loggen.build_plan(opts, now=NOW)
    baseline, anomaly, recovery = plan.phases
    assert (baseline.name, anomaly.name, recovery.name) == ("baseline", "anomaly", "recovery")
    assert anomaly.sources[0].eps == 6.0
    assert recovery.sources[0].eps == 2.0
    grouped = by_phase(emit(opts))
    assert len(grouped["baseline"]) == 20
    assert len(grouped["anomaly"]) == 30
    assert len(grouped["recovery"]) == 10


def test_drop_phase_timing_and_rate() -> None:
    opts = options(scenario="baseline-drop-recovery")
    plan = loggen.build_plan(opts, now=NOW)
    baseline, anomaly, _ = plan.phases
    # The drop default baseline is tall enough that the reduced rate still
    # moves more events than a minimum-absolute-delta guard would demand.
    assert baseline.sources[0].eps == loggen.DEFAULT_DROP_BASELINE_EPS
    assert anomaly.sources[0].eps == pytest.approx(
        loggen.DEFAULT_DROP_BASELINE_EPS * loggen.DEFAULT_DROP_MULTIPLIER
    )
    grouped = by_phase(emit(opts))
    assert len(grouped["anomaly"]) < len(grouped["baseline"])


def test_schedule_offsets_stay_inside_their_phase_windows() -> None:
    opts = options(baseline_eps=2.0, baseline_duration=10.0, anomaly_duration=5.0,
                   recovery_duration=5.0)
    plan = loggen.build_plan(opts, now=NOW)
    schedule = loggen.build_schedule(plan)
    windows = {"baseline": (0.0, 10.0), "anomaly": (10.0, 15.0), "recovery": (15.0, 20.0)}
    for offset, phase, _rate in schedule:
        start, end = windows[phase]
        assert start <= offset < end
    # Offsets are precomputed, so they are monotonic and never drift.
    assert [item[0] for item in schedule] == sorted(item[0] for item in schedule)


def test_run_advances_exactly_one_scenario_duration() -> None:
    fake = FakeTime()
    RecordingSender.instances.clear()
    opts = options(baseline_eps=1.0, baseline_duration=10.0, anomaly_duration=4.0,
                   recovery_duration=4.0)
    loggen.run_phase_a(
        opts,
        sender_factory=RecordingSender,
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
        clock=lambda: NOW,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    plan = loggen.build_plan(opts, now=NOW)
    last_offset = loggen.build_schedule(plan)[-1][0]
    assert fake.value == pytest.approx(last_offset)


def test_silence_scenario_stops_after_baseline_without_heartbeats() -> None:
    opts = options(scenario="source-volume-silence", baseline_eps=2.0, baseline_duration=20.0)
    plan = loggen.build_plan(opts, now=NOW)
    assert [phase.name for phase in plan.phases] == ["baseline"]
    messages = emit(opts)
    assert {leef_fields(message)["phase"] for message in messages} == {"baseline"}
    assert len(messages) == 40
    # Silence is the absence of events, never a synthetic zero-count heartbeat:
    # every message is a real device event, and none announces its own absence.
    for message in messages:
        parsed = leef_fields(message)
        assert parsed["eventId"] in {
            template.event_id
            for mixture in loggen.BASELINE_MIXTURES.values()
            for template in mixture.templates
        }
        assert "heartbeat" not in parsed["eventName"].lower()
        assert "zero" not in parsed["eventName"].lower()
    # A silence run must not disturb the spike/drop source's own history.
    assert plan.hosts == (loggen.DEFAULT_SILENCE_HOST,)
    assert loggen.DEFAULT_SILENCE_HOST != loggen.DEFAULT_PHASE_A_HOST


# ------------------------------------------------------- explanation shape


def test_spike_concentrates_additional_volume_on_deterministic_contributors() -> None:
    opts = options(baseline_eps=2.0, anomaly_multiplier=3.0, baseline_duration=60.0,
                   anomaly_duration=60.0, recovery_duration=10.0)
    grouped = by_phase(emit(opts))
    anomaly = grouped["anomaly"]
    baseline = grouped["baseline"]

    top_src = Counter(item["src"] for item in anomaly).most_common(1)[0]
    top_dst = Counter(item["dst"] for item in anomaly).most_common(1)[0]
    top_port = Counter(item["dstPort"] for item in anomaly).most_common(1)[0]
    top_name = Counter(item["eventName"] for item in anomaly).most_common(1)[0]
    assert top_src[0] == loggen.CONTRIBUTOR_SOURCE_IP
    assert top_dst[0] == loggen.CONTRIBUTOR_DESTINATION_IP
    assert top_port[0] == str(loggen.CONTRIBUTOR_DESTINATION_PORT)
    assert top_name[0] == "Firewall Denied Connection"

    def deny_share(rows: list[dict[str, str]]) -> float:
        return sum(1 for row in rows if row["action"] == "DENY") / len(rows)

    assert deny_share(anomaly) > deny_share(baseline) + 0.3
    # The baseline share of the elevated rate keeps its normal spread, so the
    # increase reads as a change in share rather than an entirely new universe.
    assert len({item["src"] for item in anomaly}) > 1


def test_baseline_uses_a_controlled_mixture_of_dimensions() -> None:
    baseline = by_phase(emit(options(baseline_duration=120.0, baseline_eps=2.0)))["baseline"]
    assert len({item["src"] for item in baseline}) >= 3
    assert len({item["dst"] for item in baseline}) >= 2
    assert len({item["dstPort"] for item in baseline}) >= 3
    assert {item["action"] for item in baseline} == {"ALLOW", "DENY"}
    assert len({item["eventName"] for item in baseline}) >= 2


def test_drop_phase_makes_baseline_contributors_disappear() -> None:
    grouped = by_phase(emit(options(scenario="baseline-drop-recovery", baseline_duration=120.0,
                                    anomaly_duration=120.0, recovery_duration=10.0)))
    baseline_srcs = {item["src"] for item in grouped["baseline"]}
    anomaly_srcs = {item["src"] for item in grouped["anomaly"]}
    assert loggen.CONTRIBUTOR_SOURCE_IP in baseline_srcs
    assert loggen.CONTRIBUTOR_SOURCE_IP not in anomaly_srcs
    assert str(loggen.CONTRIBUTOR_DESTINATION_PORT) not in {
        item["dstPort"] for item in grouped["anomaly"]
    }
    assert "DENY" not in {item["action"] for item in grouped["anomaly"]}
    # Reduced volume, never malformed events.
    for message in emit(options(scenario="source-volume-drop", anomaly_duration=10.0)):
        assert leef_fields(message)["eventId"]


def test_recovery_returns_to_the_baseline_mixture_and_rate() -> None:
    grouped = by_phase(emit(options(baseline_duration=120.0, anomaly_duration=30.0,
                                    recovery_duration=120.0, baseline_eps=2.0)))
    recovery, baseline = grouped["recovery"], grouped["baseline"]
    assert len(recovery) == len(baseline)
    assert {item["action"] for item in recovery} == {item["action"] for item in baseline}


# ------------------------------------------------------- source isolation


@pytest.mark.parametrize(
    "scenario,compare",
    (
        ("multi-source-single-spike", "gt"),
        ("multi-source-single-drop", "lt"),
    ),
)
def test_only_one_source_changes_volume(scenario: str, compare: str) -> None:
    opts = options(scenario=scenario, baseline_duration=60.0, anomaly_duration=60.0,
                   recovery_duration=10.0)
    grouped = by_phase(emit(opts))
    hosts = loggen.scenario_hosts(opts)
    baseline_counts = Counter(item["deviceHostName"] for item in grouped["baseline"])
    anomaly_counts = Counter(item["deviceHostName"] for item in grouped["anomaly"])
    changed, *steady = hosts
    if compare == "gt":
        assert anomaly_counts[changed] > baseline_counts[changed]
    else:
        assert anomaly_counts[changed] < baseline_counts[changed]
    for host in steady:
        assert anomaly_counts[host] == baseline_counts[host]


def test_multi_source_rejects_a_single_fixed_host() -> None:
    with pytest.raises(ValueError, match="multi-source"):
        loggen.build_plan(options(scenario="multi-source-single-spike",
                                  fixed_host="lab-fw-volume-01"), now=NOW)


# --------------------------------------------------------------- overrides


def test_fixed_overrides_pin_payload_dimensions_without_spoofing() -> None:
    messages = emit(
        options(
            scenario="source-volume-baseline",
            baseline_duration=5.0,
            fixed_host="lab-ips-volume-01",
            fixed_source_ip="198.51.100.77",
            fixed_destination_ip="10.10.10.99",
            fixed_destination_port=8443,
            fixed_action="DENY",
        )
    )
    for message in messages:
        parsed = leef_fields(message)
        assert parsed["src"] == "198.51.100.77"
        assert parsed["dst"] == "10.10.10.99"
        assert parsed["dstPort"] == "8443"
        assert parsed["action"] == "DENY"
        assert parsed["deviceHostName"] == "lab-ips-volume-01"


def test_anomaly_eps_overrides_the_multiplier() -> None:
    plan = loggen.build_plan(options(baseline_eps=2.0, anomaly_eps=9.0,
                                     anomaly_multiplier=100.0), now=NOW)
    assert plan.phases[1].sources[0].eps == 9.0


# ------------------------------------------------------------- termination


def test_count_caps_a_timed_scenario() -> None:
    messages = emit(options(count=7, baseline_eps=2.0))
    assert len(messages) == 7


def test_dry_run_performs_no_network_send(tmp_path: Path) -> None:
    fake = FakeTime()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("dry-run constructed a network sender")

    stdout = io.StringIO()
    manifest = loggen.run_phase_a(
        options(scenario="source-volume-baseline", baseline_duration=5.0, baseline_eps=2.0,
                dry_run=True, stdout=True, target=LAB_TARGET),
        sender_factory=forbidden,
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
        clock=lambda: NOW,
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert len(stdout.getvalue().splitlines()) == 10
    assert manifest["dry_run"] is True
    assert manifest["events_attempted"] == 10
    # A dry run must never claim to have sent anything.
    assert manifest["events_sent"] == 0


def test_output_file_receives_every_event(tmp_path: Path) -> None:
    fake = FakeTime()
    output = tmp_path / "nested" / "phase-a.log"
    RecordingSender.instances.clear()
    loggen.run_phase_a(
        options(scenario="source-volume-baseline", baseline_duration=4.0, baseline_eps=2.0,
                output_file=output),
        sender_factory=RecordingSender,
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
        clock=lambda: NOW,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert len(output.read_text().splitlines()) == 8


# ---------------------------------------------------------------- manifest


def test_summary_json_is_a_sanitized_manifest(tmp_path: Path) -> None:
    fake = FakeTime()
    summary = tmp_path / "runs" / "manifest.json"
    RecordingSender.instances.clear()
    manifest = loggen.run_phase_a(
        options(baseline_eps=2.0, summary_json=summary, target=LAB_TARGET),
        sender_factory=RecordingSender,
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
        clock=lambda: NOW,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    written = json.loads(summary.read_text())
    assert written == manifest
    assert written["run_id"] == "labrun-test"
    assert written["scenario"] == "baseline-spike-recovery"
    assert written["sources"] == ["lab-fw-volume-01"]
    assert written["seed"] == 20260801
    assert written["generator_version"] == loggen.GENERATOR_VERSION
    assert written["protocol"] == "udp"
    assert written["errors"] == []
    phases = {phase["phase"]: phase for phase in written["phases"]}
    assert set(phases) == {"baseline", "anomaly", "recovery"}
    for phase in phases.values():
        assert phase["attempted"] == phase["sent"] > 0
        assert phase["started_at"] and phase["ended_at"]
        assert phase["requested_eps"]
    # No credential, header or token may ever reach a manifest.
    serialized = summary.read_text().lower()
    for forbidden in ("sec", "token", "authorization", "password"):
        assert forbidden not in serialized


def test_manifest_records_send_errors_without_aborting_the_run() -> None:
    class FailingSender(RecordingSender):
        def send(self, message: str) -> None:
            raise OSError("network unreachable")

    fake = FakeTime()
    RecordingSender.instances.clear()
    manifest = loggen.run_phase_a(
        options(scenario="source-volume-baseline", baseline_duration=3.0, baseline_eps=2.0),
        sender_factory=FailingSender,
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
        clock=lambda: NOW,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert manifest["events_attempted"] == 6
    assert manifest["events_sent"] == 0
    assert len(manifest["errors"]) == 6


# --------------------------------------------------------------- transport


def _run_over(protocol: str, port: int, **overrides) -> None:
    fake = FakeTime()
    loggen.run_phase_a(
        options(scenario="source-volume-baseline", baseline_duration=2.0, baseline_eps=1.0,
                target="127.0.0.1", port=port, protocol=protocol, **overrides),
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
        clock=lambda: NOW,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )


def test_udp_receiver_sees_phase_a_events_over_loopback() -> None:
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(2)
    try:
        _run_over("udp", receiver.getsockname()[1], bind_address="127.0.0.1")
        payload, address = receiver.recvfrom(4096)
    finally:
        receiver.close()
    assert address[0] == "127.0.0.1"
    assert b"LEEF:2.0|QRadarLab|" in payload
    assert b"runId=labrun-test" in payload


def test_tcp_receiver_sees_phase_a_events_over_loopback() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)
    received: list[bytes] = []

    def accept_once() -> None:
        connection, _ = listener.accept()
        connection.settimeout(2)
        with connection:
            while chunk := connection.recv(4096):
                received.append(chunk)

    thread = threading.Thread(target=accept_once, daemon=True)
    thread.start()
    try:
        _run_over("tcp", listener.getsockname()[1])
        thread.join(3)
    finally:
        listener.close()
    joined = b"".join(received)
    assert joined.count(b"\n") == 2
    assert b"phase=baseline" in joined


def test_bind_address_failure_is_reported() -> None:
    with pytest.raises(OSError):
        _run_over("udp", 9999, bind_address="203.0.113.200")


# -------------------------------------------------------------- guardrails


@pytest.mark.parametrize(
    "overrides,error",
    (
        ({"baseline_eps": 0.0}, "greater than 0"),
        ({"baseline_eps": -1.0}, "greater than 0"),
        ({"anomaly_eps": 0.0}, "greater than 0"),
        ({"anomaly_multiplier": -2.0}, "greater than 0"),
        ({"baseline_duration": 0.0}, "greater than 0"),
        ({"anomaly_duration": -5.0}, "greater than 0"),
        ({"recovery_duration": 0.0}, "greater than 0"),
        ({"port": 0}, "between 1 and 65535"),
        ({"port": 70000}, "between 1 and 65535"),
        ({"count": -1}, "zero or greater"),
        ({"run_id": "bad run id"}, "run-id"),
        ({"fixed_destination_port": 0}, "between 1 and 65535"),
        ({"fixed_action": "DROP"}, "ALLOW or DENY"),
        ({"fixed_host": "prod-fw-01"}, "must be one of"),
        ({"bind_address": "not-an-ip"}, "valid IPv4"),
        ({"fixed_source_ip": "2001:db8::1"}, "IPv4"),
        ({"scenario": "not-a-scenario"}, "unsupported Phase A scenario"),
    ),
)
def test_invalid_phase_a_options_are_rejected(overrides: dict, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        loggen.validate_phase_a_options(options(**overrides))


def test_aggregate_high_rate_needs_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="allow-high-rate"):
        loggen.build_plan(options(baseline_eps=40.0, anomaly_multiplier=3.0), now=NOW)
    plan = loggen.build_plan(
        options(baseline_eps=40.0, anomaly_multiplier=3.0, allow_high_rate=True), now=NOW
    )
    assert plan.phases[1].sources[0].eps == 120.0


def test_multi_source_aggregate_rate_is_what_is_guarded() -> None:
    # 3 x 40 EPS is 120 aggregate even though no single source exceeds the cap.
    with pytest.raises(ValueError, match="allow-high-rate"):
        loggen.build_plan(
            options(scenario="multi-source-single-spike", baseline_eps=40.0), now=NOW
        )


def test_there_is_no_unlimited_flood_mode() -> None:
    plan = loggen.build_plan(options(), now=NOW)
    assert plan.expected_events == len(loggen.build_schedule(plan))
    assert plan.duration == 20.0


# --------------------------------------------------------------------- CLI


def test_cli_routes_phase_a_scenarios_and_carries_every_flag() -> None:
    parser = loggen.build_parser()
    namespace = parser.parse_args(
        [
            "--scenario", "multi-source-single-spike",
            "--format", "leef",
            "--seed", "11",
            "--run-id", "labrun-cli",
            "--baseline-eps", "2",
            "--baseline-duration", "360",
            "--anomaly-eps", "6",
            "--anomaly-multiplier", "3",
            "--anomaly-duration", "180",
            "--recovery-duration", "240",
            "--fixed-source-ip", "203.0.113.50",
            "--fixed-destination-ip", "10.10.10.20",
            "--fixed-destination-port", "445",
            "--fixed-action", "DENY",
            "--bind-address", "127.0.0.1",
            "--allow-high-rate",
            "--summary-json", "lab-runs/manifest.json",
        ]
    )
    assert loggen.is_phase_a(namespace.scenario)
    opts = loggen.phase_a_options_from_args(namespace)
    assert opts.run_id == "labrun-cli"
    assert (opts.baseline_eps, opts.anomaly_eps, opts.anomaly_multiplier) == (2.0, 6.0, 3.0)
    assert (opts.baseline_duration, opts.anomaly_duration, opts.recovery_duration) == (
        360.0, 180.0, 240.0
    )
    assert opts.fixed_destination_port == 445
    assert opts.fixed_action == "DENY"
    assert opts.allow_high_rate is True
    assert opts.summary_json == Path("lab-runs/manifest.json")
    assert opts.output_format == "leef"


def test_leef_is_the_default_format_and_the_lab_target_is_the_default() -> None:
    namespace = loggen.build_parser().parse_args(["--scenario", "source-volume-baseline"])
    opts = loggen.phase_a_options_from_args(namespace)
    assert opts.output_format == "leef"
    assert (opts.target, opts.port, opts.protocol) == (LAB_TARGET, 514, "udp")


def test_plan_output_is_sanitized_and_names_every_phase() -> None:
    opts = options(baseline_eps=2.0)
    text = loggen.format_plan(opts, loggen.build_plan(opts, now=NOW))
    for expected in ("labrun-test", "baseline-spike-recovery", "lab-fw-volume-01",
                     "baseline", "anomaly", "recovery", "leef", "advisory"):
        assert expected in text
    for forbidden in ("token", "authorization", "password", "SEC "):
        assert forbidden not in text
