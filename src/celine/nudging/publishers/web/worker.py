from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.db.models import DeliveryLog, WebPushSubscription
from celine.nudging.orchestrator.models import DeliveryJob
from celine.nudging.publishers.base import Publisher, PublishResult
from celine.nudging.config.settings import settings

VAPID_SUBJECT = "mailto:you@celine.localhost"


def _get_vapid_private_key() -> str | None:
    key = settings.VAPID_PRIVATE_KEY
    if not key:
        return None

    # se la PEM è stata messa con \n letterali
    if "\\n" in key:
        key = key.replace("\\n", "\n")

    return key


class WebPublisher(Publisher):
    """
    Minimal publisher for 'web' channel.

    For now it just writes a DeliveryLog row with status='sent'.
    Later, you can extend it to call a websocket gateway / push service.
    """

    async def send(self, db: AsyncSession, job: DeliveryJob) -> PublishResult:
        return await send_webpush(db, job)


def _endpoint_suffix(endpoint: str) -> str:
    # suffix breve per destination; evita PII e stringhe enormi
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:12]


async def send_webpush(db: AsyncSession, job: DeliveryJob) -> PublishResult:
    now = datetime.now(timezone.utc)

    # Load enabled subscriptions
    result = await db.execute(
        select(WebPushSubscription).where(
            WebPushSubscription.user_id == job.user_id,
            WebPushSubscription.enabled.is_(True),
        )
    )
    subscriptions = list(result.scalars().all())

    sent = 0
    failed = 0
    last_error: str | None = None

    payload = {
        "title": job.title,
        "body": job.body,
        "data": {
            "url": getattr(job, "url", None) or "/",
            "nudge_id": job.nudge_id,
            "rule_id": job.rule_id,
        },
    }

    vapid_private_key = _get_vapid_private_key()

    if not vapid_private_key:
        last_error = "Missing VAPID_PRIVATE_KEY"
    elif not subscriptions:
        last_error = "no_subscriptions"
    else:
        for sub in subscriptions:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub.endpoint,
                        "keys": {
                            "p256dh": sub.p256dh,
                            "auth": sub.auth,
                        },
                    },
                    data=json.dumps(payload),
                    vapid_private_key=vapid_private_key,
                    vapid_claims={"sub": VAPID_SUBJECT},
                )
                sent += 1

            except WebPushException as e:
                failed += 1
                last_error = str(e)

                status_code = getattr(getattr(e, "response", None), "status_code", None)

                # Disable invalid subscriptions
                if status_code in (404, 410):
                    sub.enabled = False

        await db.commit()

    # Determine final status
    status = "sent" if sent > 0 else "failed"
    sent_at = now if sent > 0 else None

    destination = job.destination
    if subscriptions:
        destination = f"{job.destination}:multi"
    else:
        destination = f"{job.destination}:none"

    # Create delivery log (same style as send_web)
    db.add(
        DeliveryLog(
            id=getattr(job, "job_id", uuid4().hex),
            nudge_id=job.nudge_id,
            channel="webpush",
            destination=job.destination,
            # title=job.title,
            # body=job.body,
            status=status,
            created_at=now,
            sent_at=now if sent > 0 else None,
            # dedup_key=getattr(job, "dedup_key", None),
            error=last_error,
        )
    )

    await db.commit()
    return PublishResult(status=status, sent_at=sent_at, error=last_error)
