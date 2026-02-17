from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.db.models import DeliveryLog, NudgeLog
from celine.nudging.orchestrator.models import Channel, DeliveryJob
from celine.nudging.orchestrator.policies import can_send_today
from celine.nudging.orchestrator.preferences import get_user_pref
from celine.nudging.publishers.registry import get_publisher


async def orchestrate(db: AsyncSession, nudge_id: str) -> list[DeliveryJob]:
    # load nudge log (contains title/body in payload)
    res = await db.execute(select(NudgeLog).where(NudgeLog.id == nudge_id))
    n = res.scalar_one()

    pref = await get_user_pref(db, n.user_id)
    max_per_day = pref.max_per_day if pref else 3

    # count sent today (simple version: delivery_log status=sent)
    today = date.today()
    cnt_res = await db.execute(
        select(func.count(DeliveryLog.id)).where(
            DeliveryLog.status == "sent",
            DeliveryLog.destination.like(f"web:{n.user_id}%"),
            func.date(DeliveryLog.sent_at) == today,
        )
    )
    sent_today = int(cnt_res.scalar() or 0)

    # build job (sempre, così lo puoi loggare anche se suppressed)
    job = DeliveryJob(
        user_id=n.user_id,
        job_id=uuid4().hex,
        rule_id=n.rule_id,
        nudge_id=n.id,
        channel=Channel.web,
        destination=f"web:{n.user_id}",
        title=n.payload.get("title", ""),
        body=n.payload.get("body", ""),
        dedup_key=n.dedup_key,
    )

    # rate limit -> suppressed (MA lo registriamo a DB)
    if not can_send_today(sent_today, max_per_day):
        db.add(
            DeliveryLog(
                id=job.job_id,
                nudge_id=job.nudge_id,
                channel="web",
                destination=job.destination,
                status="suppressed",
                error="rate_limited",
                created_at=datetime.utcnow(),
                sent_at=None,
            )
        )
        n.status = "suppressed"
        await db.commit()
        return []

    publisher = get_publisher(job.channel)
    result = await publisher.send(db, job)

    # Nudge status coerente con esito delivery
    if result.status == "sent":
        n.status = "sent"
    elif result.status == "suppressed":
        n.status = "suppressed"
    else:
        n.status = "failed"

    await db.commit()
    return [job]
