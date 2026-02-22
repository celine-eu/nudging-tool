from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.api.schemas import (
    StatusResponse,
    SubscribeRequest,
    UnsubscribeRequest,
    VapidPublicKeyResponse,
)
from celine.nudging.db.models import WebPushSubscription
from celine.nudging.db.session import get_db
from celine.nudging.config.settings import settings
from celine.nudging.security.policies import get_current_user
from celine.sdk.auth.jwt import JwtUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webpush", tags=["webpush"])


@router.get(
    "/vapid-public-key",
    response_model=VapidPublicKeyResponse,
    summary="Get VAPID public key",
    description="Returns the VAPID public key needed by the browser to set up a push subscription.",
)
async def vapid_public_key(
    user: JwtUser = Depends(get_current_user),
) -> VapidPublicKeyResponse:
    public_key = settings.VAPID_PUBLIC_KEY.strip()
    return VapidPublicKeyResponse(public_key=public_key)


@router.post(
    "/subscribe",
    response_model=StatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Register a push subscription",
    description=(
        "Registers or updates a Web Push subscription for the authenticated user. "
        "The user identity is taken from the JWT – callers cannot register on behalf of others. "
        "If the endpoint already exists for that user, its keys are refreshed."
    ),
)
async def subscribe(
    body: SubscribeRequest,
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StatusResponse:
    q = await db.execute(
        select(WebPushSubscription).where(
            WebPushSubscription.user_id == user.sub,
            WebPushSubscription.endpoint == body.subscription.endpoint,
        )
    )
    row = q.scalar_one_or_none()

    if row is None:
        row = WebPushSubscription(
            id=str(uuid.uuid4()),
            user_id=user.sub,
            endpoint=body.subscription.endpoint,
            p256dh=body.subscription.keys.p256dh,
            auth=body.subscription.keys.auth,
            enabled=True,
        )
        db.add(row)
    else:
        row.p256dh = body.subscription.keys.p256dh
        row.auth = body.subscription.keys.auth
        row.enabled = True

    await db.commit()
    return StatusResponse(status="ok")


@router.post(
    "/unsubscribe",
    response_model=StatusResponse,
    summary="Remove a push subscription",
    description=(
        "Disables the push subscription identified by endpoint for the authenticated user. "
        "The user identity is taken from the JWT – callers cannot remove others' subscriptions."
    ),
)
async def unsubscribe(
    body: UnsubscribeRequest,
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StatusResponse:
    q = await db.execute(
        select(WebPushSubscription).where(
            WebPushSubscription.user_id == user.sub,
            WebPushSubscription.endpoint == body.endpoint,
        )
    )
    row = q.scalar_one_or_none()
    if row:
        row.enabled = False
        await db.commit()

    return StatusResponse(status="ok")
