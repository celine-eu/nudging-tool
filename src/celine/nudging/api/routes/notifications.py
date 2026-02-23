"""User-facing notifications endpoints.

Queries the `notifications` table directly – NudgeLog is internal only.

GET    /notifications            – list own notifications (excludes soft-deleted)
PUT    /notifications/{id}       – mark as read (idempotent)
DELETE /notifications/{id}       – soft-delete
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.api.schemas import NotificationOut
from celine.nudging.db.models import Notification
from celine.nudging.db.session import get_db
from celine.nudging.security.policies import get_current_user
from celine.sdk.auth import JwtUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _get_own_notification(
    notification_id: str, user_id: str, db: AsyncSession
) -> Notification:
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )
    n = result.scalar_one_or_none()
    if n is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    return n


@router.get(
    "",
    response_model=list[NotificationOut],
    summary="List my notifications",
    description="Returns the caller's notifications ordered newest-first. Soft-deleted entries are always excluded.",
)
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=200, description="Max results to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    unread_only: bool = Query(
        default=False, description="Return only unread notifications"
    ),
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Notification]:
    q = (
        select(Notification)
        .where(Notification.user_id == user.sub, Notification.deleted_at.is_(None))
        .order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if unread_only:
        q = q.where(Notification.read_at.is_(None))

    result = await db.execute(q)
    return list(result.scalars().all())


@router.put(
    "/{notification_id}",
    response_model=NotificationOut,
    summary="Mark notification as read",
    description="Idempotent – safe to call multiple times.",
    responses={
        404: {"description": "Notification not found"},
        410: {"description": "Notification has been deleted"},
    },
)
async def mark_read(
    notification_id: str,
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Notification:
    n = await _get_own_notification(notification_id, user.sub, db)

    if n.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Notification has been deleted"
        )

    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(n)

    return n


@router.delete(
    "/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a notification",
    description="Soft-deletes. Idempotent – returns 204 even if already deleted.",
    responses={
        204: {"description": "Deleted (or was already deleted)"},
        404: {"description": "Notification not found or belongs to another user"},
    },
)
async def soft_delete_notification(
    notification_id: str,
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    n = await _get_own_notification(notification_id, user.sub, db)
    if n.deleted_at is None:
        n.deleted_at = datetime.now(timezone.utc)
        await db.commit()
