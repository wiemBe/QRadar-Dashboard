"""Inventory sync: pull log sources from a provider and upsert them.

Only QRadar-owned fields are ever overwritten. SOC-owned metadata (criticality,
owner, maintenance, thresholds) is never touched by a sync — clobbering an
operator's classification with each poll would make the platform unusable.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import InstanceStatus
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.providers.base import QRadarProvider
from app.repositories.log_source import LogSourceRepository
from app.schemas.log_source import SyncResult
from app.security.sanitizer import sanitize_text


class InventorySyncService:
    def __init__(self, session: AsyncSession, provider: QRadarProvider) -> None:
        self.session = session
        self.provider = provider
        self.repo = LogSourceRepository(session)

    async def sync(self, instance: QRadarInstance) -> SyncResult:
        info = await self.provider.get_instance_info()
        instance.qradar_version = info.version
        instance.status = (
            InstanceStatus.HEALTHY if info.reachable else InstanceStatus.UNREACHABLE
        )

        type_names = {t.type_id: t.name for t in await self.provider.list_log_source_types()}
        dtos = await self.provider.list_log_sources()

        created = updated = 0
        for dto in dtos:
            existing = await self.repo.get_by_qradar_id(instance.id, dto.qradar_id)
            resolved_type = dto.type_name or type_names.get(dto.type_id or -1)
            if existing is None:
                self.session.add(
                    LogSource(
                        instance_id=instance.id,
                        qradar_id=dto.qradar_id,
                        name=sanitize_text(dto.name) or dto.name,
                        description=sanitize_text(dto.description),
                        type_id=dto.type_id,
                        type_name=sanitize_text(resolved_type),
                        protocol_type_id=dto.protocol_type_id,
                        enabled=dto.enabled,
                        qradar_status=dto.status,
                        credibility=dto.credibility,
                        target_event_collector_id=dto.target_event_collector_id,
                        last_event_time=dto.last_event_time,
                    )
                )
                created += 1
            else:
                # QRadar-owned fields only.
                existing.name = sanitize_text(dto.name) or dto.name
                existing.description = sanitize_text(dto.description)
                existing.type_id = dto.type_id
                existing.type_name = sanitize_text(resolved_type)
                existing.protocol_type_id = dto.protocol_type_id
                existing.enabled = dto.enabled
                existing.qradar_status = dto.status
                existing.credibility = dto.credibility
                existing.target_event_collector_id = dto.target_event_collector_id
                existing.last_event_time = dto.last_event_time
                updated += 1

        await self.session.flush()
        return SyncResult(
            provider=type(self.provider).__name__,
            log_sources_seen=len(dtos),
            created=created,
            updated=updated,
            instance_version=info.version,
        )

    async def ensure_default_instance(self) -> QRadarInstance:
        """Return the singleton default instance, creating it if absent.

        In the MVP there is one monitored QRadar; multi-instance support exists
        in the schema but the API defaults to this one.
        """
        from sqlalchemy import select

        instance = await self.session.scalar(select(QRadarInstance).limit(1))
        if instance is not None:
            return instance
        instance = QRadarInstance(
            id=uuid.uuid4(),
            name="default",
            console_host="mock",
            provider_kind="mock",
        )
        self.session.add(instance)
        await self.session.flush()
        return instance
