"""Admin notifications endpoint.

Requires nudging.admin scope or admin group membership (enforced via require_admin dep).
Service accounts with nudging.admin scope can query any user's notifications.

GET /admin/notifications – list notifications, filterable by user_id, includes deleted
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.api.schemas import AdminNotificationOut
from celine.nudging.db.models import NudgeLog
from celine.nudging.db.session import get_db
from celine.nudging.security.policies import require_admin
from celine.sdk.auth import JwtUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/notifications", tags=["admin"])


@router.get(
    "",
    response_model=list[AdminNotificationOut],
    summary="List notifications (admin)",
    description=(
        "Admin view of all notifications. Optionally filter by user, include soft-deleted, "
        "or restrict to unread only. Requires `nudging.admin` scope or `admin` group."
    ),
)
async def admin_list_notifications(
    user_id: str | None = Query(default=None, description="Filter by user ID"),
    include_deleted: bool = Query(
        default=False, description="Include soft-deleted notifications"
    ),
    unread_only: bool = Query(
        default=False, description="Return only unread notifications"
    ),
    limit: int = Query(default=100, ge=1, le=500, description="Max results to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    _admin: JwtUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[NudgeLog]:
    q = (
        select(NudgeLog)
        .order_by(NudgeLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    if user_id is not None:
        q = q.where(NudgeLog.user_id == user_id)

    if not include_deleted:
        q = q.where(NudgeLog.deleted_at.is_(None))

    if unread_only:
        q = q.where(NudgeLog.read_at.is_(None))

    result = await db.execute(q)
    return list(result.scalars().all())
