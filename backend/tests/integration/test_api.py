"""API-level tests.

The liveness test needs no database and always runs. The overview/inventory
tests drive the real app against Postgres via a dependency override, and skip
without TEST_DATABASE_URL.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_liveness_needs_no_dependencies() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_security_headers_present() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/api/v1/health/live")
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.integration
def test_inventory_and_overview_flow(db_session) -> None:
    """Full flow: sync inventory, list it, read the overview.

    Uses the same engine as db_session by overriding get_session to bind to the
    test database.
    """
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.database import get_session

    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _override

    with TestClient(app) as client:
        synced = client.post("/api/v1/log-sources/sync")
        assert synced.status_code == 200
        assert synced.json()["created"] > 0

        listing = client.get("/api/v1/log-sources")
        assert listing.status_code == 200
        assert len(listing.json()) == synced.json()["log_sources_seen"]

        overview = client.get("/api/v1/overview")
        assert overview.status_code == 200
        body = overview.json()
        assert body["log_sources"]["total_log_sources"] > 0
