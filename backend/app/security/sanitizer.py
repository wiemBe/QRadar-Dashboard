"""Output sanitization for anything sourced from QRadar event/offense data.

QRadar payloads contain attacker-controlled strings: offense descriptions,
usernames, log-source names harvested from raw events. Rendering them verbatim
in the SPA is a stored-XSS vector. Everything that originated outside this
platform passes through `sanitize_text` before it reaches an API response.
"""

from __future__ import annotations

from typing import Any

import bleach

# We render as text, not HTML. The safe move is to allow no tags at all and
# strip them, so "<script>" becomes inert text rather than an executable node.
_ALLOWED_TAGS: list[str] = []
_ALLOWED_ATTRS: dict[str, list[str]] = {}


def sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = bleach.clean(
        value, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True
    )
    # Neutralise control characters that can corrupt terminal/log rendering.
    return "".join(ch for ch in cleaned if ch >= " " or ch in "\t\n")


def sanitize_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize every string leaf of a mapping (e.g. offense JSON)."""
    out: dict[str, Any] = {}
    for key, val in data.items():
        out[key] = _sanitize_value(val)
    return out


def _sanitize_value(val: Any) -> Any:
    if isinstance(val, str):
        return sanitize_text(val)
    if isinstance(val, dict):
        return sanitize_mapping(val)
    if isinstance(val, list):
        return [_sanitize_value(v) for v in val]
    return val
