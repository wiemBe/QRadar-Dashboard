"""QRadarRestProvider — direct QRadar REST + Ariel APIs.

Owns deterministic background collection and the full scheduled-AQL lifecycle.
AQL execution lives here and only here: the MCP provider deliberately lacks the
capability (see docs/mcp-capability-matrix.md section 1.2).

Security posture baked in here:
  * TLS verification is always on. `verify_ssl=False` is refused rather than
    honoured — a privileged SIEM token must never cross an unverified channel.
    An internal CA bundle is the supported way to trust a private PKI.
  * The SEC token is attached per-request from the encrypted store. It is never
    logged, never echoed into an exception message, and never reaches a response
    body: `_sanitize_upstream_error` rebuilds error text from a fixed vocabulary
    rather than forwarding whatever QRadar said.
  * Retries are bounded, jittered, and applied only to idempotent verbs. The one
    POST we issue (Ariel search creation) is never retried after the request has
    been transmitted, because a retried create would leave an orphaned search
    consuming appliance capacity.
"""

from __future__ import annotations

import asyncio
import logging
import random
import ssl
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from app.providers.base import (
    ProviderAuthError,
    ProviderCapability,
    ProviderError,
    ProviderUnavailableError,
    QRadarProvider,
)
from app.providers.dto import (
    AnalyticsRuleDTO,
    ArielSearchHandleDTO,
    ArielSearchResultsDTO,
    ArielSearchStatusDTO,
    DimensionAggregate,
    DimensionValueCount,
    InstanceInfoDTO,
    LogSourceDTO,
    LogSourceMetricSample,
    LogSourceTypeDTO,
    OffenseDTO,
)
from app.providers.normalize import (
    as_int,
    as_int_list,
    as_str_list,
    as_text,
    parse_qradar_time,
)
from app.providers.telemetry import EndpointCategory, ErrorClass, observe
from app.providers.tls import build_ssl_context
from app.services.backoff import backoff_delay

logger = logging.getLogger("app.providers.qradar_rest")

PROVIDER_NAME = "rest"

#: Verbs safe to replay. POST is absent by design.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "DELETE"})

#: QRadar caps a Range request; 500 keeps responses small enough to parse
#: without holding a whole inventory in memory at once.
DEFAULT_PAGE_SIZE = 500

#: Hard ceiling on pages walked in one call, so a pathological or hostile
#: upstream cannot turn a list call into an unbounded loop.
DEFAULT_MAX_PAGES = 200

class MalformedUpstreamResponse(ProviderError):
    """QRadar returned something we could not safely interpret."""


def _sanitize_upstream_error(status_code: int) -> str:
    """Build operator-facing error text from the status code alone.

    Upstream error bodies are untrusted and have been observed to echo request
    headers. Rather than filter them, we never forward them: the status code
    plus a fixed phrase is all a caller gets.
    """
    if status_code in (401, 403):
        return "QRadar rejected the credentials or the token lacks the required capability"
    if status_code == 404:
        return "QRadar reported the requested object does not exist"
    if status_code == 409:
        return "QRadar reported a conflict with the current state"
    if status_code == 422:
        return "QRadar rejected the request as invalid"
    if status_code == 429:
        return "QRadar is rate-limiting requests"
    if status_code >= 500:
        return "QRadar returned an internal error"
    return "QRadar rejected the request"


class QRadarRestProvider(QRadarProvider):
    capabilities = frozenset(
        {
            ProviderCapability.INVENTORY,
            ProviderCapability.OFFENSES,
            ProviderCapability.AQL_EXECUTION,
            ProviderCapability.CONFIG_SNAPSHOTS,
        }
    )

    def __init__(
        self,
        *,
        base_url: str,
        sec_token: str,
        api_version: str = "20.0",
        verify_ssl: bool = True,
        ca_bundle: str | None = None,
        tls_allow_missing_aki: bool = False,
        timeout: float = 60.0,
        connect_timeout: float = 10.0,
        max_retries: int = 3,
        retry_base_seconds: float = 2.0,
        retry_max_seconds: float = 30.0,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
        client: httpx.AsyncClient | None = None,
        rng: random.Random | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not verify_ssl:
            # Hard failure, not a warning. See config._production_hardening.
            raise ValueError(
                "QRadarRestProvider refuses verify_ssl=False; TLS verification is mandatory."
            )
        if not base_url.startswith("https://"):
            raise ValueError("QRadar base_url must be https://")

        self._max_retries = max(0, max_retries)
        self._retry_base = retry_base_seconds
        self._retry_max = retry_max_seconds
        self._page_size = max(1, page_size)
        self._max_pages = max(1, max_pages)
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not cryptographic
        self._sleep = sleep or asyncio.sleep

        if client is not None:
            self._client = client
            return

        # `ca_bundle` points at an internal CA so a private PKI is trusted
        # without ever disabling verification. Built as an explicit SSLContext
        # rather than passed as a path string, which httpx deprecates.
        verify: ssl.SSLContext = build_ssl_context(
            ca_bundle, allow_missing_aki=tls_allow_missing_aki
        )
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api",
            headers={
                "SEC": sec_token,
                "Version": api_version,
                "Accept": "application/json",
            },
            verify=verify,
            # Bounded on every phase: a stalled read must not pin a worker.
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- transport
    async def _request(
        self,
        method: str,
        path: str,
        *,
        category: EndpointCategory,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_404: bool = False,
    ) -> Any:
        """One upstream call with bounded, jittered retries.

        Retries cover transport failures, 429 and 5xx. Non-idempotent methods
        are attempted exactly once: replaying a POST could create a second Ariel
        search we would then never track or cancel.
        """
        retryable = method.upper() in _IDEMPOTENT_METHODS
        attempts = self._max_retries + 1 if retryable else 1
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            with observe(PROVIDER_NAME, category) as outcome:
                try:
                    response = await self._client.request(
                        method, path, params=params, headers=headers
                    )
                except httpx.TimeoutException as exc:
                    outcome["error_class"] = ErrorClass.TIMEOUT
                    last_exc = ProviderUnavailableError("QRadar request timed out")
                    last_exc.__cause__ = exc
                except httpx.TransportError as exc:
                    # Covers TLS verification failures. The message is ours, not
                    # the library's, so a certificate path never reaches a caller.
                    outcome["error_class"] = ErrorClass.NETWORK
                    last_exc = ProviderUnavailableError("Could not reach QRadar")
                    last_exc.__cause__ = exc
                else:
                    status = response.status_code

                    if status in (401, 403):
                        outcome["error_class"] = ErrorClass.AUTH
                        # Not retryable: a bad token stays bad, and hammering an
                        # auth endpoint is how accounts get locked out.
                        raise ProviderAuthError(_sanitize_upstream_error(status))

                    if status == 404 and allow_404:
                        return None

                    if status == 429 or status >= 500:
                        outcome["error_class"] = (
                            ErrorClass.RATE_LIMITED if status == 429 else ErrorClass.UPSTREAM_ERROR
                        )
                        last_exc = ProviderUnavailableError(_sanitize_upstream_error(status))
                        retry_after = self._retry_after_seconds(response)
                        if attempt < attempts:
                            await self._sleep(
                                retry_after
                                if retry_after is not None
                                else self._backoff(attempt)
                            )
                            continue
                        raise last_exc

                    if status >= 400:
                        outcome["error_class"] = ErrorClass.UPSTREAM_ERROR
                        raise ProviderError(_sanitize_upstream_error(status))

                    return self._decode(response, outcome)

            if attempt < attempts:
                await self._sleep(self._backoff(attempt))

        raise last_exc or ProviderUnavailableError("QRadar request failed")

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        """Honour QRadar's own throttling hint when it sends one.

        Clamped to the configured maximum so a hostile or misconfigured header
        cannot park a worker for hours.
        """
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            seconds = float(raw)
        except ValueError:
            return None
        if seconds < 0:
            return None
        return min(seconds, self._retry_max)

    def _backoff(self, attempt: int) -> float:
        return backoff_delay(
            attempt,
            base_seconds=self._retry_base,
            max_seconds=self._retry_max,
            rng=self._rng,
        )

    def _decode(self, response: httpx.Response, outcome: dict) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            outcome["error_class"] = ErrorClass.MALFORMED
            raise MalformedUpstreamResponse(
                "QRadar returned a response that was not valid JSON"
            ) from exc

    async def _paginate(
        self,
        path: str,
        *,
        category: EndpointCategory,
        params: dict[str, Any] | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
    ) -> list[dict]:
        """Walk QRadar's `Range: items=start-end` pagination.

        Bounded twice over: `max_pages` caps the walk, and a short page ends it.
        QRadar returns a `Content-Range` we do not rely on, because it is absent
        on some endpoints and versions.
        """
        size = page_size or self._page_size
        pages = max_pages or self._max_pages
        collected: list[dict] = []

        for page in range(pages):
            start = page * size
            end = start + size - 1
            body = await self._request(
                "GET",
                path,
                category=category,
                params=params,
                headers={"Range": f"items={start}-{end}"},
            )
            if body is None:
                break
            if not isinstance(body, list):
                raise MalformedUpstreamResponse(
                    f"QRadar returned a non-list body for a collection endpoint ({category})"
                )
            # Drop non-object entries rather than failing the whole page.
            collected.extend(item for item in body if isinstance(item, dict))
            if len(body) < size:
                break
        else:
            logger.warning(
                "pagination stopped at the page ceiling; results may be incomplete",
                extra={"endpoint_category": str(category), "max_pages": pages},
            )
        return collected

    # ------------------------------------------------- health / inventory
    async def validate_connection(self) -> InstanceInfoDTO:
        """Cheapest authenticated call that proves credentials and TLS work."""
        return await self.get_instance_info()

    async def get_instance_info(self) -> InstanceInfoDTO:
        body = await self._request("GET", "/system/about", category=EndpointCategory.SYSTEM)
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar /system/about did not return an object")
        version = body.get("external_version") or body.get("version")
        return InstanceInfoDTO(
            version=str(version) if version is not None else None,
            build=str(body.get("build_version")) if body.get("build_version") else None,
            reachable=True,
            # Small, non-sensitive descriptor block only; not the full payload.
            raw_about={
                k: v
                for k, v in body.items()
                if k in {"release_name", "external_version", "build_version"}
            },
        )

    async def list_log_sources(self) -> list[LogSourceDTO]:
        rows = await self._paginate(
            "/config/event_sources/log_source_management/log_sources",
            category=EndpointCategory.LOG_SOURCE_LIST,
        )
        return [dto for dto in (self._to_log_source(r) for r in rows) if dto is not None]

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        body = await self._request(
            "GET",
            f"/config/event_sources/log_source_management/log_sources/{int(qradar_id)}",
            category=EndpointCategory.LOG_SOURCE_DETAIL,
            allow_404=True,
        )
        if body is None:
            return None
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar log-source detail was not an object")
        return self._to_log_source(body)

    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:
        rows = await self._paginate(
            "/config/event_sources/log_source_management/log_source_types",
            category=EndpointCategory.LOG_SOURCE_TYPE_LIST,
        )
        out: list[LogSourceTypeDTO] = []
        for row in rows:
            type_id = as_int(row.get("id"))
            name = row.get("name")
            if type_id is None or not isinstance(name, str):
                continue
            out.append(LogSourceTypeDTO(type_id=type_id, name=name[:255]))
        return out

    async def get_log_source_metrics(
        self, window_start: datetime, window_end: datetime
    ) -> list[LogSourceMetricSample]:
        """Collect bounded per-log-source event volume through Ariel.

        QRadar exposes no REST metric endpoint. This is a read-only aggregate
        over ``events`` and stores no raw event or payload data. Search creation
        is attempted once, polling is bounded, and results are capped by the
        provider's pagination ceiling.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start = window_start if window_start.tzinfo else window_start.replace(tzinfo=UTC)
        end = window_end if window_end.tzinfo else window_end.replace(tzinfo=UTC)
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        seconds = int((end - start).total_seconds())
        if seconds <= 0:
            raise ValueError("metric window end must be after its start")
        if seconds > 168 * 3600:
            raise ValueError("metric window exceeds the 168-hour safety bound")

        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        aql = (
            'SELECT logsourceid AS "qradar_id", COUNT(*) AS "event_count", '  # noqa: S608 - integer epochs only
            'MAX(starttime) AS "last_event_time" FROM events '
            f"WHERE starttime >= {start_ms} AND starttime < {end_ms} "
            "GROUP BY logsourceid"
        )
        handle = await self.create_ariel_search(aql)
        status: ArielSearchStatusDTO | None = None
        # Two minutes maximum. The task's interval is at least one minute, and
        # a stuck appliance must never pin a worker indefinitely.
        for _ in range(120):
            status = await self.get_ariel_search_status(handle.search_id)
            if status.is_terminal:
                break
            await self._sleep(1.0)
        else:
            await self.cancel_ariel_search(handle.search_id)
            raise ProviderUnavailableError("QRadar Ariel metric search timed out")

        if status is None or not status.is_success:
            raise ProviderError("QRadar Ariel metric search did not complete")

        max_rows = self._page_size * self._max_pages
        results = await self.get_ariel_search_results(
            handle.search_id, max_rows=max_rows
        )
        samples: list[LogSourceMetricSample] = []
        for row in results.rows:
            qradar_id = as_int(row.get("qradar_id"))
            count = as_int(row.get("event_count"))
            if qradar_id is None or count is None or count < 0:
                continue
            last_event = parse_qradar_time(row.get("last_event_time"))
            delay = (
                max(0.0, (end - last_event).total_seconds())
                if last_event is not None
                else None
            )
            samples.append(
                LogSourceMetricSample(
                    qradar_id=qradar_id,
                    bucket_start=start,
                    bucket_seconds=seconds,
                    event_count=count,
                    average_eps=count / seconds,
                    last_event_at=last_event,
                    event_delay_seconds=delay,
                    stored_event_count=count,
                )
            )
        return samples

    # -------------------------------------------------- explanation evidence
    #: Explanation dimension -> the Ariel field that carries it, plus an
    #: optional companion field holding a human-readable label. Only fields
    #: QRadar normalizes across DSMs are listed; anything absent from a source's
    #: parser is reported UNAVAILABLE rather than guessed at.
    _DIMENSION_FIELDS: ClassVar[dict[str, tuple[str, str | None]]] = {
        "qid": ("qid", "QIDNAME(qid)"),
        "event_name": ("QIDNAME(qid)", None),
        "source_ip": ("sourceip", None),
        "destination_ip": ("destinationip", None),
        "destination_port": ("destinationport", None),
        "source_port": ("sourceport", None),
        "username": ("username", None),
        "action": ("eventdirection", None),
        "category": ("category", "CATEGORYNAME(category)"),
        "protocol": ("protocolid", "PROTOCOLNAME(protocolid)"),
    }

    async def get_dimension_aggregates(
        self,
        *,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        dimensions: Sequence[str],
        top_n: int,
    ) -> list[DimensionAggregate]:
        """One bounded aggregate Ariel query per requested dimension.

        Each query is `GROUP BY <field> ORDER BY count DESC LIMIT top_n` over a
        single log source and a bounded window. No raw events are retrieved and
        nothing is stored beyond the aggregate counts.

        A failure on one dimension is recorded on that dimension and does not
        abort the others: a partial explanation is useful, and losing the whole
        package because one DSM lacks `username` would be a poor trade.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        start, end = self._bounded_window(window_start, window_end)
        limit = max(1, min(top_n, self._page_size))

        out: list[DimensionAggregate] = []
        for dimension in dimensions:
            mapping = self._DIMENSION_FIELDS.get(dimension)
            if mapping is None:
                out.append(
                    DimensionAggregate(
                        dimension=dimension,
                        available=False,
                        error="dimension is not mapped to a QRadar field",
                    )
                )
                continue
            out.append(
                await self._aggregate_one_dimension(
                    dimension=dimension,
                    field=mapping[0],
                    label_field=mapping[1],
                    qradar_log_source_id=qradar_log_source_id,
                    start=start,
                    end=end,
                    limit=limit,
                )
            )
        return out

    def _bounded_window(
        self, window_start: datetime, window_end: datetime
    ) -> tuple[datetime, datetime]:
        start = window_start if window_start.tzinfo else window_start.replace(tzinfo=UTC)
        end = window_end if window_end.tzinfo else window_end.replace(tzinfo=UTC)
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if end <= start:
            raise ValueError("explanation window end must be after its start")
        if (end - start).total_seconds() > 168 * 3600:
            raise ValueError("explanation window exceeds the 168-hour safety bound")
        return start, end

    async def _aggregate_one_dimension(
        self,
        *,
        dimension: str,
        field: str,
        label_field: str | None,
        qradar_log_source_id: int,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> DimensionAggregate:
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        label_select = f', {label_field} AS "label"' if label_field else ""
        # Only integer epochs and a fixed field whitelist are interpolated; no
        # caller-supplied string reaches the query text.
        aql = (
            f'SELECT {field} AS "value", COUNT(*) AS "count"{label_select} '  # noqa: S608
            "FROM events "
            f"WHERE logsourceid = {int(qradar_log_source_id)} "
            f"AND starttime >= {start_ms} AND starttime < {end_ms} "
            f"GROUP BY {field} ORDER BY COUNT(*) DESC LIMIT {limit}"
        )
        try:
            rows = await self._run_bounded_aggregate(aql, max_rows=limit)
        except (ProviderError, ValueError) as exc:
            # Sanitized: the class name and our own message only, never a
            # provider response body that could carry payload or header data.
            return DimensionAggregate(
                dimension=dimension, available=True, query=aql,
                error=f"{type(exc).__name__}: {exc}"[:400],
            )

        values: list[DimensionValueCount] = []
        total = 0
        for row in rows:
            raw = row.get("value")
            count = as_int(row.get("count"))
            if raw is None or count is None or count < 0:
                continue
            label = row.get("label")
            values.append(
                DimensionValueCount(
                    value=str(raw)[:255],
                    count=count,
                    label=str(label)[:255] if label not in (None, "") else None,
                )
            )
            total += count

        # No rows at all is the signature of a field this DSM never populates.
        # Reporting it as "available with zero values" would assert we measured
        # an absence we did not measure.
        if not values:
            return DimensionAggregate(
                dimension=dimension, available=False, query=aql,
                error="field is not populated for this log source",
            )

        return DimensionAggregate(
            dimension=dimension,
            available=True,
            values=values,
            distinct_count=len(values),
            total_count=total,
            truncated=len(values) >= limit,
            query=aql,
        )

    async def _run_bounded_aggregate(self, aql: str, *, max_rows: int) -> list[dict]:
        """Create, poll and fetch one aggregate search under a hard time bound."""
        handle = await self.create_ariel_search(aql)
        status: ArielSearchStatusDTO | None = None
        for _ in range(120):
            status = await self.get_ariel_search_status(handle.search_id)
            if status.is_terminal:
                break
            await self._sleep(1.0)
        else:
            await self.cancel_ariel_search(handle.search_id)
            raise ProviderUnavailableError("Ariel explanation search timed out")

        if status is None or not status.is_success:
            raise ProviderError("Ariel explanation search did not complete")
        results = await self.get_ariel_search_results(handle.search_id, max_rows=max_rows)
        return list(results.rows)

    # ------------------------------------------------------------- rules
    async def list_rules(self) -> list[AnalyticsRuleDTO]:
        """Every analytics rule, building blocks included.

        QRadar models building blocks as rules with `type == "BUILDINGBLOCK"`,
        so one call returns both and the DTO carries the distinction.
        """
        rows = await self._paginate("/analytics/rules", category=EndpointCategory.RULE_LIST)
        return [dto for dto in (self._to_rule(r) for r in rows) if dto is not None]

    async def list_building_blocks(self) -> list[AnalyticsRuleDTO]:
        rows = await self._paginate(
            "/analytics/building_blocks", category=EndpointCategory.BUILDING_BLOCK_LIST
        )
        out: list[AnalyticsRuleDTO] = []
        for row in rows:
            dto = self._to_rule(row)
            if dto is not None:
                out.append(dto.model_copy(update={"is_building_block": True}))
        return out

    async def get_rule(self, qradar_id: int) -> AnalyticsRuleDTO | None:
        body = await self._request(
            "GET",
            f"/analytics/rules/{int(qradar_id)}",
            category=EndpointCategory.RULE_DETAIL,
            allow_404=True,
        )
        if body is None:
            return None
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar rule detail was not an object")
        return self._to_rule(body)

    # ---------------------------------------------------------- offenses
    async def list_offenses(
        self,
        *,
        open_only: bool = True,
        updated_since: datetime | None = None,
        max_pages: int | None = None,
    ) -> list[OffenseDTO]:
        """Offenses, optionally restricted to those changed since a watermark.

        `updated_since` drives incremental collection: QRadar's filter syntax
        does the narrowing server-side so we never page through the full offense
        history to find the handful that moved.
        """
        filters: list[str] = []
        if open_only:
            filters.append('status="OPEN"')
        if updated_since is not None:
            epoch_ms = int(updated_since.astimezone(UTC).timestamp() * 1000)
            filters.append(f"last_persisted_time>{epoch_ms}")

        params: dict[str, Any] = {}
        if filters:
            params["filter"] = " and ".join(filters)

        rows = await self._paginate(
            "/siem/offenses",
            category=EndpointCategory.OFFENSE_LIST,
            params=params or None,
            max_pages=max_pages,
        )
        return [dto for dto in (self._to_offense(r) for r in rows) if dto is not None]

    async def get_offense(self, qradar_id: int) -> OffenseDTO | None:
        body = await self._request(
            "GET",
            f"/siem/offenses/{int(qradar_id)}",
            category=EndpointCategory.OFFENSE_DETAIL,
            allow_404=True,
        )
        if body is None:
            return None
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar offense detail was not an object")
        return self._to_offense(body)

    async def list_offense_types(self) -> dict[int, str]:
        rows = await self._paginate(
            "/siem/offense_types", category=EndpointCategory.OFFENSE_TYPE_LIST
        )
        out: dict[int, str] = {}
        for row in rows:
            type_id = as_int(row.get("id"))
            name = row.get("name")
            if type_id is not None and isinstance(name, str):
                out[type_id] = name[:255]
        return out

    async def list_offense_closing_reasons(self) -> dict[int, str]:
        rows = await self._paginate(
            "/siem/offense_closing_reasons",
            category=EndpointCategory.OFFENSE_CLOSING_REASON_LIST,
        )
        out: dict[int, str] = {}
        for row in rows:
            reason_id = as_int(row.get("id"))
            text = row.get("text")
            if reason_id is not None and isinstance(text, str):
                out[reason_id] = text[:512]
        return out

    # ------------------------------------------------------ Ariel lifecycle
    async def create_ariel_search(self, aql: str) -> ArielSearchHandleDTO:
        """Start an Ariel search. AQL execution is REST-only, never MCP.

        Deliberately not retried. A create that timed out may still have been
        accepted; replaying it would strand a second search on the appliance
        with no handle to cancel it.
        """
        self.require(ProviderCapability.AQL_EXECUTION)
        body = await self._request(
            "POST",
            "/ariel/searches",
            category=EndpointCategory.ARIEL_CREATE,
            params={"query_expression": aql},
        )
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar did not return an Ariel search handle")
        search_id = body.get("search_id") or body.get("cursor_id")
        if not isinstance(search_id, str) or not search_id:
            raise MalformedUpstreamResponse("QRadar returned an Ariel handle without a search id")
        return ArielSearchHandleDTO(
            search_id=search_id,
            status=str(body.get("status") or "WAIT"),
            progress=as_int(body.get("progress")) or 0,
        )

    async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        body = await self._request(
            "GET",
            f"/ariel/searches/{search_id}",
            category=EndpointCategory.ARIEL_STATUS,
        )
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar Ariel status was not an object")
        errors = body.get("error_messages")
        return ArielSearchStatusDTO(
            search_id=search_id,
            status=str(body.get("status") or "ERROR"),
            progress=as_int(body.get("progress")) or 0,
            record_count=as_int(body.get("record_count")),
            # Upstream error text is untrusted; keep it out of the DTO and let
            # the caller report the status instead.
            error_messages=["QRadar reported the search failed"] if errors else [],
        )

    async def get_ariel_search_results(
        self, search_id: str, *, max_rows: int
    ) -> ArielSearchResultsDTO:
        self.require(ProviderCapability.AQL_EXECUTION)
        body = await self._request(
            "GET",
            f"/ariel/searches/{search_id}/results",
            category=EndpointCategory.ARIEL_RESULTS,
            headers={"Range": f"items=0-{max(0, max_rows - 1)}"},
        )
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("QRadar Ariel results were not an object")
        # QRadar keys the row array by the queried database ("events"/"flows").
        rows: list[dict] = []
        for value in body.values():
            if isinstance(value, list):
                rows = [r for r in value if isinstance(r, dict)]
                break
        columns = sorted({key for row in rows for key in row}) if rows else []
        return ArielSearchResultsDTO(
            search_id=search_id,
            columns=columns,
            rows=rows[:max_rows],
            total_count=len(rows),
            truncated=len(rows) > max_rows,
        )

    async def cancel_ariel_search(self, search_id: str) -> None:
        self.require(ProviderCapability.AQL_EXECUTION)
        # DELETE is idempotent, so a retry is safe and a 404 means "already gone".
        await self._request(
            "DELETE",
            f"/ariel/searches/{search_id}",
            category=EndpointCategory.ARIEL_CANCEL,
            allow_404=True,
        )

    # -------------------------------------------------------- normalization
    def _to_log_source(self, row: dict) -> LogSourceDTO | None:
        qradar_id = as_int(row.get("id"))
        name = row.get("name")
        if qradar_id is None or not isinstance(name, str) or not name.strip():
            # A source without an id or name cannot be keyed or displayed; drop
            # it rather than inventing a placeholder.
            logger.warning("dropping malformed log source row from QRadar")
            return None
        raw_type = row.get("type")
        type_info: dict = raw_type if isinstance(raw_type, dict) else {}
        return LogSourceDTO(
            qradar_id=qradar_id,
            name=name.strip()[:255],
            description=(row.get("description") or None),
            type_id=as_int(row.get("type_id")) or as_int(type_info.get("id")),
            type_name=(type_info.get("name") if isinstance(type_info.get("name"), str) else None),
            protocol_type_id=as_int(row.get("protocol_type_id")),
            enabled=bool(row.get("enabled", True)),
            status=self._log_source_status(row),
            credibility=as_int(row.get("credibility")),
            target_event_collector_id=as_int(row.get("target_event_collector_id")),
            average_eps=None,
            last_event_time=parse_qradar_time(row.get("last_event_time")),
        )

    @staticmethod
    def _log_source_status(row: dict) -> str | None:
        status = row.get("status")
        if isinstance(status, dict):
            value = status.get("status")
            return str(value)[:64] if value is not None else None
        return str(status)[:64] if status is not None else None

    def _to_rule(self, row: dict) -> AnalyticsRuleDTO | None:
        qradar_id = as_int(row.get("id"))
        name = row.get("name")
        if qradar_id is None or not isinstance(name, str) or not name.strip():
            logger.warning("dropping malformed rule row from QRadar")
            return None

        rule_type = row.get("type")
        rule_type_str = str(rule_type)[:64] if rule_type is not None else None
        responses = row.get("rule_responses")
        response_actions = as_str_list(responses, limit=32) if responses else []
        # QRadar's rule payload does not consistently expose a boolean for
        # offense creation, so it is derived from the response set and left
        # None when the payload gives us nothing to derive from.
        generates_offense: bool | None = None
        if responses is not None:
            generates_offense = any(
                "OFFENSE" in action.upper() for action in response_actions
            )

        return AnalyticsRuleDTO(
            qradar_id=qradar_id,
            name=name.strip()[:512],
            description=(row.get("description") or None),
            rule_type=rule_type_str,
            enabled=bool(row.get("enabled", True)),
            origin=(str(row.get("origin"))[:64] if row.get("origin") is not None else None),
            is_building_block=(rule_type_str or "").upper() == "BUILDINGBLOCK",
            owner=(str(row.get("owner"))[:255] if row.get("owner") is not None else None),
            average_capacity=(
                float(row["average_capacity"])
                if isinstance(row.get("average_capacity"), (int, float))
                and not isinstance(row.get("average_capacity"), bool)
                else None
            ),
            categories=as_str_list(row.get("categories"), limit=32),
            # MITRE mapping is SOC-owned; nothing is inferred from a rule
            # payload here. The collector records inferences separately.
            mitre_techniques=[],
            created_at=parse_qradar_time(row.get("creation_date")),
            modified_at=parse_qradar_time(row.get("modification_date")),
            generates_offense=generates_offense,
            response_actions=response_actions,
            building_block_ids=as_int_list(row.get("building_block_ids"), limit=64),
            log_source_type_ids=as_int_list(row.get("log_source_type_ids"), limit=64),
            last_triggered_at=parse_qradar_time(row.get("last_triggered_time")),
            event_contribution_count=as_int(row.get("event_count")),
            offense_contribution_count=as_int(row.get("offense_count")),
        )

    def _to_offense(self, row: dict) -> OffenseDTO | None:
        qradar_id = as_int(row.get("id"))
        if qradar_id is None:
            logger.warning("dropping offense row from QRadar with no id")
            return None

        # QRadar returns offense_type and closing_reason either as a bare id or
        # as an expanded object, depending on API version and `fields`.
        offense_type = row.get("offense_type")
        offense_type_id = as_int(offense_type)
        offense_type_name: str | None = None
        if isinstance(offense_type, dict):
            offense_type_id = as_int(offense_type.get("id"))
            offense_type_name = as_text(offense_type.get("name"), limit=255)

        closing_reason = row.get("closing_reason")
        closing_reason_id = as_int(row.get("closing_reason_id"))
        closing_reason_text: str | None = None
        if isinstance(closing_reason, dict):
            closing_reason_id = closing_reason_id or as_int(closing_reason.get("id"))
            closing_reason_text = as_text(closing_reason.get("text"), limit=512)
        elif isinstance(closing_reason, str):
            closing_reason_text = as_text(closing_reason, limit=512)

        return OffenseDTO(
            qradar_id=qradar_id,
            description=(row.get("description") or None),
            status=as_text(row.get("status"), limit=32),
            magnitude=as_int(row.get("magnitude")),
            severity=as_int(row.get("severity")),
            credibility=as_int(row.get("credibility")),
            relevance=as_int(row.get("relevance")),
            assigned_to=as_text(row.get("assigned_to"), limit=255),
            offense_type=offense_type_id,
            offense_type_name=offense_type_name,
            offense_source=as_text(row.get("offense_source"), limit=255),
            source_network=as_text(row.get("source_network"), limit=255),
            event_count=as_int(row.get("event_count")),
            flow_count=as_int(row.get("flow_count")),
            device_count=as_int(row.get("device_count")),
            source_count=as_int(row.get("source_count")),
            destination_count=as_int(row.get("local_destination_count")),
            start_time=parse_qradar_time(row.get("start_time")),
            last_updated_time=parse_qradar_time(
                row.get("last_persisted_time") or row.get("last_updated_time")
            ),
            close_time=parse_qradar_time(row.get("close_time")),
            closing_reason_id=closing_reason_id,
            closing_reason=closing_reason_text,
            categories=as_str_list(row.get("categories"), limit=32),
            source_addresses=as_str_list(row.get("source_address_ids")),
            local_destination_addresses=as_str_list(row.get("local_destination_address_ids")),
            usernames=as_str_list(row.get("usernames"), limit=32),
            log_source_ids=as_int_list(row.get("log_sources"), limit=64),
            rule_ids=as_int_list(row.get("rules"), limit=64),
        )
