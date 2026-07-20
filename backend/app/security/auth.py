"""Authentication abstraction (OIDC-compatible).

Resolves the request Principal. Two backends:

  * local  — a development/service principal. In non-production with no bearer
    token, a full-permission local principal is returned so the API is usable
    out of the box. This is refused in production.
  * oidc   — validates a bearer JWT against the configured issuer. The full JWKS
    validation is completed in Phase 4; the seam and the Principal mapping exist
    now so routes can depend on authorization today.

Routes depend on `get_principal`; tests override it to inject a Principal with a
specific permission set, which is how RBAC is exercised without a real IdP.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings
from app.security.rbac import Principal

_LOCAL_DEV_PERMISSIONS = frozenset({"admin:*"})
_LOCAL_READONLY_PERMISSIONS = frozenset({"read:*"})


async def get_principal(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if settings.auth_provider == "local":
        return _local_principal(authorization, settings)
    return await _oidc_principal(authorization, settings)


def _local_principal(authorization: str | None, settings: Settings) -> Principal:
    if settings.is_production:
        # Local auth is a dev convenience; production must use OIDC.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "local auth is disabled in production; configure OIDC",
        )
    # Dev: unauthenticated calls get an admin principal so the UI works locally.
    if not authorization:
        return Principal(subject="local-dev", email="dev@localhost",
                         permissions=_LOCAL_DEV_PERMISSIONS)
    # A bearer token in dev is treated as a read-only principal, letting tests
    # and demos exercise the "insufficient permission" path deterministically.
    return Principal(subject="local-token", permissions=_LOCAL_READONLY_PERMISSIONS)


async def _oidc_principal(authorization: str | None, settings: Settings) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    # Phase 4: verify signature against issuer JWKS, check aud/exp, map claims →
    # permissions via role mapping. Until then, refuse rather than trust.
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "OIDC verification is implemented in Phase 4",
    )
