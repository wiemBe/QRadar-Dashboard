"""Seed data for development.

Creates the default RBAC roles, an admin user, the default QRadar instance, and
runs one inventory sync via the configured provider (mock by default). Idempotent
— safe to run repeatedly.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.database import get_sessionmaker
from app.models.identity import Role, User
from app.providers.factory import build_provider
from app.services.inventory_sync import InventorySyncService

_ROLES = {
    "admin": ["admin:*", "search:execute", "search:write", "alert:ack", "alert:resolve"],
    "analyst": ["search:execute", "alert:ack", "alert:resolve"],
    "viewer": ["read:*"],
}


async def seed() -> None:
    maker = get_sessionmaker()
    async with maker() as session:
        for name, perms in _ROLES.items():
            existing = await session.scalar(select(Role).where(Role.name == name))
            if existing is None:
                session.add(Role(name=name, permissions=perms))
        await session.flush()

        admin_role = await session.scalar(select(Role).where(Role.name == "admin"))
        admin = await session.scalar(select(User).where(User.email == "admin@example.internal"))
        if admin is None and admin_role is not None:
            session.add(
                User(
                    email="admin@example.internal",
                    display_name="Seed Admin",
                    roles=[admin_role],
                )
            )

        provider = build_provider()
        try:
            svc = InventorySyncService(session, provider)
            instance = await svc.ensure_default_instance()
            result = await svc.sync(instance)
        finally:
            await provider.aclose()

        await session.commit()
        print(  # CLI feedback
            f"seed complete: {result.created} created, {result.updated} updated "
            f"via {result.provider}"
        )


if __name__ == "__main__":
    asyncio.run(seed())
