"""Exponential backoff with full jitter.

Pure and deterministic when a random source is injected, so retry-timing tests
don't flake. Full jitter (random between 0 and the capped exponential) is the
AWS-recommended strategy: it spreads retries out and avoids the thundering herd
that plain exponential backoff produces when many workers fail together.
"""

from __future__ import annotations

import random


def backoff_delay(
    attempt: int,
    *,
    base_seconds: float,
    max_seconds: float,
    rng: random.Random | None = None,
) -> float:
    """Delay before retry `attempt` (attempt counting starts at 1).

    exp = base * 2**(attempt-1), capped at max; return uniform(0, exp).
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    exp = min(max_seconds, base_seconds * (2 ** (attempt - 1)))
    r = rng or random
    return r.uniform(0, exp)


def next_retry_at_offset(
    attempt: int,
    *,
    base_seconds: float,
    max_seconds: float,
    rng: random.Random | None = None,
) -> float:
    """Alias kept for call sites that read better as an offset in seconds."""
    return backoff_delay(attempt, base_seconds=base_seconds, max_seconds=max_seconds, rng=rng)
