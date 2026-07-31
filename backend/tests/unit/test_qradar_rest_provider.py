"""Characterization tests for QRadarRestProvider.

Every test drives the provider through a mocked httpx transport, so the full
request/response path -- headers, retries, pagination, decoding, normalization
-- is exercised without a QRadar appliance. Retry sleeps are captured rather
than performed, and jitter uses a seeded Random, so timing assertions are exact.

The security assertions here are the point of the file: a token that leaks into
a log record or an exception message is a finding, not a style issue, so those
paths are asserted directly rather than inferred from code review.
"""

from __future__ import annotations

import logging
import random
import ssl
from datetime import UTC, datetime

import httpx
import pytest

from app.providers.base import (
    ProviderAuthError,
    ProviderCapability,
    ProviderError,
    ProviderUnavailableError,
)
from app.providers.qradar_rest import (
    MalformedUpstreamResponse,
    QRadarRestProvider,
    _sanitize_upstream_error,
)

SEC_TOKEN = "s3cr3t-qradar-token-do-not-leak"
BASE_URL = "https://qradar.example.internal"


class RecordingSleep:
    """Stands in for asyncio.sleep and records what it was asked to wait."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class TestLogSourceMetrics:
    @pytest.mark.asyncio
    async def test_uses_bounded_read_only_ariel_aggregate(self) -> None:
        captured_query = ""

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_query
            if request.method == "POST" and request.url.path.endswith("/ariel/searches"):
                captured_query = request.url.params["query_expression"]
                return httpx.Response(201, json={"search_id": "metric-1", "status": "WAIT"})
            if request.url.path.endswith("/ariel/searches/metric-1/results"):
                return httpx.Response(
                    200,
                    json={
                        "events": [
                            {
                                "qradar_id": "7",
                                "event_count": "300",
                                "last_event_time": 1_785_506_699_000,
                            }
                        ]
                    },
                )
            if request.url.path.endswith("/ariel/searches/metric-1"):
                return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
            raise AssertionError(f"unexpected request: {request.method} {request.url}")

        provider, _ = build_provider(handler, page_size=10, max_pages=2)
        start = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)
        end = datetime(2026, 7, 31, 10, 5, tzinfo=UTC)

        (sample,) = await provider.get_log_source_metrics(start, end)

        assert "FROM events" in captured_query
        assert "GROUP BY logsourceid" in captured_query
        assert str(int(start.timestamp() * 1000)) in captured_query
        assert str(int(end.timestamp() * 1000)) in captured_query
        assert sample.qradar_id == 7
        assert sample.event_count == 300
        assert sample.average_eps == pytest.approx(1.0)
        assert sample.bucket_seconds == 300
        assert sample.stored_event_count == 300

    @pytest.mark.asyncio
    async def test_rejects_unbounded_metric_window_before_io(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"unexpected request: {request.url}")

        provider, _ = build_provider(handler)
        start = datetime(2026, 7, 1, tzinfo=UTC)
        end = datetime(2026, 7, 31, tzinfo=UTC)

        with pytest.raises(ValueError, match="168-hour"):
            await provider.get_log_source_metrics(start, end)


def build_provider(
    handler,
    *,
    sleep: RecordingSleep | None = None,
    seed: int = 1337,
    **kwargs,
) -> tuple[QRadarRestProvider, RecordingSleep]:
    """A provider wired to `handler`, with the real header/timeout config kept.

    The client is constructed here rather than by the provider because a
    MockTransport has to be injected; the headers below mirror exactly what the
    provider sets for itself so header assertions stay meaningful.
    """
    recorder = sleep or RecordingSleep()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=BASE_URL + "/api",
        headers={
            "SEC": SEC_TOKEN,
            "Version": kwargs.pop("api_version", "20.0"),
            "Accept": "application/json",
        },
        timeout=httpx.Timeout(60.0, connect=10.0),
    )
    provider = QRadarRestProvider(
        base_url=BASE_URL,
        sec_token=SEC_TOKEN,
        client=client,
        rng=random.Random(seed),
        sleep=recorder,
        **kwargs,
    )
    return provider, recorder


# --------------------------------------------------------------- construction
class TestConstruction:
    def test_tls_verification_cannot_be_disabled(self) -> None:
        with pytest.raises(ValueError, match="verify_ssl=False"):
            QRadarRestProvider(base_url=BASE_URL, sec_token=SEC_TOKEN, verify_ssl=False)

    def test_plaintext_base_url_refused(self) -> None:
        with pytest.raises(ValueError, match="https://"):
            QRadarRestProvider(base_url="http://qradar.example.internal", sec_token=SEC_TOKEN)

    def test_auth_header_carries_token_and_version(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(200, json={"external_version": "7.5.0"})

        provider, _ = build_provider(handler, api_version="21.0")

        async def run():
            await provider.get_instance_info()

        import asyncio

        asyncio.run(run())
        assert seen["sec"] == SEC_TOKEN
        assert seen["version"] == "21.0"
        assert seen["accept"] == "application/json"

    def test_custom_ca_bundle_is_used_as_the_verify_source(self, tmp_path) -> None:
        """An internal CA path must reach httpx, not be silently dropped."""
        bundle = tmp_path / "internal-ca.pem"
        # A syntactically valid but untrusted self-signed cert is unnecessary
        # here: httpx only needs a loadable PEM to build the context.
        bundle.write_text(_SELF_SIGNED_PEM)
        provider = QRadarRestProvider(
            base_url=BASE_URL, sec_token=SEC_TOKEN, ca_bundle=str(bundle)
        )
        context = provider._client._transport._pool._ssl_context
        assert isinstance(context, ssl.SSLContext)
        # Verification stays on; the bundle widens trust, it does not remove it.
        assert context.verify_mode == ssl.CERT_REQUIRED

    def test_unreadable_ca_bundle_fails_loudly(self, tmp_path) -> None:
        """A typo'd CA path must not silently degrade to the system store."""
        missing = tmp_path / "nope.pem"
        with pytest.raises((OSError, ssl.SSLError)):
            QRadarRestProvider(
                base_url=BASE_URL, sec_token=SEC_TOKEN, ca_bundle=str(missing)
            )

    def test_capabilities_include_aql_and_exclude_nothing_unexpected(self) -> None:
        caps = QRadarRestProvider.capabilities
        assert ProviderCapability.AQL_EXECUTION in caps
        assert ProviderCapability.OFFENSES in caps
        assert ProviderCapability.INVENTORY in caps

    @pytest.mark.parametrize(
        "name",
        [
            "update_offense", "close_offense", "assign_offense", "add_offense_note",
            "create_rule", "update_rule", "delete_rule", "enable_rule", "disable_rule",
            "update_config", "set_config", "create_log_source", "delete_log_source",
        ],
    )
    def test_no_mutation_methods_are_exposed(self, name: str) -> None:
        """The provider is read-only apart from the Ariel search lifecycle.

        Ariel create/cancel are the only writes, and they act on our own search
        objects -- never on QRadar configuration, offenses or rules.
        """
        assert not hasattr(QRadarRestProvider, name)


# ------------------------------------------------------------ error sanitizing
class TestErrorSanitization:
    @pytest.mark.parametrize(
        ("status", "fragment"),
        [
            (401, "credentials"),
            (403, "credentials"),
            (404, "does not exist"),
            (409, "conflict"),
            (422, "invalid"),
            (429, "rate-limiting"),
            (500, "internal error"),
            (503, "internal error"),
            (418, "rejected the request"),
        ],
    )
    def test_message_built_from_status_alone(self, status: int, fragment: str) -> None:
        assert fragment in _sanitize_upstream_error(status)

    @pytest.mark.asyncio
    async def test_upstream_body_never_reaches_the_caller(self) -> None:
        """QRadar error bodies have been observed echoing request headers."""
        leaked = f"auth failed for SEC={SEC_TOKEN} at /api/siem/offenses"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text=leaked)

        provider, _ = build_provider(handler)
        with pytest.raises(ProviderAuthError) as exc:
            await provider.get_instance_info()

        message = str(exc.value)
        assert SEC_TOKEN not in message
        assert "/api/siem/offenses" not in message
        assert leaked not in message

    @pytest.mark.asyncio
    async def test_token_absent_from_log_records(self, caplog) -> None:
        """Nothing the provider logs may carry the SEC token."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"id": "not-an-int"}])

        provider, _ = build_provider(handler, max_pages=1)
        with caplog.at_level(logging.DEBUG):
            await provider.list_rules()

        rendered = "\n".join(
            r.getMessage() + repr(getattr(r, "__dict__", {})) for r in caplog.records
        )
        assert SEC_TOKEN not in rendered

    @pytest.mark.asyncio
    async def test_transport_error_message_hides_certificate_detail(self) -> None:
        """A TLS failure must not leak local certificate paths to an operator."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(
                "certificate verify failed: unable to get local issuer "
                "certificate (/etc/pki/internal-ca.pem)"
            )

        provider, _ = build_provider(handler, max_retries=0)
        with pytest.raises(ProviderUnavailableError) as exc:
            await provider.get_instance_info()
        assert str(exc.value) == "Could not reach QRadar"
        assert "/etc/pki" not in str(exc.value)


# --------------------------------------------------------------- retry policy
class TestRetries:
    @pytest.mark.asyncio
    async def test_401_is_not_retried(self) -> None:
        """Hammering auth is how service accounts get locked out."""
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(401)

        provider, sleeps = build_provider(handler, max_retries=3)
        with pytest.raises(ProviderAuthError):
            await provider.get_instance_info()
        assert calls == 1
        assert sleeps.delays == []

    @pytest.mark.asyncio
    async def test_403_is_not_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(403)

        provider, _ = build_provider(handler, max_retries=3)
        with pytest.raises(ProviderAuthError):
            await provider.get_instance_info()
        assert calls == 1

    @pytest.mark.asyncio
    async def test_404_without_allowance_raises_and_is_not_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(404)

        provider, _ = build_provider(handler, max_retries=3)
        with pytest.raises(ProviderError) as exc:
            await provider.get_instance_info()
        assert calls == 1
        assert "does not exist" in str(exc.value)

    @pytest.mark.asyncio
    async def test_404_with_allowance_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        provider, _ = build_provider(handler)
        assert await provider.get_offense(999) is None
        assert await provider.get_rule(999) is None
        assert await provider.get_log_source(999) is None

    @pytest.mark.asyncio
    async def test_422_is_not_retried(self) -> None:
        """A request QRadar considers invalid will stay invalid."""
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(422)

        provider, sleeps = build_provider(handler, max_retries=3)
        with pytest.raises(ProviderError):
            await provider.get_instance_info()
        assert calls == 1
        assert sleeps.delays == []

    @pytest.mark.asyncio
    async def test_500_is_retried_to_the_configured_ceiling(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500)

        provider, sleeps = build_provider(handler, max_retries=2)
        with pytest.raises(ProviderUnavailableError):
            await provider.get_instance_info()
        assert calls == 3  # initial + 2 retries
        assert len(sleeps.delays) == 2

    @pytest.mark.asyncio
    async def test_429_is_retried_then_succeeds(self) -> None:
        responses = [httpx.Response(429), httpx.Response(200, json={"version": "7.5"})]

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        provider, sleeps = build_provider(handler, max_retries=3)
        info = await provider.get_instance_info()
        assert info.version == "7.5"
        assert len(sleeps.delays) == 1

    @pytest.mark.asyncio
    async def test_retry_after_header_overrides_backoff(self) -> None:
        responses = [
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"version": "7.5"}),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        provider, sleeps = build_provider(handler, retry_base_seconds=2.0)
        await provider.get_instance_info()
        assert sleeps.delays == [7.0]

    @pytest.mark.asyncio
    async def test_retry_after_is_clamped_to_the_maximum(self) -> None:
        """A hostile header must not park a worker for hours."""
        responses = [
            httpx.Response(503, headers={"Retry-After": "86400"}),
            httpx.Response(200, json={"version": "7.5"}),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        provider, sleeps = build_provider(handler, retry_max_seconds=30.0)
        await provider.get_instance_info()
        assert sleeps.delays == [30.0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("raw", ["Wed, 21 Oct 2026 07:28:00 GMT", "-5", "soon", ""])
    async def test_unusable_retry_after_falls_back_to_backoff(self, raw: str) -> None:
        """HTTP-date and junk values fall back rather than crashing the retry."""
        responses = [
            httpx.Response(429, headers={"Retry-After": raw} if raw else {}),
            httpx.Response(200, json={"version": "7.5"}),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        provider, sleeps = build_provider(handler, retry_base_seconds=2.0, seed=99)
        await provider.get_instance_info()
        assert len(sleeps.delays) == 1
        # Full jitter: uniform(0, base * 2**0) on the first retry.
        assert 0.0 <= sleeps.delays[0] <= 2.0

    @pytest.mark.asyncio
    async def test_backoff_grows_exponentially_and_is_jittered(self) -> None:
        """Delays stay inside the full-jitter envelope and are seed-reproducible."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        provider, sleeps = build_provider(
            handler, max_retries=4, retry_base_seconds=1.0, retry_max_seconds=60.0, seed=7
        )
        with pytest.raises(ProviderUnavailableError):
            await provider.get_instance_info()

        assert len(sleeps.delays) == 4
        for attempt, delay in enumerate(sleeps.delays, start=1):
            ceiling = min(60.0, 1.0 * 2 ** (attempt - 1))
            assert 0.0 <= delay <= ceiling

        # Same seed, same sequence -- jitter is deterministic under injection.
        provider2, sleeps2 = build_provider(
            handler, max_retries=4, retry_base_seconds=1.0, retry_max_seconds=60.0, seed=7
        )
        with pytest.raises(ProviderUnavailableError):
            await provider2.get_instance_info()
        assert sleeps.delays == sleeps2.delays

    @pytest.mark.asyncio
    async def test_timeouts_are_retried_and_surface_as_unavailable(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("read timed out")

        provider, sleeps = build_provider(handler, max_retries=2)
        with pytest.raises(ProviderUnavailableError, match="timed out"):
            await provider.get_instance_info()
        assert calls == 3
        assert len(sleeps.delays) == 2

    @pytest.mark.asyncio
    async def test_connect_timeout_surfaces_as_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("connect timed out")

        provider, _ = build_provider(handler, max_retries=0)
        with pytest.raises(ProviderUnavailableError, match="timed out"):
            await provider.get_instance_info()

    @pytest.mark.asyncio
    async def test_ariel_create_is_never_retried(self) -> None:
        """A replayed POST would strand an untracked search on the appliance."""
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500)

        provider, sleeps = build_provider(handler, max_retries=5)
        with pytest.raises(ProviderUnavailableError):
            await provider.create_ariel_search("SELECT * FROM events")
        assert calls == 1
        assert sleeps.delays == []

    def test_connect_and_read_timeouts_are_configured_separately(self) -> None:
        provider = QRadarRestProvider(
            base_url=BASE_URL, sec_token=SEC_TOKEN, timeout=45.0, connect_timeout=3.0
        )
        timeout = provider._client.timeout
        assert timeout.connect == 3.0
        assert timeout.read == 45.0
        # A stalled pool checkout must be bounded too.
        assert timeout.pool == 45.0


# ----------------------------------------------------------------- pagination
class TestPagination:
    @pytest.mark.asyncio
    async def test_range_headers_advance_by_page_size(self) -> None:
        ranges: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            ranges.append(request.headers["Range"])
            page = len(ranges)
            if page < 3:
                return httpx.Response(
                    200, json=[{"id": i, "name": f"r{i}"} for i in range(2)]
                )
            return httpx.Response(200, json=[{"id": 99, "name": "last"}])

        provider, _ = build_provider(handler, page_size=2)
        rules = await provider.list_rules()
        assert ranges == ["items=0-1", "items=2-3", "items=4-5"]
        # A short page ends the walk; 2 + 2 + 1 rows collected.
        assert len(rules) == 5

    @pytest.mark.asyncio
    async def test_empty_page_terminates_the_walk(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=[])

        provider, _ = build_provider(handler, page_size=50, max_pages=10)
        assert await provider.list_rules() == []
        assert calls == 1

    @pytest.mark.asyncio
    async def test_max_pages_bounds_a_hostile_upstream(self, caplog) -> None:
        """A server that always returns a full page must not loop forever."""
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=[{"id": calls, "name": f"r{calls}"}])

        provider, _ = build_provider(handler, page_size=1, max_pages=4)
        with caplog.at_level(logging.WARNING):
            rules = await provider.list_rules()

        assert calls == 4
        assert len(rules) == 4
        assert any("page ceiling" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_per_call_max_pages_overrides_the_default(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=[{"id": calls}])

        provider, _ = build_provider(handler, page_size=1, max_pages=100)
        await provider.list_offenses(open_only=False, max_pages=2)
        assert calls == 2

    @pytest.mark.asyncio
    async def test_non_list_collection_body_is_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "object"})

        provider, _ = build_provider(handler)
        with pytest.raises(MalformedUpstreamResponse, match="non-list"):
            await provider.list_rules()

    @pytest.mark.asyncio
    async def test_non_object_entries_are_dropped_not_fatal(self) -> None:
        """One junk element must not discard the whole page."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=["a string", 42, None, {"id": 5, "name": "real rule"}],
            )

        provider, _ = build_provider(handler, page_size=10)
        rules = await provider.list_rules()
        assert [r.qradar_id for r in rules] == [5]


# ------------------------------------------------------------------- decoding
class TestDecoding:
    @pytest.mark.asyncio
    async def test_malformed_json_raises_a_typed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"{not json at all")

        provider, _ = build_provider(handler)
        with pytest.raises(MalformedUpstreamResponse, match="not valid JSON"):
            await provider.get_instance_info()

    @pytest.mark.asyncio
    async def test_empty_body_decodes_to_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"")

        provider, _ = build_provider(handler)
        with pytest.raises(MalformedUpstreamResponse):
            # /system/about returning nothing is not a usable instance info.
            await provider.get_instance_info()

    @pytest.mark.asyncio
    async def test_unexpected_shape_on_detail_endpoint_is_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["a", "list", "not", "an", "object"])

        provider, _ = build_provider(handler)
        with pytest.raises(MalformedUpstreamResponse, match="not an object"):
            await provider.get_offense(1)


# -------------------------------------------------------------- normalization
class TestNormalization:
    @pytest.mark.asyncio
    async def test_offense_timestamps_are_utc_from_epoch_millis(self) -> None:
        # 2026-07-15T10:00:00Z
        start_ms = 1784109600000

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 42,
                        "status": "OPEN",
                        "magnitude": 7,
                        "start_time": start_ms,
                        "last_persisted_time": start_ms + 60000,
                        # Zero means "still open" in QRadar, not 1970.
                        "close_time": 0,
                    }
                ],
            )

        provider, _ = build_provider(handler, page_size=10)
        (offense,) = await provider.list_offenses(open_only=False)

        assert offense.start_time == datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
        assert offense.start_time.tzinfo is UTC
        assert offense.last_updated_time == datetime(2026, 7, 15, 10, 1, tzinfo=UTC)
        assert offense.close_time is None

    @pytest.mark.asyncio
    async def test_offense_without_id_is_dropped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=[{"status": "OPEN"}, {"id": 7, "status": "OPEN"}]
            )

        provider, _ = build_provider(handler, page_size=10)
        offenses = await provider.list_offenses(open_only=False)
        assert [o.qradar_id for o in offenses] == [7]

    @pytest.mark.asyncio
    async def test_offense_type_accepts_bare_id_or_expanded_object(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "offense_type": 3},
                    {"id": 2, "offense_type": {"id": 4, "name": "Source IP"}},
                ],
            )

        provider, _ = build_provider(handler, page_size=10)
        bare, expanded = await provider.list_offenses(open_only=False)
        assert (bare.offense_type, bare.offense_type_name) == (3, None)
        assert (expanded.offense_type, expanded.offense_type_name) == (4, "Source IP")

    @pytest.mark.asyncio
    async def test_closing_reason_accepts_id_object_or_string(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "closing_reason_id": 9},
                    {"id": 2, "closing_reason": {"id": 10, "text": "False Positive"}},
                    {"id": 3, "closing_reason": "Policy Violation"},
                ],
            )

        provider, _ = build_provider(handler, page_size=10)
        a, b, c = await provider.list_offenses(open_only=False)
        assert a.closing_reason_id == 9
        assert (b.closing_reason_id, b.closing_reason) == (10, "False Positive")
        assert c.closing_reason == "Policy Violation"

    @pytest.mark.asyncio
    async def test_updated_since_becomes_an_epoch_millisecond_filter(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json=[])

        provider, _ = build_provider(handler)
        since = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
        await provider.list_offenses(open_only=True, updated_since=since)

        assert "last_persisted_time%3E1784109600000" in captured["url"]
        assert "status%3D%22OPEN%22" in captured["url"]

    @pytest.mark.asyncio
    async def test_rule_without_name_or_id_is_dropped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"id": 1},                       # no name
                    {"name": "nameless id"},         # no id
                    {"id": 2, "name": "   "},        # blank name
                    {"id": 3, "name": "Good Rule"},
                ],
            )

        provider, _ = build_provider(handler, page_size=10)
        rules = await provider.list_rules()
        assert [r.qradar_id for r in rules] == [3]

    @pytest.mark.asyncio
    async def test_building_block_flag_derived_from_rule_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "name": "BB: Suspicious", "type": "BUILDINGBLOCK"},
                    {"id": 2, "name": "Real Rule", "type": "CUSTOM"},
                ],
            )

        provider, _ = build_provider(handler, page_size=10)
        bb, rule = await provider.list_rules()
        assert bb.is_building_block is True
        assert rule.is_building_block is False

    @pytest.mark.asyncio
    async def test_building_block_endpoint_forces_the_flag(self) -> None:
        """/analytics/building_blocks rows may omit the type field entirely."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"id": 5, "name": "BB: Admin Assets"}])

        provider, _ = build_provider(handler, page_size=10)
        (bb,) = await provider.list_building_blocks()
        assert bb.is_building_block is True

    @pytest.mark.asyncio
    async def test_generates_offense_is_none_when_undeterminable(self) -> None:
        """Absent rule_responses means unknown, which is not the same as False."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "name": "No responses key"},
                    {"id": 2, "name": "Empty responses", "rule_responses": []},
                    {
                        "id": 3,
                        "name": "Creates offense",
                        "rule_responses": ["NEW_OFFENSE", "EMAIL"],
                    },
                    {"id": 4, "name": "Email only", "rule_responses": ["EMAIL"]},
                ],
            )

        provider, _ = build_provider(handler, page_size=10)
        unknown, empty, offense, email = await provider.list_rules()
        assert unknown.generates_offense is None
        assert empty.generates_offense is False
        assert offense.generates_offense is True
        assert email.generates_offense is False

    @pytest.mark.asyncio
    async def test_mitre_techniques_are_never_inferred_from_the_payload(self) -> None:
        """MITRE mapping is SOC-owned; a QRadar field must not seed it."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[{"id": 1, "name": "R", "mitre_techniques": ["T1059", "T1078"]}],
            )

        provider, _ = build_provider(handler, page_size=10)
        (rule,) = await provider.list_rules()
        assert rule.mitre_techniques == []

    @pytest.mark.asyncio
    async def test_log_source_status_accepts_object_or_scalar(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "name": "a", "status": {"status": "SUCCESS"}},
                    {"id": 2, "name": "b", "status": "ERROR"},
                    {"id": 3, "name": "c"},
                ],
            )

        provider, _ = build_provider(handler, page_size=10)
        a, b, c = await provider.list_log_sources()
        assert (a.status, b.status, c.status) == ("SUCCESS", "ERROR", None)

    @pytest.mark.asyncio
    async def test_log_source_type_is_read_from_either_shape(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "name": "a", "type_id": 12},
                    {"id": 2, "name": "b", "type": {"id": 13, "name": "Linux OS"}},
                ],
            )

        provider, _ = build_provider(handler, page_size=10)
        a, b = await provider.list_log_sources()
        assert a.type_id == 12
        assert (b.type_id, b.type_name) == (13, "Linux OS")

    @pytest.mark.asyncio
    async def test_offense_and_closing_reason_lookups_skip_malformed_rows(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "offense_types" in str(request.url):
                return httpx.Response(
                    200,
                    json=[
                        {"id": 1, "name": "Source IP"},
                        {"id": "bad"},                 # unusable id
                        {"name": "no id"},
                        {"id": 2, "name": 12345},      # non-string name
                    ],
                )
            return httpx.Response(
                200,
                json=[{"id": 3, "text": "False Positive"}, {"id": 4}],
            )

        provider, _ = build_provider(handler, page_size=10)
        assert await provider.list_offense_types() == {1: "Source IP"}
        assert await provider.list_offense_closing_reasons() == {3: "False Positive"}


# --------------------------------------------------------------------- Ariel
class TestArielLifecycle:
    @pytest.mark.asyncio
    async def test_create_returns_a_handle(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            return httpx.Response(
                201, json={"search_id": "abc-123", "status": "WAIT", "progress": 0}
            )

        provider, _ = build_provider(handler)
        handle = await provider.create_ariel_search("SELECT sourceip FROM events")

        assert captured["method"] == "POST"
        assert "/ariel/searches" in captured["url"]
        assert handle.search_id == "abc-123"
        assert handle.status == "WAIT"

    @pytest.mark.asyncio
    async def test_create_accepts_cursor_id_alias(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={"cursor_id": "cur-9", "status": "EXECUTE"})

        provider, _ = build_provider(handler)
        handle = await provider.create_ariel_search("SELECT * FROM events")
        assert handle.search_id == "cur-9"

    @pytest.mark.asyncio
    async def test_create_without_a_search_id_is_malformed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={"status": "WAIT"})

        provider, _ = build_provider(handler)
        with pytest.raises(MalformedUpstreamResponse, match="without a search id"):
            await provider.create_ariel_search("SELECT * FROM events")

    @pytest.mark.asyncio
    async def test_polling_reports_progress_then_completion(self) -> None:
        states = [
            {"status": "EXECUTE", "progress": 10},
            {"status": "SORTING", "progress": 80},
            {"status": "COMPLETED", "progress": 100, "record_count": 3},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=states.pop(0))

        provider, _ = build_provider(handler)
        first = await provider.get_ariel_search_status("s1")
        second = await provider.get_ariel_search_status("s1")
        third = await provider.get_ariel_search_status("s1")

        assert (first.status, first.progress) == ("EXECUTE", 10)
        assert (second.status, second.progress) == ("SORTING", 80)
        assert (third.status, third.record_count) == ("COMPLETED", 3)

    @pytest.mark.asyncio
    async def test_terminal_failure_does_not_forward_upstream_error_text(self) -> None:
        """QRadar's own error strings are untrusted and stay out of the DTO."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "ERROR",
                    "progress": 0,
                    "error_messages": [
                        {"message": f"parse failed near SEC={SEC_TOKEN}"}
                    ],
                },
            )

        provider, _ = build_provider(handler)
        status = await provider.get_ariel_search_status("s1")
        assert status.status == "ERROR"
        assert status.error_messages == ["QRadar reported the search failed"]
        assert SEC_TOKEN not in " ".join(status.error_messages)

    @pytest.mark.asyncio
    async def test_missing_status_field_is_treated_as_error(self) -> None:
        """Fail closed: an unreadable status must not look like success."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"progress": 50})

        provider, _ = build_provider(handler)
        assert (await provider.get_ariel_search_status("s1")).status == "ERROR"

    @pytest.mark.asyncio
    async def test_results_are_bounded_by_max_rows(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["range"] = request.headers["Range"]
            return httpx.Response(
                200,
                json={
                    "events": [
                        {"sourceip": f"10.0.0.{i}", "qid": i} for i in range(10)
                    ]
                },
            )

        provider, _ = build_provider(handler)
        results = await provider.get_ariel_search_results("s1", max_rows=4)

        assert captured["range"] == "items=0-3"
        assert len(results.rows) == 4
        assert results.truncated is True
        assert results.columns == ["qid", "sourceip"]

    @pytest.mark.asyncio
    async def test_results_are_not_marked_truncated_when_they_fit(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"flows": [{"a": 1}, {"a": 2}]})

        provider, _ = build_provider(handler)
        results = await provider.get_ariel_search_results("s1", max_rows=10)
        assert len(results.rows) == 2
        assert results.truncated is False

    @pytest.mark.asyncio
    async def test_results_with_no_row_array_yield_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"metadata": {"count": 0}})

        provider, _ = build_provider(handler)
        results = await provider.get_ariel_search_results("s1", max_rows=10)
        assert results.rows == []
        assert results.columns == []

    @pytest.mark.asyncio
    async def test_cancel_issues_delete_and_tolerates_a_missing_search(self) -> None:
        captured: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append((request.method, str(request.url)))
            return httpx.Response(404)

        provider, _ = build_provider(handler)
        # Already-gone is success: cancellation is idempotent.
        await provider.cancel_ariel_search("s1")
        assert captured[0][0] == "DELETE"
        assert "/ariel/searches/s1" in captured[0][1]

    @pytest.mark.asyncio
    async def test_cancel_is_retried_because_delete_is_idempotent(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500)

        provider, _ = build_provider(handler, max_retries=2)
        with pytest.raises(ProviderUnavailableError):
            await provider.cancel_ariel_search("s1")
        assert calls == 3


# A throwaway self-signed certificate, used only to prove a CA bundle path is
# loaded into the SSL context. It is not a secret and grants no access.
_SELF_SIGNED_PEM = """-----BEGIN CERTIFICATE-----
MIIBhTCCASugAwIBAgIQIRi6zePL6mKjOipn+dNuaTAKBggqhkjOPQQDAjASMRAw
DgYDVQQKEwdBY21lIENvMB4XDTE3MTAyMDE5NDMwNloXDTE4MTAyMDE5NDMwNlow
EjEQMA4GA1UEChMHQWNtZSBDbzBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABD0d
7VNhbWvZLWPuj/RtHFjvtJBEwOkhbN/BnnE8rnZR8+sbwnc/KhCk3FhnpHZnQz7B
5aETbbIgmuvewdjvSBSjYzBhMA4GA1UdDwEB/wQEAwICpDATBgNVHSUEDDAKBggr
BgEFBQcDATAPBgNVHRMBAf8EBTADAQH/MCkGA1UdEQQiMCCCDmxvY2FsaG9zdDo1
NDUzgg4xMjcuMC4wLjE6NTQ1MzAKBggqhkjOPQQDAgNIADBFAiEA2zpJEPQyz6/l
Wf86aX6PepsntZv2GYlA5UpabfT2EZICICpJ5h/iI+i341gBmLiAFQOyTDT+/wQc
6MF9+Yw1Yy0t
-----END CERTIFICATE-----
"""
