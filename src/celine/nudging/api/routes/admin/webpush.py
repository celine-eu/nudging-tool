from __future__ import annotations

import json
import uuid
import logging

from fastapi import APIRouter, Depends, status
import requests
from pywebpush import WebPushException, webpush
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.api.schemas import (
    SendTestRequest,
    SendTestResponse,
)
from celine.nudging.config.settings import settings
from celine.nudging.db.models import WebPushSubscription
from celine.nudging.publishers.web.endpoint import (
    EndpointRefused,
    check_endpoint,
    push_session,
)
from celine.nudging.db.session import get_db
from celine.nudging.security.policies import require_admin
from celine.sdk.auth import JwtUser
from celine.nudging.utils import get_vapid

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/webpush/send-test",
    response_model=SendTestResponse,
    summary="Send a test push notification",
    description=(
        "Sends a test Web Push notification to all active subscriptions for a given user. "
        "Requires nudging.admin scope or platform-admin role. "
        "user_id is explicit in the body because an admin targets any user."
    ),
)
async def send_test(
    body: SendTestRequest,
    _admin: JwtUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SendTestResponse:
    filters = [
        WebPushSubscription.user_id == body.user_id,
        WebPushSubscription.enabled.is_(True),
    ]
    if body.community_id is not None:
        filters.append(
            or_(
                WebPushSubscription.community_id == body.community_id,
                WebPushSubscription.community_id.is_(None),
            )
        )

    q = await db.execute(select(WebPushSubscription).where(*filters))
    subs = q.scalars().all()
    if not subs:
        return SendTestResponse(status="no_subscriptions", sent=0, failed=0)

    payload = {"title": body.title, "body": body.body, "data": {"url": body.url}}

    vapid = get_vapid()
    signing_key = vapid.signing_key

    sent, failed = 0, 0
    session = push_session(settings)
    for s in subs:
        try:
            check_endpoint(s.endpoint, settings)  # REQ-0084
            webpush(
                subscription_info={
                    "endpoint": s.endpoint,
                    "keys": {"p256dh": s.p256dh, "auth": s.auth},
                },
                data=json.dumps(payload),
                vapid_private_key=signing_key,
                vapid_claims={"sub": vapid.subject},
                timeout=settings.WEBPUSH_TIMEOUT_SECONDS,
                requests_session=session,
            )
            sent += 1
        except EndpointRefused as e:
            failed += 1
            if e.permanent:
                s.enabled = False
        except requests.RequestException:
            failed += 1
        except WebPushException as e:
            failed += 1
            http_status = getattr(getattr(e, "response", None), "status_code", None)
            if http_status in (404, 410):
                s.enabled = False

    session.close()
    await db.commit()
    return SendTestResponse(status="ok", sent=sent, failed=failed)
