"""User-facing notifications endpoints.

All routes require a valid user token (enforced by AuthMiddleware).
Operations are scoped to the authenticated user's own notifications only.

GET    /notifications          – list own notifications (excludes soft-deleted)
PUT    /notifications/{id}     – mark as read (idempotent)
DELETE /notifications/{id}     – soft-delete
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.security.policies import get_current_user
from celine.nudging.db.models import NudgeLog
from celine.nudging.db.session import get_db
from celine.sdk.auth import JwtUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class NotificationOut(BaseModel):
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
# Helpers
# ---------------------------------------------------------------------------


async def _get_own_nudge(
    nudge_id: str,
    user_id: str,
    db: AsyncSession,
) -> NudgeLog:
    """Fetch a NudgeLog row that belongs to the current user, or raise 404."""
    result = await db.execute(
        select(NudgeLog).where(
            NudgeLog.id == nudge_id,
            NudgeLog.user_id == user_id,
        )
    )
    nudge = result.scalar_one_or_none()
    if nudge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    return nudge


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    unread_only: bool = Query(default=False),
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[NudgeLog]:
    """Return the caller's notifications, excluding soft-deleted ones."""
    q = (
        select(NudgeLog)
        .where(
            NudgeLog.user_id == user.sub,
            NudgeLog.deleted_at.is_(None),
        )
        .order_by(NudgeLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if unread_only:
        q = q.where(NudgeLog.read_at.is_(None))

    result = await db.execute(q)
    return list(result.scalars().all())


@router.put("/{nudge_id}", response_model=NotificationOut)
async def mark_read(
    nudge_id: str,
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NudgeLog:
    """Mark a notification as read. Idempotent – succeeds even if already read."""
    nudge = await _get_own_nudge(nudge_id, user.sub, db)

    if nudge.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Notification has been deleted",
        )

    if nudge.read_at is None:
        nudge.read_at = datetime.utcnow()
        await db.commit()
        await db.refresh(nudge)

    return nudge


@router.delete("/{nudge_id}", status_code=status.HTTP_204_NO_CONTENT)
async def soft_delete_notification(
    nudge_id: str,
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a notification. Idempotent."""
    nudge = await _get_own_nudge(nudge_id, user.sub, db)

    if nudge.deleted_at is None:
        nudge.deleted_at = datetime.utcnow()
        await db.commit()
