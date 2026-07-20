"""Provider call telemetry.

Every upstream call records duration, outcome class and an *endpoint category*
rather than the concrete path. Categories keep cardinality bounded and, more
importantly, keep identifiers out of telemetry: `siem/offenses/4821` becomes
`offense_detail`, so offense ids never leak into metrics or logs.

Nothing here records request bodies, response bodies, headers or tokens.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Lock

logger = logging.getLogger("app.providers.telemetry")


class EndpointCategory(StrEnum):
    SYSTEM = "system"
    LOG_SOURCE_LIST = "log_source_list"
    LOG_SOURCE_DETAIL = "log_source_detail"
    LOG_SOURCE_TYPE_LIST = "log_source_type_list"
    RULE_LIST = "rule_list"
    RULE_DETAIL = "rule_detail"
    BUILDING_BLOCK_LIST = "building_block_list"
    OFFENSE_LIST = "offense_list"
    OFFENSE_DETAIL = "offense_detail"
    OFFENSE_TYPE_LIST = "offense_type_list"
    OFFENSE_CLOSING_REASON_LIST = "offense_closing_reason_list"
    ARIEL_CREATE = "ariel_create"
    ARIEL_STATUS = "ariel_status"
    ARIEL_RESULTS = "ariel_results"
    ARIEL_CANCEL = "ariel_cancel"
    MCP_TOOL = "mcp_tool"


class ErrorClass(StrEnum):
    OK = "ok"
    AUTH = "auth"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    NETWORK = "network"
    UPSTREAM_ERROR = "upstream_error"
    MALFORMED = "malformed"
    NOT_ALLOWED = "not_allowed"


@dataclass
class CallStat:
    calls: int = 0
    errors: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    by_error_class: dict[str, int] = field(default_factory=dict)

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.calls if self.calls else 0.0


class ProviderMetrics:
    """In-process counters, exposed through the provider capability endpoint.

    Deliberately not a Prometheus client: the platform has no metrics exporter
    yet, and a dict keyed by a bounded category set is enough to answer "is the
    REST provider slow or erroring" from the API. Swapping in a real registry
    later touches only this class.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._stats: dict[tuple[str, str], CallStat] = {}

    def record(
        self,
        *,
        provider: str,
        category: EndpointCategory | str,
        duration_ms: float,
        error_class: ErrorClass | str,
    ) -> None:
        key = (str(provider), str(category))
        with self._lock:
            stat = self._stats.setdefault(key, CallStat())
            stat.calls += 1
            stat.total_ms += duration_ms
            stat.max_ms = max(stat.max_ms, duration_ms)
            if str(error_class) != ErrorClass.OK:
                stat.errors += 1
                stat.by_error_class[str(error_class)] = (
                    stat.by_error_class.get(str(error_class), 0) + 1
                )

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "provider": provider,
                    "endpoint_category": category,
                    "calls": s.calls,
                    "errors": s.errors,
                    "avg_ms": round(s.avg_ms, 2),
                    "max_ms": round(s.max_ms, 2),
                    "by_error_class": dict(s.by_error_class),
                }
                for (provider, category), s in sorted(self._stats.items())
            ]

    def reset(self) -> None:
        with self._lock:
            self._stats.clear()


PROVIDER_METRICS = ProviderMetrics()


@contextmanager
def observe(
    provider: str, category: EndpointCategory | str
) -> Iterator[dict[str, ErrorClass]]:
    """Time a provider call and record its outcome.

    The caller sets `outcome["error_class"]`; an escaping exception is recorded
    as UPSTREAM_ERROR unless the caller already classified it.
    """
    outcome: dict[str, ErrorClass] = {"error_class": ErrorClass.OK}
    started = time.perf_counter()
    try:
        yield outcome
    except BaseException:
        if outcome["error_class"] is ErrorClass.OK:
            outcome["error_class"] = ErrorClass.UPSTREAM_ERROR
        raise
    finally:
        duration_ms = (time.perf_counter() - started) * 1000.0
        PROVIDER_METRICS.record(
            provider=provider,
            category=category,
            duration_ms=duration_ms,
            error_class=outcome["error_class"],
        )
        logger.debug(
            "provider call",
            extra={
                "provider": provider,
                "endpoint_category": str(category),
                "duration_ms": round(duration_ms, 2),
                "error_class": str(outcome["error_class"]),
            },
        )
