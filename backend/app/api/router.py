"""Top-level API router aggregating all versioned route modules."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import alerts, anomalies, health, log_sources, overview, searches

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(overview.router)
api_router.include_router(log_sources.router)
api_router.include_router(searches.router)
api_router.include_router(anomalies.router)
api_router.include_router(alerts.router)
