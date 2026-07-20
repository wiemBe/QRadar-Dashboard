"""Characterization tests for offense change detection and resume bounding.

`offense_content_hash` decides whether polling writes a row. Get it wrong in one
direction and the history table fills with 288 identical rows a day; wrong in
the other and a real state change silently disappears. Both directions are
asserted here.

`_resume_point` decides how far back a run reaches. It is the bound that stops a
collector that has been down for a month from attempting a month-long catch-up
in a single tick.

Database-backed collection behaviour lives in
tests/integration/test_offense_collection.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.collectors.offense_collector import (
    _HASHED_FIELDS,
    OffenseCollector,
    offense_content_hash,
)
from app.core.config import Settings, get_settings
from app.models.monitoring import CollectionWatermark
from app.providers.dto import OffenseDTO

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def settings(**overrides) -> Settings:
    return get_settings().model_copy(update=overrides)


def make_dto(**overrides) -> OffenseDTO:
    base = dict(
        qradar_id=4242,
        description="Multiple failed logins followed by success",
        status="OPEN",
        magnitude=7,
        severity=6,
        credibility=5,
        relevance=4,
        assigned_to=None,
        offense_type=3,
        offense_source="10.1.2.3",
        source_network="corp.internal",
        event_count=120,
        flow_count=0,
        device_count=2,
        source_count=1,
        destination_count=3,
        start_time=NOW - timedelta(hours=4),
        last_updated_time=NOW - timedelta(minutes=5),
        close_time=None,
        closing_reason_id=None,
        categories=["Authentication"],
        source_addresses=["10.1.2.3"],
        local_destination_addresses=["10.9.9.9"],
        usernames=["svc_backup"],
        log_source_ids=[101],
        rule_ids=[500, 501],
    )
    base.update(overrides)
    return OffenseDTO(**base)


def collector(cfg: Settings | None = None) -> OffenseCollector:
    return OffenseCollector(
        session=None,  # type: ignore[arg-type] - _resume_point touches no session
        provider=None,  # type: ignore[arg-type]
        settings=cfg or settings(),
        clock=lambda: NOW,
    )


# ============================================================================
# Change detection.
# ============================================================================
class TestContentHashStability:
    def test_identical_offenses_hash_identically(self) -> None:
        assert offense_content_hash(make_dto()) == offense_content_hash(make_dto())

    def test_hash_is_stable_across_repeated_calls(self) -> None:
        dto = make_dto()
        assert len({offense_content_hash(dto) for _ in range(5)}) == 1

    def test_capture_time_does_not_enter_the_hash(self) -> None:
        """Otherwise every poll would look like a change."""
        assert offense_content_hash(make_dto()) == offense_content_hash(
            make_dto(last_updated_time=NOW)
        )

    def test_last_updated_time_alone_is_not_a_meaningful_change(self) -> None:
        """QRadar bumps last_persisted_time on offenses that did not change."""
        a = offense_content_hash(make_dto(last_updated_time=NOW - timedelta(hours=1)))
        b = offense_content_hash(make_dto(last_updated_time=NOW))
        assert a == b

    def test_start_time_is_immutable_and_excluded(self) -> None:
        assert offense_content_hash(make_dto()) == offense_content_hash(
            make_dto(start_time=NOW - timedelta(days=9))
        )

    def test_offense_type_name_is_not_hashed(self) -> None:
        """A lookup-table rename is not an offense state change."""
        assert offense_content_hash(make_dto(offense_type_name="Source IP")) == (
            offense_content_hash(make_dto(offense_type_name="Src IP (renamed)"))
        )


class TestMeaningfulChangeDetection:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("status", "CLOSED"),
            ("magnitude", 9),
            ("severity", 8),
            ("credibility", 2),
            ("relevance", 1),
            ("assigned_to", "alice"),
            ("offense_type", 4),
            ("offense_source", "10.4.4.4"),
            ("source_network", "dmz"),
            ("event_count", 121),
            ("flow_count", 3),
            ("device_count", 5),
            ("source_count", 2),
            ("destination_count", 4),
            ("close_time", NOW),
            ("closing_reason_id", 2),
            ("description", "Something else entirely"),
            ("categories", ["Authentication", "Exploit"]),
            ("source_addresses", ["10.1.2.3", "10.1.2.4"]),
            ("local_destination_addresses", ["10.9.9.9", "10.9.9.10"]),
            ("usernames", ["svc_backup", "root"]),
            ("log_source_ids", [101, 102]),
            ("rule_ids", [500, 501, 502]),
        ],
    )
    def test_each_meaningful_field_change_produces_a_new_hash(
        self, field: str, value: object
    ) -> None:
        assert offense_content_hash(make_dto()) != offense_content_hash(
            make_dto(**{field: value})
        )

    def test_assignment_to_an_analyst_is_detected(self) -> None:
        """Assignment latency is a headline SOC metric; it must never be missed."""
        unassigned = offense_content_hash(make_dto(assigned_to=None))
        assigned = offense_content_hash(make_dto(assigned_to="alice"))
        reassigned = offense_content_hash(make_dto(assigned_to="bob"))
        assert len({unassigned, assigned, reassigned}) == 3

    def test_status_transition_open_to_closed_is_detected(self) -> None:
        assert offense_content_hash(make_dto(status="OPEN")) != offense_content_hash(
            make_dto(status="CLOSED", close_time=NOW, closing_reason_id=1)
        )

    def test_contributing_rule_change_is_detected(self) -> None:
        """A new rule contributing to an offense changes what the offense means."""
        assert offense_content_hash(make_dto(rule_ids=[500])) != offense_content_hash(
            make_dto(rule_ids=[500, 999])
        )

    def test_entity_list_growth_is_detected(self) -> None:
        assert offense_content_hash(
            make_dto(source_addresses=["10.0.0.1"])
        ) != offense_content_hash(make_dto(source_addresses=["10.0.0.1", "10.0.0.2"]))

    def test_none_and_empty_list_are_distinguishable_from_a_value(self) -> None:
        assert offense_content_hash(make_dto(usernames=[])) != offense_content_hash(
            make_dto(usernames=["root"])
        )

    def test_zero_and_none_are_not_conflated(self) -> None:
        """A count of zero is data; a missing count is not."""
        assert offense_content_hash(make_dto(event_count=0)) != offense_content_hash(
            make_dto(event_count=None)
        )

    def test_hashed_field_set_matches_the_dto(self) -> None:
        """A DTO field added without a hashing decision would silently go unwatched.

        Pinning the exclusion set means a new OffenseDTO field fails this test
        until someone decides, explicitly, whether a change to it matters.
        """
        unhashed = set(OffenseDTO.model_fields) - set(_HASHED_FIELDS)
        assert unhashed == {
            "qradar_id",            # the natural key, not a mutable attribute
            "start_time",           # immutable once the offense exists
            "last_updated_time",    # bumped by QRadar without a state change
            "closing_reason",       # display text for closing_reason_id (hashed)
            "offense_type_name",    # display text for offense_type (hashed)
        }
        # Everything the hash does cover must actually exist on the DTO.
        assert set(_HASHED_FIELDS) <= set(OffenseDTO.model_fields)

    def test_hash_is_a_sha256_hex_digest(self) -> None:
        digest = offense_content_hash(make_dto())
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")


class TestListOrdering:
    """QRadar does not promise a stable order for offense entity lists.

    The hash is computed over the list as received, so a reordered but otherwise
    identical list currently reads as a change. See docs/PHASE3-HANDOFF.md --
    this is recorded as a known limitation rather than silently normalized,
    because normalizing would also erase genuine "first seen" ordering that the
    aggregation layer may later rely on.
    """

    def test_reordered_source_addresses_currently_change_the_hash(self) -> None:
        a = offense_content_hash(make_dto(source_addresses=["10.0.0.1", "10.0.0.2"]))
        b = offense_content_hash(make_dto(source_addresses=["10.0.0.2", "10.0.0.1"]))
        assert a != b

    def test_reordered_rule_ids_currently_change_the_hash(self) -> None:
        a = offense_content_hash(make_dto(rule_ids=[500, 501]))
        b = offense_content_hash(make_dto(rule_ids=[501, 500]))
        assert a != b

    def test_dict_key_order_never_affects_the_hash(self) -> None:
        """The one ordering the implementation *does* neutralize, via sort_keys."""
        forwards = make_dto(status="OPEN", magnitude=7)
        backwards = make_dto(magnitude=7, status="OPEN")
        assert offense_content_hash(forwards) == offense_content_hash(backwards)


# ============================================================================
# Resume point / bounded backfill.
# ============================================================================
class TestResumePoint:
    def make_watermark(self, watermark_at: datetime | None) -> CollectionWatermark:
        return CollectionWatermark(
            id=uuid.uuid4(),
            instance_id=uuid.uuid4(),
            collector="offense_snapshot",
            watermark_at=watermark_at,
            intervals_collected=0,
        )

    def test_first_run_reaches_back_exactly_the_backfill_limit(self) -> None:
        """A fresh install must not attempt to ingest years of offense history."""
        cfg = settings(offense_max_backfill_hours=48)
        resume = collector(cfg)._resume_point(self.make_watermark(None), NOW)
        assert resume == NOW - timedelta(hours=48)

    def test_a_recent_watermark_is_used_verbatim(self) -> None:
        cfg = settings(offense_max_backfill_hours=48)
        wm = self.make_watermark(NOW - timedelta(hours=2))
        assert collector(cfg)._resume_point(wm, NOW) == NOW - timedelta(hours=2)

    def test_a_stale_watermark_is_clamped_to_the_backfill_floor(self) -> None:
        """A collector down for a month resumes at the limit, not a month back."""
        cfg = settings(offense_max_backfill_hours=48)
        wm = self.make_watermark(NOW - timedelta(days=30))
        assert collector(cfg)._resume_point(wm, NOW) == NOW - timedelta(hours=48)

    def test_the_floor_is_inclusive_at_the_boundary(self) -> None:
        cfg = settings(offense_max_backfill_hours=48)
        wm = self.make_watermark(NOW - timedelta(hours=48))
        assert collector(cfg)._resume_point(wm, NOW) == NOW - timedelta(hours=48)

    def test_a_naive_watermark_is_interpreted_as_utc(self) -> None:
        """Postgres can return naive datetimes; comparing them would raise."""
        cfg = settings(offense_max_backfill_hours=48)
        wm = self.make_watermark((NOW - timedelta(hours=2)).replace(tzinfo=None))
        resume = collector(cfg)._resume_point(wm, NOW)
        assert resume == NOW - timedelta(hours=2)
        assert resume.tzinfo is not None

    def test_a_future_watermark_is_not_rewound(self) -> None:
        """Clock skew must not make the collector re-read a span it has passed."""
        cfg = settings(offense_max_backfill_hours=48)
        wm = self.make_watermark(NOW + timedelta(hours=1))
        assert collector(cfg)._resume_point(wm, NOW) == NOW + timedelta(hours=1)
