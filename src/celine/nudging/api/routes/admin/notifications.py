"""Admin notifications endpoint – queries the Notification table."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.api.schemas import AdminNotificationOut
from celine.nudging.db.models import Notification
from celine.nudging.db.session import get_db
from celine.nudging.security.policies import require_admin
from celine.sdk.auth import JwtUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["admin"])


@router.get(
    "",
    response_model=list[AdminNotificationOut],
    summary="List notifications (admin)",
    description=(
        "Admin view of all notifications. Filterable by user_id, family, severity. "
        "Requires nudging.admin scope or admin group."
    ),
)
async def admin_list_notifications(
    user_id: str | None = Query(default=None, description="Filter by user ID"),
    family: str | None = Query(
        default=None, description="Filter by family (energy, onboarding, …)"
    ),
    severity: str | None = Query(
        default=None, description="Filter by severity (info, warning, critical)"
    ),
    include_deleted: bool = Query(
        default=False, description="Include soft-deleted notifications"
    ),
    unread_only: bool = Query(
        default=False, description="Return only unread notifications"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _admin: JwtUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[Notification]:
    q = (
        select(Notification)
        .order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    if user_id is not None:
        q = q.where(Notification.user_id == user_id)
    if family is not None:
        q = q.where(Notification.family == family)
    if severity is not None:
        q = q.where(Notification.severity == severity)
    if not include_deleted:
        q = q.where(Notification.deleted_at.is_(None))
    if unread_only:
        q = q.where(Notification.read_at.is_(None))

    result = await db.execute(q)
    return list(result.scalars().all())
