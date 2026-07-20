"""Top-level API router aggregating all versioned route modules."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import requires
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
from app.security.rbac import (
    PERM_COVERAGE_READ,
    PERM_OFFENSE_READ,
    PERM_PROVIDER_READ,
    PERM_RULE_READ,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(overview.router)
api_router.include_router(log_sources.router)
api_router.include_router(searches.router)
api_router.include_router(anomalies.router)
api_router.include_router(alerts.router)
# --- Phase 3 ---
# Guarded at the router. These endpoints expose offence records (parsed
# usernames, source addresses, analyst assignment) and a map of where detection
# coverage is absent; neither may reach an unauthenticated caller. The `read:*`
# wildcard satisfies all four, so existing read-only roles are unaffected.
api_router.include_router(
    offenses.router, dependencies=[Depends(requires(PERM_OFFENSE_READ))]
)
api_router.include_router(rules.router, dependencies=[Depends(requires(PERM_RULE_READ))])
api_router.include_router(
    coverage.router, dependencies=[Depends(requires(PERM_COVERAGE_READ))]
)
api_router.include_router(
    providers.router, dependencies=[Depends(requires(PERM_PROVIDER_READ))]
)
