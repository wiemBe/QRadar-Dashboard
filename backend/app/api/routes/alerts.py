"""Alert lifecycle and notification-history API."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.routing import default_policy
from app.alerts.service import AlertResult, AlertService
from app.api.deps import SessionDep
from app.models.alert import Alert, AlertNotification
from app.models.enums import AlertStatus
from app.repositories.audit import record_audit
from app.schemas.alert import AlertActionIn, AlertOut, NotificationOut
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_ALERT_ACK,
    PERM_ALERT_RESOLVE,
    PermissionDenied,
    Principal,
    require_permission,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _guard(principal: Principal, permission: str) -> None:
    try:
        require_permission(principal, permission)
    except PermissionDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


async def _enqueue_transition(session: AsyncSession, result: AlertResult) -> None:
    """Record the notification intent for an operator-driven state change.

    Enqueue-only: no notifier map is built here, so an HTTP request never blocks
    on an outbound webhook. The dispatch_notifications worker delivers the rows.
    A no-op transition (a repeated acknowledge) has transition=None and enqueues
    nothing, which is the same dedup rule the anomaly engine follows.
    """
    if result.transition is None:
        return
    dispatcher = NotificationDispatcher(session, notifiers={}, policy=default_policy())
    await dispatcher.enqueue(result.alert, result.transition)


@router.get("", response_model=list[AlertOut])
async def list_alerts(
    session: SessionDep,
    status_filter: AlertStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AlertOut]:
    stmt = select(Alert).order_by(Alert.opened_at.desc()).limit(limit)
    if status_filter is not None:
        stmt = stmt.where(Alert.status == status_filter)
    rows = (await session.scalars(stmt)).all()
    return [AlertOut.model_validate(r) for r in rows]


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: uuid.UUID, session: SessionDep) -> AlertOut:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    return AlertOut.model_validate(alert)


@router.get("/{alert_id}/notifications", response_model=list[NotificationOut])
async def alert_notifications(alert_id: uuid.UUID, session: SessionDep) -> list[NotificationOut]:
    rows = (
        await session.scalars(
            select(AlertNotification)
            .where(AlertNotification.alert_id == alert_id)
            .order_by(AlertNotification.created_at.desc())
        )
    ).all()
    return [NotificationOut.model_validate(r) for r in rows]


@router.post("/{alert_id}/acknowledge", response_model=AlertOut)
async def acknowledge_alert(
    alert_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> AlertOut:
    _guard(principal, PERM_ALERT_ACK)
    try:
        result = await AlertService(session).acknowledge(alert_id, actor=principal.subject)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _enqueue_transition(session, result)
    await record_audit(
        session, action="alert.acknowledge", actor=principal.subject,
        object_type="alert", object_id=alert_id,
    )
    return AlertOut.model_validate(result.alert)


@router.post("/{alert_id}/resolve", response_model=AlertOut)
async def resolve_alert(
    alert_id: uuid.UUID, payload: AlertActionIn, session: SessionDep, principal: PrincipalDep
) -> AlertOut:
    _guard(principal, PERM_ALERT_RESOLVE)
    try:
        result = await AlertService(session).resolve(
            alert_id, actor=principal.subject, reason=payload.reason
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _enqueue_transition(session, result)
    await record_audit(
        session, action="alert.resolve", actor=principal.subject,
        object_type="alert", object_id=alert_id, detail={"reason": payload.reason},
    )
    return AlertOut.model_validate(result.alert)
