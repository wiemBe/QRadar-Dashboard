"""Shared test fixtures.

Test-safe secrets are injected into the environment before any application
module imports Settings. Integration fixtures that need PostgreSQL skip cleanly
when TEST_DATABASE_URL is unset, so the unit suite runs anywhere.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("QRADAR_PROVIDER", "mock")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long!!")
# Deterministic Fernet key so encrypted-column tests are reproducible.
os.environ.setdefault("ENCRYPTION_KEY", "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A=")

import pytest
import pytest_asyncio

from app.providers.mock import MockQRadarProvider

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
requires_db = pytest.mark.skipif(
    TEST_DB_URL is None,
    reason="set TEST_DATABASE_URL to a PostgreSQL+TimescaleDB instance to run integration tests",
)


@pytest.fixture
def mock_provider() -> MockQRadarProvider:
    return MockQRadarProvider(seed=1337)


@pytest_asyncio.fixture
async def db_session():
    """Async session against a real Postgres. Creates the schema from model
    metadata, yields a session, drops it afterwards."""
    if TEST_DB_URL is None:
        pytest.skip("TEST_DATABASE_URL not set")

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base

    engine = create_async_engine(TEST_DB_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
