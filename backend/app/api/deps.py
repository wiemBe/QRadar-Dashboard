"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.providers.base import QRadarProvider
from app.providers.factory import get_provider

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProviderDep = Annotated[QRadarProvider, Depends(get_provider)]
# Injected rather than imported at call sites so a test can override the
# guardrail values (page caps, SLA hours, health thresholds) per request.
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def _provider_dep() -> AsyncGenerator[QRadarProvider, None]:
    async for p in get_provider():
        yield p
