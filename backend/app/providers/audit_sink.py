"""Bridge the MCP provider's audit callback to the platform AuditLog.

`QRadarMCPProvider` accepts an `audit_sink` callable and emits one record per
tool call — success, failure, denial and unknown tool alike. Until now nothing
supplied the callable, so MCP activity was logged to stdout and lost. This binds
it to the same append-only `AuditLog` table that carries administrative and
search actions, so MCP tool use is reviewable alongside everything else.

What is deliberately *not* stored: the SEC token, the SEC header, response
bodies, raw event payloads and stack traces. The provider already restricts the
record to argument key *names* and a sanitized error label; this module adds
correlation and instance context and nothing else.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.audit import new_correlation_id, record_audit

logger = logging.getLogger("app.providers.audit_sink")

AuditSink = Callable[[dict], Awaitable[None]]

#: Fields the provider may put in `detail` that we are willing to persist.
#: An allowlist rather than a denylist: a future provider change that starts
#: emitting a response body must not silently start writing it to the database.
_ALLOWED_DETAIL_KEYS = frozenset(
    {"tool", "duration_ms", "argument_keys", "error", "provider", "capability"}
)


def make_audit_sink(
    session: AsyncSession,
    *,
    instance_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
    capability: str | None = None,
) -> AuditSink:
    """Build an audit sink bound to one session and one logical operation.

    `correlation_id` ties every tool call made while serving one request or one
    collection run together; a fresh one is generated when the caller does not
    supply it, so records are never uncorrelated.

    An exception from the write propagates rather than being swallowed. If the
    audit trail cannot be written, an audited read-only integration should fail
    rather than proceed unaudited.
    """
    correlation = correlation_id or new_correlation_id()

    async def sink(record: dict) -> None:
        raw_detail = record.get("detail") or {}
        detail = {k: v for k, v in raw_detail.items() if k in _ALLOWED_DETAIL_KEYS}

        dropped = set(raw_detail) - _ALLOWED_DETAIL_KEYS
        if dropped:
            # Loud, because it means the provider is emitting something this
            # module has not vetted for sensitivity.
            logger.warning(
                "dropped unvetted MCP audit detail keys",
                extra={"dropped_keys": sorted(dropped)},
            )

        detail["correlation_id"] = correlation
        if instance_id is not None:
            detail["instance_id"] = str(instance_id)
        if capability is not None:
            detail.setdefault("capability", capability)

        await record_audit(
            session,
            action=str(record.get("action", "mcp.tool_call")),
            actor=record.get("actor"),
            object_type=str(record.get("object_type", "mcp_tool")),
            object_id=record.get("object_id"),
            outcome=str(record.get("outcome", "UNKNOWN")),
            detail=detail,
        )

    return sink
