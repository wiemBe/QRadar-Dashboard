"""RBAC on search and alert endpoints. DB-gated (writes go to the database)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import create_app
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run API permission integration tests",
    ),
]

VALID_SEARCH = {
    "name": "perm-search",
    "aql_query": "SELECT sourceip FROM events LAST 1 HOURS",
    "schedule_cron": "*/5 * * * *",
}


def _client_with(principal: Principal) -> TestClient:
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def test_viewer_cannot_create_search() -> None:
    client = _client_with(Principal(subject="viewer", permissions=frozenset({"read:*"})))
    resp = client.post("/api/v1/searches", json=VALID_SEARCH)
    assert resp.status_code == 403


def test_admin_can_create_search() -> None:
    client = _client_with(Principal(subject="admin", permissions=frozenset({"admin:*"})))
    resp = client.post("/api/v1/searches", json=VALID_SEARCH)
    assert resp.status_code == 201, resp.text


def test_analyst_can_execute_but_not_write() -> None:
    analyst = Principal(subject="analyst", permissions=frozenset({"search:execute"}))
    client = _client_with(analyst)
    # Cannot create (needs search:write)...
    assert client.post("/api/v1/searches", json=VALID_SEARCH).status_code == 403


def test_unsafe_aql_rejected_at_create(monkeypatch) -> None:
    client = _client_with(Principal(subject="admin", permissions=frozenset({"admin:*"})))
    bad = {**VALID_SEARCH, "name": "unsafe", "aql_query": "SELECT * FROM events; DROP TABLE x"}
    resp = client.post("/api/v1/searches", json=bad)
    assert resp.status_code == 422  # schema validation rejects unsafe AQL
