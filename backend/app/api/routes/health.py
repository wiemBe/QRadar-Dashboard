"""Liveness and readiness endpoints for container health checks."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    """Process is up. No dependencies checked — used as the container liveness
    probe so a slow DB does not trigger a restart loop."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: SessionDep) -> dict[str, str]:
    """Ready to serve traffic: database reachable."""
    await session.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}
