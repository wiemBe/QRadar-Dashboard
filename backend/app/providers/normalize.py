"""Coercion helpers for untrusted QRadar payloads.

Shared by the REST and MCP providers, which see the same QRadar JSON by two
routes and must normalize it identically — a field that means one thing over
REST and another over MCP would make the two providers non-interchangeable.

Every helper is total: it returns a usable value or None/[] and never raises on
bad input. Upstream data is attacker-influenced (offense descriptions and
usernames come from parsed events), so the parsing layer degrades rather than
trusting shape. Callers decide whether a missing field is fatal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

#: Address/username lists are capped when normalizing. QRadar can attach
#: thousands to a wide offense; we store a bounded sample and rely on the
#: separate source_count/destination_count for true breadth.
MAX_ADDRESSES = 100


def parse_qradar_time(value: Any) -> datetime | None:
    """QRadar timestamps are epoch milliseconds. Always returns UTC or None.

    Zero and negative values mean "unset" in QRadar (notably `close_time` on an
    open offense), not 1970, so they are normalized to None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def as_text(value: Any, *, limit: int) -> str | None:
    """Truncate to the column width. Empty and whitespace-only become None."""
    if value is None or isinstance(value, bool):
        return None
    text = value.strip() if isinstance(value, str) else str(value)
    return text[:limit] if text else None


def as_str_list(value: Any, *, limit: int = MAX_ADDRESSES) -> list[str]:
    """Coerce an upstream list to bounded strings, dropping anything unusable."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:limit]:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            text = str(item)
        else:
            continue
        if text:
            out.append(text[:255])
    return out


def as_int_list(value: Any, *, limit: int = MAX_ADDRESSES) -> list[int]:
    """Coerce a list of ids, accepting either scalars or `{"id": n}` objects."""
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value[:limit]:
        candidate = item.get("id") if isinstance(item, dict) else item
        parsed = as_int(candidate)
        if parsed is not None:
            out.append(parsed)
    return out
