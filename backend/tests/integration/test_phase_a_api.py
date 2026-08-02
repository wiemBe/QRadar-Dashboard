"""Phase A behavioural API: authorization, filtering, pagination and bounds.

The authorization block is a regression guard of the same kind as Phase 3's.
Contributor evidence carries source and destination addresses and, where the
DSM supplies them, usernames; none of it may reach an unauthenticated caller.
The guard lives on the router, so adding an endpoint to this module cannot
reintroduce the gap.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_session
from app.main import create_app
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    DimensionAvailability,
    EvidenceStatus,
    Severity,
)
from app.models.explanation import (
    AnomalyExplanation,
    AnomalyExplanationContributor,
    AnomalyExplanationDimension,
)
from app.models.instance import QRadarInstance
from app.models.log_source import (
    AnomalyStateTransition,
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceMetric,
)
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run Phase A API integration tests",
    ),
]

# Relative to the real clock: the list endpoints default to the last 24 hours,
# so a fixed literal date would drift out of the default window and make these
# tests fail with the passage of time rather than with a real regression.
NOW = (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0)

def _iso(dt: datetime) -> str:
    """UTC timestamp in the Z spelling.

    `isoformat()` emits `+00:00`, and a bare `+` in a query string decodes to a
    space, so the parameter arrives malformed.
    """
    return dt.isoformat().replace("+00:00", "Z")


ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())
ALERTS_ONLY = Principal(subject="alerts", permissions=frozenset({"alert:ack"}))

PHASE_A_ENDPOINTS = [
    "/api/v1/anomalies",
    "/api/v1/anomalies/summary",
    "/api/v1/behavior/sources",
]


def client_for(principal: Principal) -> TestClient:
    url = os.environ["TEST_DATABASE_URL"]
    # NullPool: see the Phase 3 API tests -- TestClient runs each request on its
    # own event loop and a pooled asyncpg connection is pinned to its opener.
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


async def _seed(session) -> dict:
    """One source, a reliable baseline, a bucket, and an explained spike."""
    inst = QRadarInstance(
        name=f"inst-{uuid.uuid4().hex[:8]}", console_host="mock", provider_kind="mock"
    )
    session.add(inst)
    await session.flush()

    src = LogSource(
        instance_id=inst.id, qradar_id=4242, name="LAB Firewall", monitoring_enabled=True
    )
    session.add(src)
    await session.flush()

    session.add(
        LogSourceBaseline(
            log_source_id=src.id, metric_name="average_eps",
            weekday=NOW.isoweekday(), hour=NOW.hour,
            median=2.0, mad=0.2, p05=1.7, p95=2.3,
            sample_count=30, is_reliable=True, baseline_version=2,
            observations=[2.0], completeness=0.9,
        )
    )
    session.add(
        LogSourceMetric(
            log_source_id=src.id, bucket_start=NOW, bucket_seconds=300,
            event_count=1800, average_eps=6.0, peak_eps=6.5,
            completeness=BucketCompleteness.COMPLETE,
        )
    )
    anomaly = LogSourceAnomaly(
        log_source_id=src.id,
        anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.HIGH,
        state=AnomalyState.OPEN,
        detected_at=NOW,
        opened_at=NOW,
        anomaly_start=NOW,
        anomaly_end=NOW + timedelta(minutes=5),
        observed_value=6.0,
        expected_value=2.0,
        deviation_ratio=3.0,
        robust_z=13.5,
        absolute_delta=1200.0,
        consecutive_buckets=2,
        confidence=0.82,
        baseline_version=2,
        policy_version=1,
        evidence_status=EvidenceStatus.PARTIAL,
        explanation="average EPS 6.00 is above the baseline median 2.00",
        # Shaped exactly as the volume detector writes it, including the nested
        # `extra` facts, so the detection projection is exercised against the
        # real payload rather than a convenient one.
        details={
            "reason": "average EPS 6.00 is above the baseline median 2.00",
            "baseline_low": 1.7,
            "baseline_high": 2.3,
            "threshold": 3.5,
            "sample_count": 30,
            "signal": "ANOMALOUS",
            "extra": {
                "observed_eps": 6.0,
                "expected_eps": 2.0,
                "observed_events": 1800.0,
                "expected_events": 600.0,
                "absolute_delta_events": 1200.0,
                "bucket_seconds": 300.0,
                "baseline_completeness": 0.9,
                "baseline_version": 2,
                "ratio": 3.0,
                "robust_score_status": "OK",
                "robust_z": 13.5,
            },
        },
    )
    session.add(anomaly)
    await session.flush()

    session.add(
        AnomalyStateTransition(
            anomaly_id=anomaly.id, from_state=AnomalyState.CANDIDATE,
            to_state=AnomalyState.OPEN, occurred_at=NOW, bucket_start=NOW,
            reason="2 consecutive abnormal bucket(s)", actor="anomaly-engine",
            observed_value=6.0, expected_value=2.0,
        )
    )

    package = AnomalyExplanation(
        anomaly_id=anomaly.id,
        status=EvidenceStatus.PARTIAL,
        anomaly_window_start=NOW,
        anomaly_window_end=NOW + timedelta(minutes=5),
        baseline_window_start=NOW - timedelta(minutes=15),
        baseline_window_end=NOW,
        comparison_strategy="recent_normal_window",
        anomaly_total_events=1800,
        baseline_total_events=600,
        query_provenance={"queries": [{"dimension": "source_ip", "aql": "MOCK"}]},
        schema_version=1,
    )
    session.add(package)
    await session.flush()

    session.add(
        AnomalyExplanationDimension(
            explanation_id=package.id, dimension="source_ip",
            availability=DimensionAvailability.AVAILABLE,
            baseline_distinct_count=5, anomaly_distinct_count=42,
            cardinality_ratio=8.4, new_value_count=37,
            baseline_top_share=0.33, anomaly_top_share=0.68,
        )
    )
    session.add(
        AnomalyExplanationDimension(
            explanation_id=package.id, dimension="username",
            availability=DimensionAvailability.UNAVAILABLE,
            detail="field is not populated for this log source",
        )
    )
    session.add(
        AnomalyExplanationContributor(
            explanation_id=package.id, dimension="source_ip",
            value="203.0.113.50", baseline_count=12, anomaly_count=4820,
            absolute_delta=4808, percent_delta=400.6, anomaly_share=0.68,
            baseline_share=0.02, contribution_share=0.68,
            baseline_rank=5, anomaly_rank=1, rank=1,
            is_new=False, is_disappeared=False,
        )
    )
    await session.commit()
    return {"source_id": src.id, "anomaly_id": anomaly.id, "instance_id": inst.id}


@pytest.fixture
def seeded(db_schema):
    """Seed via a dedicated engine, then hand the ids to the TestClient."""
    import asyncio

    url = os.environ["TEST_DATABASE_URL"]

    async def _go():
        engine = create_async_engine(url, future=True, poolclass=NullPool)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            ids = await _seed(session)
        await engine.dispose()
        return ids

    return asyncio.run(_go())


# ============================================================================
class TestAuthorization:
    @pytest.mark.parametrize("path", PHASE_A_ENDPOINTS)
    def test_a_principal_without_read_permission_is_refused(
        self, path: str, db_schema
    ) -> None:
        assert client_for(NOBODY).get(path).status_code == 403

    @pytest.mark.parametrize("path", PHASE_A_ENDPOINTS)
    def test_an_unrelated_permission_does_not_grant_access(
        self, path: str, db_schema
    ) -> None:
        """alert:ack must not imply the right to read contributor evidence."""
        assert client_for(ALERTS_ONLY).get(path).status_code == 403

    @pytest.mark.parametrize("path", PHASE_A_ENDPOINTS)
    def test_the_read_wildcard_grants_access(self, path: str, db_schema) -> None:
        assert client_for(READER).get(path).status_code == 200

    @pytest.mark.parametrize("path", PHASE_A_ENDPOINTS)
    def test_admin_retains_access(self, path: str, db_schema) -> None:
        assert client_for(ADMIN).get(path).status_code == 200

    def test_detail_routes_are_guarded_before_existence_is_revealed(
        self, db_schema
    ) -> None:
        """A 403 must precede the 404 -- existence is itself information."""
        denied = client_for(NOBODY)
        unknown = uuid.uuid4()
        assert denied.get(f"/api/v1/anomalies/{unknown}").status_code == 403
        assert denied.get(f"/api/v1/behavior/sources/{unknown}").status_code == 403
        assert (
            denied.get(f"/api/v1/behavior/sources/{unknown}/metrics").status_code == 403
        )


class TestAnomalyList:
    def test_returns_paged_shape(self, seeded) -> None:
        body = client_for(READER).get("/api/v1/anomalies").json()
        assert body["total"] == 1
        assert body["limit"] == 50
        assert body["offset"] == 0
        assert body["items"][0]["anomaly_type"] == "VOLUME_SPIKE"
        assert body["items"][0]["state"] == "OPEN"

    def test_filters_by_detector_type(self, seeded) -> None:
        c = client_for(READER)
        assert c.get("/api/v1/anomalies?anomaly_type=VOLUME_SPIKE").json()["total"] == 1
        assert c.get("/api/v1/anomalies?anomaly_type=NO_EVENTS").json()["total"] == 0

    def test_filters_by_state_and_severity(self, seeded) -> None:
        c = client_for(READER)
        assert c.get("/api/v1/anomalies?state=OPEN").json()["total"] == 1
        assert c.get("/api/v1/anomalies?state=RESOLVED").json()["total"] == 0
        assert c.get("/api/v1/anomalies?severity=HIGH").json()["total"] == 1
        assert c.get("/api/v1/anomalies?severity=LOW").json()["total"] == 0

    def test_filters_by_source(self, seeded) -> None:
        c = client_for(READER)
        sid = seeded["source_id"]
        assert c.get(f"/api/v1/anomalies?log_source_id={sid}").json()["total"] == 1
        other = uuid.uuid4()
        assert c.get(f"/api/v1/anomalies?log_source_id={other}").json()["total"] == 0

    def test_active_only_filter(self, seeded) -> None:
        body = client_for(READER).get("/api/v1/anomalies?active_only=true").json()
        assert body["total"] == 1

    def test_pagination_offset(self, seeded) -> None:
        body = client_for(READER).get("/api/v1/anomalies?limit=1&offset=5").json()
        assert body["items"] == []
        assert body["total"] == 1

    def test_filters_by_evidence_status(self, seeded) -> None:
        c = client_for(READER)
        assert c.get("/api/v1/anomalies?evidence_status=PARTIAL").json()["total"] == 1
        assert c.get("/api/v1/anomalies?evidence_status=COMPLETE").json()["total"] == 0

    def test_filters_by_instance(self, seeded) -> None:
        """Anomalies inherit their instance from the source they belong to."""
        c = client_for(READER)
        iid = seeded["instance_id"]
        assert c.get(f"/api/v1/anomalies?instance_id={iid}").json()["total"] == 1
        assert c.get(f"/api/v1/anomalies?instance_id={uuid.uuid4()}").json()["total"] == 0

    def test_items_carry_the_source_name(self, seeded) -> None:
        """The list renders a name per row; resolving it client-side would mean
        one request per row against an endpoint the list already paginates."""
        item = client_for(READER).get("/api/v1/anomalies").json()["items"][0]
        assert item["log_source_name"] == "LAB Firewall"

    def test_duration_is_serialized_not_left_to_the_client(self, seeded) -> None:
        item = client_for(READER).get("/api/v1/anomalies").json()["items"][0]
        assert item["duration_seconds"] == 300.0

    def test_a_still_running_anomaly_reports_no_duration(self, seeded) -> None:
        """No end yet means no duration. Substituting `now` would report a
        number that grows on every page refresh as if the incident were
        measured, so the field stays null until an end exists."""
        from app.schemas.anomaly import AnomalyListItem as Item

        item = Item(
            id=uuid.uuid4(),
            log_source_id=uuid.uuid4(),
            anomaly_type=AnomalyType.VOLUME_SPIKE,
            state=AnomalyState.OPEN,
            severity=Severity.HIGH,
            detected_at=NOW,
            anomaly_start=NOW,
            evidence_status=EvidenceStatus.NOT_REQUESTED,
        )
        assert item.model_dump()["duration_seconds"] is None


class TestRangeBounds:
    def test_inverted_range_is_rejected(self, seeded) -> None:
        r = client_for(READER).get(
            f"/api/v1/anomalies?since={_iso(NOW)}"
            f"&until={_iso(NOW - timedelta(hours=2))}"
        )
        assert r.status_code == 422

    def test_an_excessive_range_is_rejected(self, seeded) -> None:
        """An unbounded range over a hypertable is a self-inflicted DoS."""
        r = client_for(READER).get(
            f"/api/v1/anomalies?since=1990-01-01T00:00:00Z&until={_iso(NOW)}"
        )
        assert r.status_code == 422

    def test_metric_history_enforces_the_same_bound(self, seeded) -> None:
        sid = seeded["source_id"]
        r = client_for(READER).get(
            f"/api/v1/behavior/sources/{sid}/metrics"
            f"?since=1990-01-01T00:00:00Z&until={_iso(NOW)}"
        )
        assert r.status_code == 422


class TestInvestigationDetail:
    def test_detail_carries_lifecycle_and_evidence(self, seeded) -> None:
        r = client_for(READER).get(f"/api/v1/anomalies/{seeded['anomaly_id']}")
        assert r.status_code == 200
        body = r.json()

        assert body["log_source_name"] == "LAB Firewall"
        assert body["observed_value"] == 6.0
        assert body["expected_value"] == 2.0
        assert body["deviation_ratio"] == 3.0
        assert body["evidence_status"] == "PARTIAL"

        assert len(body["transitions"]) == 1
        assert body["transitions"][0]["to_state"] == "OPEN"
        assert body["transitions"][0]["reason"]

    def test_contributors_are_grouped_under_their_dimension(self, seeded) -> None:
        body = client_for(READER).get(
            f"/api/v1/anomalies/{seeded['anomaly_id']}"
        ).json()
        package = body["explanation_package"]
        assert package["comparison_strategy"] == "recent_normal_window"

        by_dim = {d["dimension"]: d for d in package["dimensions"]}
        source_ip = by_dim["source_ip"]
        assert source_ip["cardinality_ratio"] == 8.4
        assert source_ip["new_value_count"] == 37
        assert source_ip["contributors"][0]["value"] == "203.0.113.50"
        assert source_ip["contributors"][0]["absolute_delta"] == 4808

    def test_unavailable_dimensions_are_visible_to_the_client(self, seeded) -> None:
        """An UNAVAILABLE dimension the UI cannot see is one the operator
        will assume was clean."""
        body = client_for(READER).get(
            f"/api/v1/anomalies/{seeded['anomaly_id']}"
        ).json()
        by_dim = {d["dimension"]: d for d in body["explanation_package"]["dimensions"]}
        assert by_dim["username"]["availability"] == "UNAVAILABLE"
        assert by_dim["username"]["contributors"] == []
        # Never a fabricated zero.
        assert by_dim["username"]["baseline_distinct_count"] is None

    def test_unknown_anomaly_is_404(self, seeded) -> None:
        r = client_for(READER).get(f"/api/v1/anomalies/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_detection_block_exposes_the_detector_reasoning(self, seeded) -> None:
        """The investigation page must state which thresholds were applied.
        Without them the verdict is an assertion the analyst cannot check."""
        body = client_for(READER).get(
            f"/api/v1/anomalies/{seeded['anomaly_id']}"
        ).json()
        det = body["detection"]
        assert det["expected_low"] == 1.7
        assert det["expected_high"] == 2.3
        assert det["threshold"] == 3.5
        assert det["baseline_sample_count"] == 30
        assert det["baseline_completeness"] == 0.9
        assert det["ratio"] == 3.0
        assert det["robust_score_status"] == "OK"
        assert det["robust_z"] == 13.5
        assert det["absolute_delta_events"] == 1200.0

    def test_detection_block_forwards_no_unlisted_key(self, seeded) -> None:
        """The evidence column is a detector-owned payload. Publishing it
        wholesale would make every future internal fact a public API field."""
        body = client_for(READER).get(
            f"/api/v1/anomalies/{seeded['anomaly_id']}"
        ).json()
        # `signal` is present in the stored dict and deliberately not projected.
        assert "signal" not in body["detection"]


class TestSourceBehavior:
    def test_observed_and_expected_are_reported_together(self, seeded) -> None:
        rows = client_for(READER).get("/api/v1/behavior/sources").json()
        row = next(r for r in rows if r["name"] == "LAB Firewall")
        assert row["observed_eps"] == 6.0
        assert row["expected_eps"] == 2.0
        assert row["expected_low"] == 1.7
        assert row["expected_high"] == 2.3
        assert row["deviation_ratio"] == 3.0
        assert row["open_anomaly_count"] == 1

    def test_metric_history_returns_buckets(self, seeded) -> None:
        sid = seeded["source_id"]
        since = _iso(NOW - timedelta(hours=1))
        until = _iso(NOW + timedelta(hours=1))
        rows = client_for(READER).get(
            f"/api/v1/behavior/sources/{sid}/metrics?since={since}&until={until}"
        ).json()
        assert len(rows) == 1
        assert rows[0]["average_eps"] == 6.0
        assert rows[0]["completeness"] == "COMPLETE"

    def test_baselines_expose_reliability(self, seeded) -> None:
        sid = seeded["source_id"]
        rows = client_for(READER).get(
            f"/api/v1/behavior/sources/{sid}/baselines"
        ).json()
        assert rows[0]["is_reliable"] is True
        assert rows[0]["sample_count"] == 30
        assert rows[0]["baseline_version"] == 2

    def test_unknown_source_is_404(self, seeded) -> None:
        r = client_for(READER).get(f"/api/v1/behavior/sources/{uuid.uuid4()}")
        assert r.status_code == 404


class TestSummary:
    def test_summary_counts_by_detector_type(self, seeded) -> None:
        body = client_for(READER).get("/api/v1/anomalies/summary").json()
        assert body["open_anomalies"] == 1
        assert body["spikes"] == 1
        assert body["drops"] == 0
        assert body["silent_sources"] == 0
        assert body["monitored_sources"] == 1

    def test_highest_deviation_is_ranked(self, seeded) -> None:
        body = client_for(READER).get("/api/v1/anomalies/summary").json()
        assert body["highest_deviation"]
        assert body["highest_deviation"][0]["deviation_ratio"] == 3.0
        # The overview links each row to its source, so the name travels with it.
        assert body["highest_deviation"][0]["log_source_name"] == "LAB Firewall"

    def test_evidence_backlog_and_failures_are_counted_apart(self, seeded) -> None:
        """A queued explanation job and a failed one both leave the
        investigation page empty; only the counts distinguish them."""
        body = client_for(READER).get("/api/v1/anomalies/summary").json()
        assert body["evidence_pending"] == 0
        assert body["evidence_failed"] == 0


class TestNoSecretsLeak:
    def test_responses_carry_no_provider_credentials(self, seeded) -> None:
        """Provenance is query structure, never credentials or headers."""
        body = client_for(READER).get(
            f"/api/v1/anomalies/{seeded['anomaly_id']}"
        ).text.lower()
        for forbidden in ("sec", "token", "authorization", "password", "secret"):
            # `sec` appears legitimately inside words like "seconds"; assert on
            # the header/credential spellings that would indicate a real leak.
            assert f'"{forbidden}"' not in body

    def test_no_mutation_verbs_are_exposed(self, db_schema) -> None:
        """QRadar integration is read-only by design, not by omission."""
        app = create_app()
        for route in app.routes:
            methods = getattr(route, "methods", set()) or set()
            path = getattr(route, "path", "")
            if "/anomalies" in path or "/behavior" in path:
                assert methods <= {"GET", "HEAD", "OPTIONS"}, path
