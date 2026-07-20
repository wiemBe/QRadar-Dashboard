"""Rate limiting via slowapi.

A single shared Limiter keyed by client IP. Search execution has a tighter
dedicated limit applied at its route, since each execution consumes a scarce
Ariel search slot.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[get_settings().rate_limit_default],
    headers_enabled=True,
)
