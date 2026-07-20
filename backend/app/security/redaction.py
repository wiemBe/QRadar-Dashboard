"""Redact secrets from structures before they are logged or persisted to audit.

Applied to audit detail payloads and notification payloads. Keys whose name
suggests a secret are masked; token-shaped values are masked wherever they
appear.
"""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|sec|webhook|smtp_password)",
    re.IGNORECASE,
)
_TOKEN_VALUE_RE = re.compile(r"[A-Za-z0-9_\-]{24,}")
_MASK = "***redacted***"

#: Keys whose values are correlation identifiers, not credentials.
#:
#: A UUID and a QRadar SEC token are the same shape, so the value-level
#: token heuristic below masks both. That is the right default — but applied to
#: an audit record's own correlation and instance ids it destroys the only
#: thing that makes the records joinable, silently, because the mask is a
#: non-empty string that still looks fine in a spot check.
#:
#: The exemption is keyed on the *name*, and only these names, so it cannot
#: widen to a value that merely happens to sit next to one. Never add a key
#: here whose value could carry a credential.
_IDENTIFIER_KEYS = frozenset({"correlation_id", "instance_id"})


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (
                _MASK
                if _SECRET_KEY_RE.search(k)
                else v
                if k in _IDENTIFIER_KEYS and isinstance(v, str)
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # Mask long token-like substrings but leave short readable text intact.
        if len(value) >= 24 and _TOKEN_VALUE_RE.fullmatch(value):
            return _MASK
        return value
    return value
