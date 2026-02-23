from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.db.models import DeliveryLog, Notification, NudgeLog, utc_now
from celine.nudging.orchestrator.models import Channel, DeliveryJob
from celine.nudging.orchestrator.policies import can_send_today
from celine.nudging.orchestrator.preferences import get_user_pref
from celine.nudging.publishers.registry import get_publisher


async def orchestrate(db: AsyncSession, nudge_id: str) -> list[DeliveryJob]:
    # Load the audit log row (contains rule_id, user_id, dedup_key)
    nudge_log_res = await db.execute(select(NudgeLog).where(NudgeLog.id == nudge_id))
    n = nudge_log_res.scalar_one()

    # Load the linked notification (title, body, status live here now)
    notif_res = await db.execute(
        select(Notification).where(Notification.nudge_log_id == nudge_id)
    )
    notification = notif_res.scalar_one()

    pref = await get_user_pref(db, n.user_id, n.community_id)
    max_per_day = pref.max_per_day if pref else 3

    today = date.today()
    if n.community_id:
        dest_prefix = f"web:{n.user_id}:{n.community_id}"
    else:
        dest_prefix = f"web:{n.user_id}"
    cnt_res = await db.execute(
        select(func.count(DeliveryLog.id)).where(
            DeliveryLog.status == "sent",
            DeliveryLog.destination.like(f"{dest_prefix}%"),
            func.date(DeliveryLog.sent_at) == today,
        )
    )
    sent_today = int(cnt_res.scalar() or 0)

    job = DeliveryJob(
        user_id=n.user_id,
        community_id=n.community_id,
        job_id=uuid4().hex,
        rule_id=n.rule_id,
        nudge_id=n.id,
        channel=Channel.web,
        destination=dest_prefix,
        title=notification.title,
        body=notification.body,
        dedup_key=n.dedup_key,
    )

    if not can_send_today(sent_today, max_per_day):
        db.add(
            DeliveryLog(
                id=job.job_id,
                nudge_id=job.nudge_id,
                channel="web",
                destination=job.destination,
                status="suppressed",
                error="rate_limited",
                created_at=utc_now,
                sent_at=None,
            )
        )
        notification.status = "suppressed"
        await db.commit()
        return []

    publisher = get_publisher(job.channel)
    result = await publisher.send(db, job)

    if result.status == "sent":
        notification.status = "sent"
    elif result.status == "suppressed":
        notification.status = "suppressed"
    else:
        notification.status = "failed"

    await db.commit()
    return [job]
