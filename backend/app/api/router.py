"""Top-level API router aggregating all versioned route modules."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    alerts,
    anomalies,
    coverage,
    health,
    log_sources,
    offenses,
    overview,
    providers,
    rules,
    searches,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(overview.router)
api_router.include_router(log_sources.router)
api_router.include_router(searches.router)
api_router.include_router(anomalies.router)
api_router.include_router(alerts.router)
# --- Phase 3 ---
api_router.include_router(offenses.router)
api_router.include_router(rules.router)
api_router.include_router(coverage.router)
api_router.include_router(providers.router)
