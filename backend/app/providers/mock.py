"""MockQRadarProvider — deterministic in-memory QRadar for dev and tests.

Deterministic by design: given the same seed the same log sources, EPS values,
offenses and AQL results come back every run, so tests assert on exact numbers
and demos look identical each time. It supports every capability, including the
full Ariel search lifecycle, so Phase 2 collection and anomaly work can proceed
with no real QRadar.

The mock also fabricates a few *unhealthy* sources on purpose — a silent source,
a volume-collapsed source, a parsing-degraded source — so the anomaly detector
and health scoring have something to detect in development.
"""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime, timedelta

from app.providers.base import ProviderCapability, QRadarProvider
from app.providers.dto import (
    AnalyticsRuleDTO,
    ArielSearchHandleDTO,
    ArielSearchResultsDTO,
    ArielSearchStatusDTO,
    InstanceInfoDTO,
    LogSourceDTO,
    LogSourceMetricSample,
    LogSourceTypeDTO,
    OffenseDTO,
)

_LOG_SOURCE_TYPES = [
    (12, "Cisco ASA"),
    (13, "Microsoft Windows Security Event Log"),
    (2, "Linux OS"),
    (95, "Palo Alto PA Series"),
    (145, "Okta"),
    (351, "AWS CloudTrail"),
]

# name, type_id, health-shape. The health-shape drives what the mock emits so
# the detector has deterministic targets.
_SOURCE_TEMPLATES = [
    ("fw-edge-01 Cisco ASA", 12, "healthy"),
    ("fw-edge-02 Cisco ASA", 12, "healthy"),
    ("dc01 Windows Security", 13, "healthy"),
    ("dc02 Windows Security", 13, "silent"),          # -> NO_EVENTS
    ("web-cluster Linux", 2, "volume_drop"),          # -> VOLUME_DROP
    ("pan-fw-01 Palo Alto", 95, "healthy"),
    ("okta-prod Okta", 145, "parsing_degraded"),      # -> PARSING_DEGRADATION
    ("aws-org CloudTrail", 351, "spike"),             # -> VOLUME_SPIKE
    ("legacy-unix Linux", 2, "disabled"),
]


class MockQRadarProvider(QRadarProvider):
    capabilities = frozenset(
        {
            ProviderCapability.INVENTORY,
            ProviderCapability.OFFENSES,
            ProviderCapability.AQL_EXECUTION,
            ProviderCapability.CONFIG_SNAPSHOTS,
        }
    )

    def __init__(self, *, seed: int = 1337, now: datetime | None = None) -> None:
        self._seed = seed
        self._now = now or datetime.now(UTC)
        self._rng = random.Random(seed)  # noqa: S311 (not cryptographic)
        # Active Ariel searches: search_id -> (aql, created_at)
        self._searches: dict[str, tuple[str, datetime]] = {}
        self._search_counter = 0

    # -- health / inventory -------------------------------------------------
    async def get_instance_info(self) -> InstanceInfoDTO:
        return InstanceInfoDTO(
            version="7.5.0",
            build="20260601123456",
            reachable=True,
            raw_about={"release_name": "QRadar 7.5.0 UP9", "external_version": "7.5.0"},
        )

    async def list_log_sources(self) -> list[LogSourceDTO]:
        out: list[LogSourceDTO] = []
        for idx, (name, type_id, shape) in enumerate(_SOURCE_TEMPLATES, start=1):
            type_name = dict(_LOG_SOURCE_TYPES).get(type_id)
            enabled = shape != "disabled"
            avg_eps, last_event = self._shape_liveness(shape)
            out.append(
                LogSourceDTO(
                    qradar_id=1000 + idx,
                    name=name,
                    description=f"Mock log source ({shape})",
                    type_id=type_id,
                    type_name=type_name,
                    protocol_type_id=0,
                    enabled=enabled,
                    status="SUCCESS" if enabled and shape != "silent" else "ERROR",
                    credibility=self._rng.randint(5, 10),
                    target_event_collector_id=1,
                    average_eps=avg_eps,
                    last_event_time=last_event,
                )
            )
        return out

    def _shape_liveness(self, shape: str) -> tuple[float, datetime | None]:
        """Map a health-shape to (average_eps, last_event_time)."""
        if shape == "silent":
            return 0.0, self._now - timedelta(hours=6)
        if shape == "disabled":
            return 0.0, None
        if shape == "volume_drop":
            return 3.0, self._now - timedelta(minutes=2)
        if shape == "spike":
            return 950.0, self._now - timedelta(seconds=20)
        base = {"healthy": 45.0, "parsing_degraded": 40.0}.get(shape, 30.0)
        jitter = self._rng.uniform(-5, 5)
        return round(base + jitter, 2), self._now - timedelta(seconds=self._rng.randint(5, 90))

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        for src in await self.list_log_sources():
            if src.qradar_id == qradar_id:
                return src
        return None

    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:
        return [LogSourceTypeDTO(type_id=t, name=n) for t, n in _LOG_SOURCE_TYPES]

    async def get_log_source_metrics(
        self, window_start: datetime, window_end: datetime
    ) -> list[LogSourceMetricSample]:
        """Deterministic per-interval samples.

        A healthy source follows a weekly-seasonal EPS curve (higher on weekday
        business hours) with small per-interval jitter seeded from the interval,
        so a baseline built over history is stable and reproducible. The injected
        failure shapes deviate from that curve in the specific way each anomaly
        type detects.
        """
        bucket_seconds = int((window_end - window_start).total_seconds()) or 300
        samples: list[LogSourceMetricSample] = []
        for idx, (_name, _type_id, shape) in enumerate(_SOURCE_TEMPLATES, start=1):
            qid = 1000 + idx
            base_eps = self._seasonal_eps(window_start, shape)
            sample = self._build_sample(qid, window_start, bucket_seconds, base_eps, shape)
            samples.append(sample)
        return samples

    def _seasonal_eps(self, when: datetime, shape: str) -> float:
        # ISO weekday 1..7, hour 0..23.
        weekday = when.isoweekday()
        hour = when.hour
        business = weekday <= 5 and 8 <= hour < 18
        base = 50.0 if business else 18.0
        # Deterministic jitter from the interval so history is reproducible.
        seed = int(when.timestamp()) // 300
        jitter_rng = random.Random(f"{shape}:{seed}")  # noqa: S311
        return base * (0.9 + 0.2 * jitter_rng.random())

    def _build_sample(
        self, qid: int, start: datetime, bucket_seconds: int, base_eps: float, shape: str
    ) -> LogSourceMetricSample:
        event_count = int(base_eps * bucket_seconds)
        common: dict = dict(  # noqa: C408 - kwargs dict built incrementally below
            qradar_id=qid,
            bucket_start=start,
            bucket_seconds=bucket_seconds,
            last_event_at=start + timedelta(seconds=bucket_seconds - 5),
            event_delay_seconds=5.0,
            parsed_username_ratio=0.9,
            parsed_source_ip_ratio=0.95,
            unknown_event_count=int(event_count * 0.01),
            stored_event_count=event_count,
            distinct_qid_count=20,
            distinct_username_count=40,
            distinct_source_ip_count=60,
            collection_error_count=0,
            payload_signature=f"sig-{qid}-{int(start.timestamp()) // 300}",
        )

        if shape == "silent":
            common.update(event_count=0, average_eps=0.0, peak_eps=0.0,
                          last_event_at=start - timedelta(hours=6), event_delay_seconds=21600.0,
                          unknown_event_count=0, stored_event_count=0,
                          distinct_qid_count=0, distinct_username_count=0,
                          distinct_source_ip_count=0)
            return LogSourceMetricSample(**common)
        if shape == "disabled":
            common.update(event_count=0, average_eps=0.0, peak_eps=0.0, last_event_at=None,
                          event_delay_seconds=None, unknown_event_count=0, stored_event_count=0,
                          distinct_qid_count=0, distinct_username_count=0,
                          distinct_source_ip_count=0)
            return LogSourceMetricSample(**common)
        if shape == "volume_drop":
            base_eps *= 0.05  # collapse to 5% of normal
        if shape == "spike":
            base_eps *= 20.0
        if shape == "parsing_degraded":
            common.update(parsed_username_ratio=0.15, parsed_source_ip_ratio=0.2,
                          unknown_event_count=int(event_count * 0.6))

        event_count = int(base_eps * bucket_seconds)
        common.update(
            event_count=event_count,
            average_eps=round(base_eps, 2),
            peak_eps=round(base_eps * 1.5, 2),
            stored_event_count=event_count,
        )
        return LogSourceMetricSample(**common)

    async def list_rules(self) -> list[AnalyticsRuleDTO]:
        specs = [
            ("Excessive Failed Logins", ["T1110"], True, False, 12.0),
            ("Impossible Travel", ["T1078"], True, False, 3.0),
            ("Malware Beacon Detected", ["T1071"], True, False, 0.4),
            ("Cleartext Protocol Usage", ["T1040"], True, True, 88.0),  # noisy
            ("Dormant Account Reactivated", ["T1078.002"], False, False, 0.0),  # disabled/stale
            ("Data Exfil Over DNS", ["T1048.003"], True, False, 0.0),  # never fires
        ]
        out: list[AnalyticsRuleDTO] = []
        for idx, (name, mitre, enabled, _noisy, cap) in enumerate(specs, start=1):
            out.append(
                AnalyticsRuleDTO(
                    qradar_id=5000 + idx,
                    name=name,
                    rule_type="EVENT",
                    enabled=enabled,
                    origin="USER",
                    is_building_block=False,
                    owner="soc-team",
                    average_capacity=cap,
                    categories=["Authentication"] if "T1110" in mitre else [],
                    mitre_techniques=mitre,
                    modified_at=self._now - timedelta(days=idx * 3),
                )
            )
        return out

    # -- offenses -----------------------------------------------------------
    async def list_offenses(self, *, open_only: bool = True) -> list[OffenseDTO]:
        out: list[OffenseDTO] = []
        specs = [
            ("Multiple Failed Logins then Success", "OPEN", 8, None, 240),
            ("Malware Beacon to Known C2", "OPEN", 9, "analyst.jones", 30),
            ("Suspicious Admin Group Change", "OPEN", 6, None, 1440),
            ("Port Scan from Internal Host", "CLOSED", 4, "analyst.lee", 60),
        ]
        for idx, (desc, status, mag, assignee, age_min) in enumerate(specs, start=1):
            if open_only and status != "OPEN":
                continue
            start = self._now - timedelta(minutes=age_min)
            out.append(
                OffenseDTO(
                    qradar_id=9000 + idx,
                    description=desc,
                    status=status,
                    magnitude=mag,
                    severity=mag,
                    credibility=self._rng.randint(4, 9),
                    relevance=self._rng.randint(3, 8),
                    assigned_to=assignee,
                    offense_type=3,
                    offense_source="10.20.30." + str(40 + idx),
                    event_count=self._rng.randint(50, 5000),
                    flow_count=self._rng.randint(0, 500),
                    device_count=self._rng.randint(1, 6),
                    start_time=start,
                    last_updated_time=self._now - timedelta(minutes=self._rng.randint(0, 20)),
                    categories=["Authentication", "Suspicious Activity"],
                    source_addresses=["203.0.113." + str(idx)],
                    local_destination_addresses=["10.20.30." + str(40 + idx)],
                    rule_ids=[5000 + idx],
                )
            )
        return out

    # -- Ariel lifecycle ----------------------------------------------------
    async def create_ariel_search(self, aql: str) -> ArielSearchHandleDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        self._search_counter += 1
        search_id = f"mock-{self._search_counter:06d}"
        self._searches[search_id] = (aql, self._now)
        return ArielSearchHandleDTO(search_id=search_id, status="EXECUTE", progress=0)

    async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        if search_id not in self._searches:
            return ArielSearchStatusDTO(
                search_id=search_id, status="ERROR", error_messages=["unknown search id"]
            )
        aql, _ = self._searches[search_id]
        rows = self._deterministic_rows(aql)
        # Mock completes immediately — the polling loop must still handle a
        # COMPLETED-on-first-poll response correctly.
        return ArielSearchStatusDTO(
            search_id=search_id, status="COMPLETED", progress=100, record_count=len(rows)
        )

    async def get_ariel_search_results(
        self, search_id: str, *, max_rows: int
    ) -> ArielSearchResultsDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        aql, _ = self._searches.get(search_id, ("", self._now))
        rows = self._deterministic_rows(aql)
        truncated = len(rows) > max_rows
        cols = list(rows[0].keys()) if rows else []
        return ArielSearchResultsDTO(
            search_id=search_id,
            columns=cols,
            rows=rows[:max_rows],
            total_count=len(rows),
            truncated=truncated,
        )

    async def cancel_ariel_search(self, search_id: str) -> None:
        self.require(ProviderCapability.AQL_EXECUTION)
        self._searches.pop(search_id, None)

    def _deterministic_rows(self, aql: str) -> list[dict]:
        """Return stable pseudo-results keyed off the query text.

        The row shape mimics a typical aggregate AQL (a GROUP BY producing a
        label and a count), which is what our scheduled searches use.
        """
        digest = hashlib.sha256(aql.encode()).hexdigest()
        n = int(digest[:2], 16) % 8  # 0..7 rows, deterministic per query
        rng = random.Random(digest)  # noqa: S311
        return [
            {"grouping": f"host-{i}", "event_count": rng.randint(1, 500)}
            for i in range(n)
        ]
