"""Deterministic alert fingerprints (application-layer dedup)."""

from __future__ import annotations

import uuid

from app.alerts.fingerprint import (
    anomaly_fingerprint,
    compute_fingerprint,
    search_failure_fingerprint,
    search_threshold_fingerprint,
)


def test_same_condition_same_fingerprint() -> None:
    lsid = uuid.uuid4()
    a = anomaly_fingerprint(lsid, "VOLUME_DROP")
    b = anomaly_fingerprint(lsid, "VOLUME_DROP")
    assert a == b


def test_different_anomaly_type_differs() -> None:
    lsid = uuid.uuid4()
    assert anomaly_fingerprint(lsid, "VOLUME_DROP") != anomaly_fingerprint(lsid, "VOLUME_SPIKE")


def test_different_source_differs() -> None:
    assert anomaly_fingerprint(uuid.uuid4(), "NO_EVENTS") != anomaly_fingerprint(
        uuid.uuid4(), "NO_EVENTS"
    )


def test_fingerprint_is_stable_across_string_and_uuid() -> None:
    lsid = uuid.uuid4()
    assert anomaly_fingerprint(lsid, "NO_EVENTS") == anomaly_fingerprint(str(lsid), "NO_EVENTS")


def test_normalisation_ignores_case_and_whitespace() -> None:
    a = compute_fingerprint(source_type="Log_Source", source_id="ABC ", condition="anomaly")
    b = compute_fingerprint(source_type="log_source", source_id="abc", condition="anomaly")
    assert a == b


def test_fingerprint_is_prefixed_by_condition() -> None:
    fp = search_threshold_fingerprint(uuid.uuid4())
    assert fp.startswith("search_threshold:")


def test_search_failure_is_prefixed_by_condition() -> None:
    assert search_failure_fingerprint(uuid.uuid4()).startswith("search_failure:")


def test_search_failure_and_threshold_never_collide() -> None:
    # Same search, two unrelated problems: a search that is erroring and a
    # search whose result crossed its threshold must be separately alertable.
    sid = uuid.uuid4()
    assert search_failure_fingerprint(sid) != search_threshold_fingerprint(sid)


def test_search_failure_is_stable_and_source_scoped() -> None:
    sid = uuid.uuid4()
    assert search_failure_fingerprint(sid) == search_failure_fingerprint(str(sid))
    assert search_failure_fingerprint(sid) != search_failure_fingerprint(uuid.uuid4())
