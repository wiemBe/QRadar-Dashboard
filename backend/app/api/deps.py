"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.providers.base import QRadarProvider
from app.providers.factory import get_provider
from app.security.auth import get_principal
from app.security.rbac import PermissionDenied, Principal, require_permission

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProviderDep = Annotated[QRadarProvider, Depends(get_provider)]
# Injected rather than imported at call sites so a test can override the
# guardrail values (page caps, SLA hours, health thresholds) per request.
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def _provider_dep() -> AsyncGenerator[QRadarProvider, None]:
    async for p in get_provider():
        yield p


def requires(permission: str):  # type: ignore[no-untyped-def]
    """Router-level dependency enforcing authentication *and* a permission.

    Applied at `include_router` rather than per-endpoint so that adding a route
    to a Phase 3 module cannot accidentally ship it unauthenticated: the guard
    is a property of the router, not something each handler must remember.

    Resolving `get_principal` is what enforces authentication -- under OIDC it
    raises 401 for a missing or unverifiable bearer token.
    """

    async def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        try:
            require_permission(principal, permission)
        except PermissionDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        return principal

    return _dep
