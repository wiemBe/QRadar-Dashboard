"""QRadarMCPProvider — read-only inventory retrieval over the IBM qradar-mcp service.

Deliberately restricted, in this order of precedence:

  1. Capabilities exclude AQL_EXECUTION. Running Ariel searches is a POST surface
     that we keep off the MCP path entirely; the REST provider owns query
     execution. See docs/mcp-capability-matrix.md section 1.2.

  2. A hardcoded read-only allowlist (`ALLOWED_TOOLS`) is checked before every
     call, independent of what the MCP server advertises. Even if the mounted
     feature-toggle file were misconfigured to re-enable writes, this provider
     would still refuse to invoke a write tool. This is defence-in-depth layer 5
     from the capability matrix, and it is the layer that actually holds.

  3. Capability negotiation fails *closed*. If the server advertises a tool set
     that contradicts the allowlist — notably by exposing a known write tool —
     the provider marks itself degraded and refuses to dispatch rather than
     quietly continuing against a server that is not in the expected posture.

A denied write tool never falls back to REST. Silently satisfying a blocked
request through another path would defeat the entire control, so denial is
terminal and audited.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

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
    InstanceInfoDTO,
    LogSourceDTO,
    LogSourceTypeDTO,
    OffenseDTO,
)
from app.providers.normalize import (
    as_int,
    as_int_list,
    as_str_list,
    parse_qradar_time,
)
from app.providers.qradar_rest import MalformedUpstreamResponse
from app.providers.telemetry import EndpointCategory, ErrorClass, observe

logger = logging.getLogger("app.providers.qradar_mcp")

PROVIDER_NAME = "mcp"

# Read-only GET tools only. Any tool not on this list cannot be invoked through
# this provider, full stop. Mirrors the "Primary/High" read rows of the matrix.
ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "get_system_info",
        "list_servers",
        "list_log_sources",
        "get_log_source",
        "list_log_source_types",
        "list_rules",
        "get_rule",
        "list_building_blocks",
        "list_offenses",
        "get_offense",
        "get_offense_notes",
        "list_offense_types",
        "list_offense_closing_reasons",
        "list_saved_searches",
        "get_saved_search",
        "list_users",
        "list_user_roles",
        "list_qid_records",
        "list_dsm_event_mappings",
    }
)

# The 25 mutating tools the pinned qradar-mcp commit exposes (17 POST, 8 DELETE),
# enumerated from docs/mcp-capability-matrix.md section 3.
#
# The allowlist above is what enforces policy — this set exists so capability
# negotiation can *detect* a server running with writes re-enabled, and so the
# test suite can assert every known write tool is refused by name. Adding a tool
# here does not block it; removing one does not permit it.
KNOWN_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        # --- POST (17) ---
        "create_ariel_search",
        "validate_aql",
        "update_offense",
        "add_offense_note",
        "create_reference_set",
        "update_reference_set",
        "add_to_reference_set",
        "create_reference_map",
        "add_to_reference_map",
        "create_reference_table",
        "add_to_reference_table",
        "create_qid_record",
        "update_qid_record",
        "create_dsm_event_mapping",
        "update_dsm_event_mapping",
        "dns_lookup",
        "whois_lookup",
        # --- DELETE (8) ---
        "delete_ariel_search",
        "delete_saved_search",
        "delete_reference_set",
        "remove_from_reference_set",
        "delete_reference_map",
        "remove_from_reference_map",
        "delete_reference_table",
        "remove_from_reference_table",
    }
)

#: Ariel tools must never be reachable over MCP, whatever the allowlist says.
#: Belt and braces against a future edit widening the allowlist by accident.
FORBIDDEN_TOOL_PREFIXES: tuple[str, ...] = ("create_ariel", "delete_ariel", "validate_aql")

#: Responses larger than this are refused unread. A hostile or broken MCP server
#: must not be able to exhaust worker memory.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class MCPToolNotAllowedError(ProviderError):
    """A code path attempted an MCP tool outside the read-only allowlist."""


class MCPCapabilityError(ProviderError):
    """Capability discovery returned a posture we refuse to operate against."""


class MCPResponseTooLarge(ProviderError):
    """The MCP server returned more data than we will read."""


class QRadarMCPProvider(QRadarProvider):
    # Note: no AQL_EXECUTION. This is the whole point of the split.
    capabilities = frozenset(
        {
            ProviderCapability.INVENTORY,
            ProviderCapability.OFFENSES,
        }
    )

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 60.0,
        connect_timeout: float = 10.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        client: httpx.AsyncClient | None = None,
        audit_sink: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        # Callable invoked with one audit record per tool call. The Celery/API
        # callers wire this to app.repositories.audit.record_audit; left None it
        # degrades to logging only, so the provider stays usable in unit tests.
        self._audit_sink = audit_sink
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
        )
        self._negotiated: frozenset[str] | None = None
        self._request_id = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- guards
    def _guard(self, tool_name: str) -> None:
        """Refuse anything not explicitly allowlisted. Fails closed.

        Checked before argument validation and before any network I/O, so a
        blocked tool never produces a request at all.
        """
        if any(tool_name.startswith(prefix) for prefix in FORBIDDEN_TOOL_PREFIXES):
            raise MCPToolNotAllowedError(
                f"MCP tool {tool_name!r} is permanently blocked: "
                "Ariel/AQL execution is REST-only by policy"
            )
        if tool_name not in ALLOWED_TOOLS:
            raise MCPToolNotAllowedError(
                f"MCP tool {tool_name!r} is not on the read-only allowlist"
            )

    @staticmethod
    def _validate_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize tool arguments before they leave the process.

        Only scalars are permitted. Nested structures are the shape an injected
        instruction would take, and no read tool we call needs one.
        """
        clean: dict[str, Any] = {}
        for key, value in arguments.items():
            if not isinstance(key, str) or not key.isidentifier():
                raise ValueError(f"invalid MCP argument name for {tool_name}: {key!r}")
            if isinstance(value, (bool, int)):
                clean[key] = value
            elif isinstance(value, str):
                if len(value) > 512:
                    raise ValueError(f"MCP argument {key!r} exceeds the length limit")
                clean[key] = value
            elif value is None:
                continue
            else:
                raise ValueError(
                    f"MCP argument {key!r} must be a scalar; got {type(value).__name__}"
                )
        return clean

    # --------------------------------------------------- capability handshake
    async def negotiate_capabilities(self) -> frozenset[str]:
        """Discover the server's tool set and refuse an unexpected posture.

        Fails closed on two conditions:
          * the server advertises a known write tool (writes are enabled), or
          * it advertises none of our allowlisted tools (wrong server/version).
        """
        with observe(PROVIDER_NAME, EndpointCategory.MCP_TOOL) as outcome:
            try:
                response = await self._client.get("/tools")
            except httpx.TimeoutException as exc:
                outcome["error_class"] = ErrorClass.TIMEOUT
                raise ProviderUnavailableError("MCP capability discovery timed out") from exc
            except httpx.TransportError as exc:
                outcome["error_class"] = ErrorClass.NETWORK
                raise ProviderUnavailableError("Could not reach the MCP service") from exc

            if response.status_code in (401, 403):
                outcome["error_class"] = ErrorClass.AUTH
                raise ProviderAuthError("MCP service rejected the request")
            if response.status_code >= 400:
                outcome["error_class"] = ErrorClass.UPSTREAM_ERROR
                raise ProviderUnavailableError("MCP capability discovery failed")

            body = self._decode(response, outcome)

        advertised = self._extract_tool_names(body)

        exposed_writes = advertised & KNOWN_WRITE_TOOLS
        if exposed_writes:
            # Do not proceed. A server advertising write tools is not in the
            # posture this deployment was reviewed against, and continuing would
            # mean trusting the allowlist as the only remaining control.
            raise MCPCapabilityError(
                "MCP service advertises write tools; refusing to operate. "
                f"Offending tools: {', '.join(sorted(exposed_writes))}"
            )
        if advertised and not (advertised & ALLOWED_TOOLS):
            raise MCPCapabilityError(
                "MCP service advertises no allowlisted read tools; capability discovery "
                "is inconsistent with the pinned qradar-mcp commit"
            )

        self._negotiated = frozenset(advertised & ALLOWED_TOOLS)
        return self._negotiated

    @staticmethod
    def _extract_tool_names(body: Any) -> set[str]:
        if isinstance(body, dict):
            body = body.get("tools", [])
        if not isinstance(body, list):
            raise MCPCapabilityError("MCP capability discovery returned an unexpected shape")
        names: set[str] = set()
        for item in body:
            if isinstance(item, str):
                names.add(item)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                names.add(item["name"])
        return names

    # ----------------------------------------------------------- invocation
    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None, *, caller: str = "system"
    ) -> Any:
        """Invoke one allowlisted MCP tool.

        Every call is audited with the tool name, duration, outcome and caller.
        Arguments are recorded only as key names, and the response body is never
        audited: both can carry QRadar data we have no reason to duplicate into
        the audit log.
        """
        args = dict(arguments or {})
        started = datetime.now(UTC)

        try:
            self._guard(tool_name)
            clean_args = self._validate_arguments(tool_name, args)
        except (MCPToolNotAllowedError, ValueError) as exc:
            await self._audit(
                tool_name,
                caller=caller,
                outcome="DENIED",
                started=started,
                arg_keys=sorted(args),
                error=type(exc).__name__,
            )
            # Deliberately no REST fallback. A denied tool is a policy decision,
            # not a transport failure.
            raise

        if self._negotiated is not None and tool_name not in self._negotiated:
            await self._audit(
                tool_name,
                caller=caller,
                outcome="DENIED",
                started=started,
                arg_keys=sorted(args),
                error="NotNegotiated",
            )
            raise MCPToolNotAllowedError(
                f"MCP tool {tool_name!r} was not advertised during capability negotiation"
            )

        with observe(PROVIDER_NAME, EndpointCategory.MCP_TOOL) as outcome:
            self._request_id += 1
            payload = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": clean_args},
            }
            try:
                response = await self._client.post("/mcp", json=payload)
            except httpx.TimeoutException as exc:
                outcome["error_class"] = ErrorClass.TIMEOUT
                await self._audit(
                    tool_name, caller=caller, outcome="TIMEOUT", started=started,
                    arg_keys=sorted(clean_args), error="Timeout",
                )
                raise ProviderUnavailableError(f"MCP tool {tool_name} timed out") from exc
            except httpx.TransportError as exc:
                outcome["error_class"] = ErrorClass.NETWORK
                await self._audit(
                    tool_name, caller=caller, outcome="FAILURE", started=started,
                    arg_keys=sorted(clean_args), error="Transport",
                )
                raise ProviderUnavailableError("Could not reach the MCP service") from exc

            if response.status_code in (401, 403):
                outcome["error_class"] = ErrorClass.AUTH
                await self._audit(
                    tool_name, caller=caller, outcome="FAILURE", started=started,
                    arg_keys=sorted(clean_args), error="Auth",
                )
                raise ProviderAuthError("MCP service rejected the request")
            if response.status_code >= 400:
                outcome["error_class"] = ErrorClass.UPSTREAM_ERROR
                await self._audit(
                    tool_name, caller=caller, outcome="FAILURE", started=started,
                    arg_keys=sorted(clean_args), error=f"HTTP{response.status_code}",
                )
                raise ProviderUnavailableError(f"MCP tool {tool_name} failed upstream")

            body = self._decode(response, outcome)

        result = self._unwrap(tool_name, body)
        await self._audit(
            tool_name, caller=caller, outcome="SUCCESS", started=started,
            arg_keys=sorted(clean_args), error=None,
        )
        return result

    def _decode(self, response: httpx.Response, outcome: dict) -> Any:
        raw = response.content
        if len(raw) > self._max_response_bytes:
            outcome["error_class"] = ErrorClass.MALFORMED
            raise MCPResponseTooLarge(
                f"MCP response exceeded the {self._max_response_bytes} byte limit"
            )
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as exc:
            outcome["error_class"] = ErrorClass.MALFORMED
            raise MalformedUpstreamResponse("MCP returned a body that was not valid JSON") from exc

    @staticmethod
    def _unwrap(tool_name: str, body: Any) -> Any:
        """Normalize a JSON-RPC envelope into the tool's payload."""
        if body is None:
            return None
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse(f"MCP tool {tool_name} returned a non-object envelope")
        if "error" in body and body["error"] is not None:
            # Upstream error text is untrusted; report the tool, not the message.
            raise ProviderError(f"MCP tool {tool_name} reported an error")

        result = body.get("result", body)
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            # MCP content blocks: concatenate the text parts and parse as JSON.
            texts: list[str] = [
                block["text"]
                for block in result["content"]
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            if texts:
                try:
                    return json.loads("".join(texts))
                except ValueError as exc:
                    raise MalformedUpstreamResponse(
                        f"MCP tool {tool_name} returned unparseable content"
                    ) from exc
        return result

    async def _audit(
        self,
        tool_name: str,
        *,
        caller: str,
        outcome: str,
        started: datetime,
        arg_keys: list[str],
        error: str | None,
    ) -> None:
        duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        record = {
            "action": "mcp.tool_call",
            "actor": caller,
            "object_type": "mcp_tool",
            "object_id": tool_name,
            "outcome": outcome,
            "detail": {
                "tool": tool_name,
                "duration_ms": duration_ms,
                # Names only — values may contain QRadar identifiers.
                "argument_keys": arg_keys,
                "error": error,
                "provider": PROVIDER_NAME,
            },
        }
        detail: dict = dict(record["detail"])  # type: ignore[arg-type]
        logger.info("mcp tool call", extra={**detail, "outcome": outcome})
        if self._audit_sink is not None:
            await self._audit_sink(record)

    # ------------------------------------------------------------- read API
    @staticmethod
    def _rows(payload: Any) -> list[dict]:
        if isinstance(payload, dict):
            for key in ("items", "results", "data"):
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    async def get_instance_info(self) -> InstanceInfoDTO:
        body = await self.call_tool("get_system_info")
        if not isinstance(body, dict):
            raise MalformedUpstreamResponse("get_system_info did not return an object")
        version = body.get("external_version") or body.get("version")
        return InstanceInfoDTO(
            version=str(version) if version is not None else None,
            build=str(body.get("build_version")) if body.get("build_version") else None,
            reachable=True,
            raw_about={
                k: v
                for k, v in body.items()
                if k in {"release_name", "external_version", "build_version"}
            },
        )

    async def list_log_sources(self) -> list[LogSourceDTO]:
        rows = self._rows(await self.call_tool("list_log_sources"))
        return [dto for dto in (self._to_log_source(r) for r in rows) if dto is not None]

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        body = await self.call_tool("get_log_source", {"log_source_id": int(qradar_id)})
        return self._to_log_source(body) if isinstance(body, dict) else None

    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:
        rows = self._rows(await self.call_tool("list_log_source_types"))
        out: list[LogSourceTypeDTO] = []
        for row in rows:
            type_id = as_int(row.get("id"))
            name = row.get("name")
            if type_id is not None and isinstance(name, str):
                out.append(LogSourceTypeDTO(type_id=type_id, name=name[:255]))
        return out

    async def list_rules(self) -> list[AnalyticsRuleDTO]:
        rows = self._rows(await self.call_tool("list_rules"))
        return [dto for dto in (self._to_rule(r) for r in rows) if dto is not None]

    async def list_building_blocks(self) -> list[AnalyticsRuleDTO]:
        rows = self._rows(await self.call_tool("list_building_blocks"))
        out: list[AnalyticsRuleDTO] = []
        for row in rows:
            dto = self._to_rule(row)
            if dto is not None:
                out.append(dto.model_copy(update={"is_building_block": True}))
        return out

    async def get_rule(self, qradar_id: int) -> AnalyticsRuleDTO | None:
        body = await self.call_tool("get_rule", {"rule_id": int(qradar_id)})
        return self._to_rule(body) if isinstance(body, dict) else None

    async def list_offenses(
        self,
        *,
        open_only: bool = True,
        updated_since: datetime | None = None,
        max_pages: int | None = None,
    ) -> list[OffenseDTO]:
        args: dict[str, Any] = {}
        if open_only:
            args["status"] = "OPEN"
        rows = self._rows(await self.call_tool("list_offenses", args))
        out = [dto for dto in (self._to_offense(r) for r in rows) if dto is not None]
        if updated_since is not None:
            # The MCP read tool exposes no watermark filter, so honour the
            # contract locally rather than returning rows that predate it.
            cutoff = updated_since.astimezone(UTC)
            out = [
                o for o in out
                if o.last_updated_time is not None and o.last_updated_time > cutoff
            ]
        return out

    async def get_offense(self, qradar_id: int) -> OffenseDTO | None:
        body = await self.call_tool("get_offense", {"offense_id": int(qradar_id)})
        return self._to_offense(body) if isinstance(body, dict) else None

    async def list_offense_types(self) -> dict[int, str]:
        rows = self._rows(await self.call_tool("list_offense_types"))
        return {
            tid: str(row["name"])[:255]
            for row in rows
            if (tid := as_int(row.get("id"))) is not None and isinstance(row.get("name"), str)
        }

    async def list_offense_closing_reasons(self) -> dict[int, str]:
        rows = self._rows(await self.call_tool("list_offense_closing_reasons"))
        return {
            rid: str(row["text"])[:512]
            for row in rows
            if (rid := as_int(row.get("id"))) is not None and isinstance(row.get("text"), str)
        }

    # -------------------------------------------------------- normalization
    # The MCP service proxies QRadar's own JSON, so normalization matches the
    # REST provider's. Shared helpers keep the two from drifting apart.
    @staticmethod
    def _to_log_source(row: dict) -> LogSourceDTO | None:
        qradar_id = as_int(row.get("id"))
        name = row.get("name")
        if qradar_id is None or not isinstance(name, str) or not name.strip():
            logger.warning("dropping malformed log source row from MCP")
            return None
        raw_type = row.get("type")
        type_info: dict = raw_type if isinstance(raw_type, dict) else {}
        status = row.get("status")
        return LogSourceDTO(
            qradar_id=qradar_id,
            name=name.strip()[:255],
            description=(row.get("description") or None),
            type_id=as_int(row.get("type_id")) or as_int(type_info.get("id")),
            type_name=(type_info.get("name") if isinstance(type_info.get("name"), str) else None),
            protocol_type_id=as_int(row.get("protocol_type_id")),
            enabled=bool(row.get("enabled", True)),
            status=(
                str(status.get("status"))[:64]
                if isinstance(status, dict) and status.get("status") is not None
                else (str(status)[:64] if status is not None else None)
            ),
            credibility=as_int(row.get("credibility")),
            target_event_collector_id=as_int(row.get("target_event_collector_id")),
            average_eps=None,
            last_event_time=parse_qradar_time(row.get("last_event_time")),
        )

    @staticmethod
    def _to_rule(row: dict) -> AnalyticsRuleDTO | None:
        qradar_id = as_int(row.get("id"))
        name = row.get("name")
        if qradar_id is None or not isinstance(name, str) or not name.strip():
            logger.warning("dropping malformed rule row from MCP")
            return None
        rule_type = row.get("type")
        rule_type_str = str(rule_type)[:64] if rule_type is not None else None
        return AnalyticsRuleDTO(
            qradar_id=qradar_id,
            name=name.strip()[:512],
            description=(row.get("description") or None),
            rule_type=rule_type_str,
            enabled=bool(row.get("enabled", True)),
            origin=(str(row.get("origin"))[:64] if row.get("origin") is not None else None),
            is_building_block=(rule_type_str or "").upper() == "BUILDINGBLOCK",
            owner=(str(row.get("owner"))[:255] if row.get("owner") is not None else None),
            categories=as_str_list(row.get("categories"), limit=32),
            mitre_techniques=[],
            created_at=parse_qradar_time(row.get("creation_date")),
            modified_at=parse_qradar_time(row.get("modification_date")),
            building_block_ids=as_int_list(row.get("building_block_ids"), limit=64),
            log_source_type_ids=as_int_list(row.get("log_source_type_ids"), limit=64),
            last_triggered_at=parse_qradar_time(row.get("last_triggered_time")),
        )

    @staticmethod
    def _to_offense(row: dict) -> OffenseDTO | None:
        qradar_id = as_int(row.get("id"))
        if qradar_id is None:
            logger.warning("dropping offense row from MCP with no id")
            return None
        offense_type = row.get("offense_type")
        offense_type_id = as_int(offense_type)
        offense_type_name = None
        if isinstance(offense_type, dict):
            offense_type_id = as_int(offense_type.get("id"))
            raw = offense_type.get("name")
            offense_type_name = str(raw)[:255] if isinstance(raw, str) else None
        assigned = row.get("assigned_to")
        return OffenseDTO(
            qradar_id=qradar_id,
            description=(row.get("description") or None),
            status=(str(row.get("status"))[:32] if row.get("status") is not None else None),
            magnitude=as_int(row.get("magnitude")),
            severity=as_int(row.get("severity")),
            credibility=as_int(row.get("credibility")),
            relevance=as_int(row.get("relevance")),
            assigned_to=(
                assigned.strip()[:255]
                if isinstance(assigned, str) and assigned.strip()
                else None
            ),
            offense_type=offense_type_id,
            offense_type_name=offense_type_name,
            offense_source=(
                str(row.get("offense_source"))[:255]
                if row.get("offense_source") is not None
                else None
            ),
            source_network=(
                str(row.get("source_network"))[:255]
                if row.get("source_network") is not None
                else None
            ),
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
            closing_reason_id=as_int(row.get("closing_reason_id")),
            categories=as_str_list(row.get("categories"), limit=32),
            source_addresses=as_str_list(row.get("source_address_ids")),
            local_destination_addresses=as_str_list(row.get("local_destination_address_ids")),
            usernames=as_str_list(row.get("usernames"), limit=32),
            log_source_ids=as_int_list(row.get("log_sources"), limit=64),
            rule_ids=as_int_list(row.get("rules"), limit=64),
        )
