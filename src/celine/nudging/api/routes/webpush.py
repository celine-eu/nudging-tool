from __future__ import annotations

import json
import os
import uuid

from fastapi import APIRouter, Depends, Request
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.db.models import WebPushSubscription
from celine.nudging.db.session import get_db

router = APIRouter(prefix="/webpush", tags=["webpush"])


def _vapid_public_key() -> str:
    return (os.getenv("VAPID_PUBLIC_KEY") or "").strip()


def _vapid_private_key() -> str:
    return (os.getenv("VAPID_PRIVATE_KEY") or "").strip()


def _vapid_subject() -> str:
    return (os.getenv("VAPID_SUBJECT") or "mailto:test@example.com").strip()


@router.get("/vapid-public-key")
async def vapid_public_key():
    return {"public_key": _vapid_public_key()}


@router.post("/subscribe", response_model=None)
async def subscribe(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    # Per test: prendo user_id dal body (in prod: da auth)
    user_id = body["user_id"]
    community_id = body.get("community_id")
    sub = body["subscription"]

    endpoint = sub["endpoint"]
    p256dh = sub["keys"]["p256dh"]
    auth = sub["keys"]["auth"]

    filters = [
        WebPushSubscription.user_id == user_id,
        WebPushSubscription.endpoint == endpoint,
    ]
    if community_id is None:
        filters.append(WebPushSubscription.community_id.is_(None))
    else:
        filters.append(WebPushSubscription.community_id == community_id)

    q = await db.execute(select(WebPushSubscription).where(*filters))
    row = q.scalar_one_or_none()

    if row is None:
        row = WebPushSubscription(
            id=str(uuid.uuid4()),
            user_id=user_id,
            community_id=community_id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            enabled=True,
        )
        db.add(row)
    else:
        row.community_id = community_id
        row.p256dh = p256dh
        row.auth = auth
        row.enabled = True

    await db.commit()
    return {"status": "ok"}


@router.post("/unsubscribe", response_model=None)
async def unsubscribe(body: dict, db: AsyncSession = Depends(get_db)):
    user_id = body["user_id"]
    endpoint = body["endpoint"]

    filters = [
        WebPushSubscription.user_id == user_id,
        WebPushSubscription.endpoint == endpoint,
    ]
    if "community_id" in body:
        filters.append(WebPushSubscription.community_id == body.get("community_id"))

    q = await db.execute(select(WebPushSubscription).where(*filters))
    rows = q.scalars().all()
    for row in rows:
        row.enabled = False
    if rows:
        await db.commit()

    return {"status": "ok"}


@router.post("/send-test", response_model=None)
async def send_test(body: dict, db: AsyncSession = Depends(get_db)):
    user_id = body["user_id"]
    community_id = body.get("community_id")
    title = body.get("title", "Test")
    msg = body.get("body", "Hello!")
    url = body.get("url", "/")

    # carica subscription
    filters = [
        WebPushSubscription.user_id == user_id,
        WebPushSubscription.enabled.is_(True),
    ]
    if community_id is not None:
        filters.append(WebPushSubscription.community_id == community_id)
    q = await db.execute(select(WebPushSubscription).where(*filters))
    subs = q.scalars().all()
    if not subs:
        return {"status": "no_subscriptions"}

    payload = {"title": title, "body": msg, "data": {"url": url}}

    sent, failed = 0, 0
    for s in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": s.endpoint,
                    "keys": {"p256dh": s.p256dh, "auth": s.auth},
                },
                data=json.dumps(payload),
                vapid_private_key=_vapid_private_key(),
                vapid_claims={"sub": _vapid_subject()},
            )
            sent += 1
        except WebPushException as e:
            failed += 1
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                s.enabled = False

    await db.commit()
    return {"status": "ok", "sent": sent, "failed": failed}
