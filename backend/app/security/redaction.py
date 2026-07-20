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


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (_MASK if _SECRET_KEY_RE.search(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # Mask long token-like substrings but leave short readable text intact.
        if len(value) >= 24 and _TOKEN_VALUE_RE.fullmatch(value):
            return _MASK
        return value
    return value
