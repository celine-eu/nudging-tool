"""Admin notifications endpoint.

Requires nudging.admin scope or admin group membership (enforced via require_admin dep).
Service accounts with nudging.admin scope can query any user's notifications.

GET /admin/notifications   – list notifications, filterable by user_id, includes deleted
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.security.policies import require_admin
from celine.nudging.db.models import NudgeLog
from celine.nudging.db.session import get_db
from celine.sdk.auth import JwtUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/notifications", tags=["admin"])


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class AdminNotificationOut(BaseModel):
    id: str
    rule_id: str
    user_id: str
    status: str
    payload: dict[str, Any]
    created_at: datetime
    read_at: datetime | None
    deleted_at: datetime | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[AdminNotificationOut])
async def admin_list_notifications(
    user_id: str | None = Query(default=None, description="Filter by user ID"),
    include_deleted: bool = Query(
        default=False, description="Include soft-deleted notifications"
    ),
    unread_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _admin: JwtUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[NudgeLog]:
    """Admin view of notifications. Filterable by user_id. Includes soft-deleted when requested."""
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
