"""Backoff jitter bounds and the execution error taxonomy."""

from __future__ import annotations

import random

import httpx
import pytest

from app.models.enums import ExecutionErrorType
from app.providers.base import ProviderAuthError, ProviderUnavailableError
from app.services.aql_validator import AQLValidationError
from app.services.ariel_errors import (
    ArielSearchFailed,
    ArielTimeout,
    ResultParsingError,
    classify,
    is_retryable,
)
from app.services.backoff import backoff_delay


def test_backoff_is_within_full_jitter_bounds() -> None:
    rng = random.Random(0)
    for attempt in range(1, 8):
        exp = min(60.0, 2.0 * (2 ** (attempt - 1)))
        for _ in range(50):
            d = backoff_delay(attempt, base_seconds=2.0, max_seconds=60.0, rng=rng)
            assert 0.0 <= d <= exp


def test_backoff_is_capped() -> None:
    rng = random.Random(1)
    for _ in range(100):
        assert backoff_delay(20, base_seconds=2.0, max_seconds=30.0, rng=rng) <= 30.0


def test_backoff_deterministic_with_seed() -> None:
    a = backoff_delay(3, base_seconds=2.0, max_seconds=60.0, rng=random.Random(42))
    b = backoff_delay(3, base_seconds=2.0, max_seconds=60.0, rng=random.Random(42))
    assert a == b


def test_invalid_attempt_rejected() -> None:
    with pytest.raises(ValueError):
        backoff_delay(0, base_seconds=1, max_seconds=2)


@pytest.mark.parametrize(
    "exc,expected",
    [
        (AQLValidationError(["bad"]), ExecutionErrorType.VALIDATION),
        (ProviderAuthError("nope"), ExecutionErrorType.AUTH),
        (ArielTimeout("t"), ExecutionErrorType.TIMEOUT),
        (ArielSearchFailed("f"), ExecutionErrorType.SEARCH_FAILED),
        (ResultParsingError("p"), ExecutionErrorType.PARSING),
        (ProviderUnavailableError("net"), ExecutionErrorType.NETWORK),
        (ConnectionError("net"), ExecutionErrorType.NETWORK),
    ],
)
def test_classify(exc: BaseException, expected: ExecutionErrorType) -> None:
    assert classify(exc) == expected


def test_classify_http_status_codes() -> None:
    def _err(status: int) -> httpx.HTTPStatusError:
        req = httpx.Request("GET", "https://q/api")
        resp = httpx.Response(status, request=req)
        return httpx.HTTPStatusError("x", request=req, response=resp)

    assert classify(_err(401)) == ExecutionErrorType.AUTH
    assert classify(_err(429)) == ExecutionErrorType.RATE_LIMITED
    assert classify(_err(503)) == ExecutionErrorType.NETWORK
    assert classify(_err(400)) == ExecutionErrorType.SEARCH_FAILED


def test_retryable_partition() -> None:
    assert not is_retryable(ExecutionErrorType.VALIDATION)
    assert not is_retryable(ExecutionErrorType.AUTH)
    assert not is_retryable(ExecutionErrorType.PARSING)
    assert is_retryable(ExecutionErrorType.NETWORK)
    assert is_retryable(ExecutionErrorType.RATE_LIMITED)
    assert is_retryable(ExecutionErrorType.TIMEOUT)
