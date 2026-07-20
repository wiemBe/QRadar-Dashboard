"""Map provider/transport failures to the ExecutionErrorType taxonomy.

The spec requires distinguishing validation, auth, rate-limiting, timeout, search
failure, network and parsing failures — because each drives different retry and
alerting behaviour. This module centralises that classification so the executor
and the provider stay decoupled.
"""

from __future__ import annotations

import httpx

from app.models.enums import ExecutionErrorType
from app.providers.base import ProviderAuthError, ProviderUnavailableError
from app.services.aql_validator import AQLValidationError


class ArielSearchFailed(RuntimeError):
    """QRadar reported the Ariel search itself entered an error state."""


class ArielTimeout(RuntimeError):
    """The search exceeded our execution timeout."""


class ResultParsingError(RuntimeError):
    """The provider returned results we could not normalise."""


# Failure modes that must NEVER be retried (they are deterministic).
NON_RETRYABLE = {
    ExecutionErrorType.VALIDATION,
    ExecutionErrorType.AUTH,
    ExecutionErrorType.PARSING,
    ExecutionErrorType.CANCELLED,
}


def classify(exc: BaseException) -> ExecutionErrorType:
    """Classify an exception into the execution error taxonomy."""
    if isinstance(exc, AQLValidationError):
        return ExecutionErrorType.VALIDATION
    if isinstance(exc, ProviderAuthError):
        return ExecutionErrorType.AUTH
    if isinstance(exc, ArielTimeout):
        return ExecutionErrorType.TIMEOUT
    if isinstance(exc, ArielSearchFailed):
        return ExecutionErrorType.SEARCH_FAILED
    if isinstance(exc, ResultParsingError):
        return ExecutionErrorType.PARSING
    if isinstance(exc, ProviderUnavailableError):
        return ExecutionErrorType.NETWORK
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return ExecutionErrorType.AUTH
        if status == 429:
            return ExecutionErrorType.RATE_LIMITED
        if status >= 500:
            return ExecutionErrorType.NETWORK
        return ExecutionErrorType.SEARCH_FAILED
    if isinstance(exc, (httpx.TimeoutException,)):
        return ExecutionErrorType.TIMEOUT
    if isinstance(exc, (httpx.TransportError, ConnectionError)):
        return ExecutionErrorType.NETWORK
    # Unknown — treat as network so it retries a bounded number of times rather
    # than being permanently non-retryable.
    return ExecutionErrorType.NETWORK


def is_retryable(error_type: ExecutionErrorType) -> bool:
    return error_type not in NON_RETRYABLE
