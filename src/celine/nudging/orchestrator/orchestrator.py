from __future__ import annotations

import re
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.db.models import DeliveryLog, Notification, NudgeLog, utc_now
from celine.nudging.orchestrator.models import Channel, DeliveryJob
from celine.nudging.orchestrator.policies import can_send_today
from celine.nudging.orchestrator.preferences import (
    get_enabled_notification_kinds,
    get_rule_kind,
    get_user_pref,
)
from celine.nudging.publishers.registry import get_publisher

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _explicit_email_recipients(n: NudgeLog) -> list[str]:
    payload = n.payload or {}
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        return []

    raw = facts.get("email_recipients")
    if not isinstance(raw, list):
        return []

    recipients: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        email = item.strip()
        if not email or not _EMAIL_RE.match(email):
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        recipients.append(email)
    return recipients


def _is_email_only_ingest(n: NudgeLog, explicit_recipients: list[str]) -> bool:
    return bool(explicit_recipients) and str(n.user_id).startswith("email-ingest:")


def _build_delivery_jobs(
    n: NudgeLog, notification: Notification, pref
) -> list[DeliveryJob]:
    explicit_recipients = _explicit_email_recipients(n)
    jobs: list[DeliveryJob] = []

    if not _is_email_only_ingest(n, explicit_recipients):
        jobs.append(
            DeliveryJob(
                user_id=n.user_id,
                community_id=n.community_id,
                job_id=uuid4().hex,
                rule_id=n.rule_id,
                nudge_id=n.id,
                channel=Channel.web,
                destination=f"web:{n.user_id}:{n.community_id}" if n.community_id else f"web:{n.user_id}",
                title=notification.title,
                body=notification.body,
                dedup_key=n.dedup_key,
            )
        )

    if explicit_recipients:
        email_recipients = explicit_recipients
    elif pref and pref.channel_email and pref.email:
        email_recipients = [pref.email]
    else:
        email_recipients = []

    for recipient in email_recipients:
        jobs.append(
            DeliveryJob(
                user_id=n.user_id,
                community_id=n.community_id,
                job_id=uuid4().hex,
                rule_id=n.rule_id,
                nudge_id=n.id,
                channel=Channel.email,
                destination=recipient,
                title=notification.title,
                body=notification.body,
                dedup_key=n.dedup_key,
            )
        )

    return jobs


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
    enabled_kinds = set(get_enabled_notification_kinds(pref))
    rule_kind = await get_rule_kind(db, n.rule_id)

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
    jobs = _build_delivery_jobs(n, notification, pref)

    if rule_kind and rule_kind not in enabled_kinds:
        for job in jobs:
            db.add(
                DeliveryLog(
                    id=job.job_id,
                    nudge_id=job.nudge_id,
                    channel=job.channel.value,
                    destination=job.destination,
                    status="suppressed",
                    error="kind_disabled",
                    created_at=utc_now(),
                    sent_at=None,
                )
            )
        notification.status = "suppressed"
        await db.commit()
        return []

    if not can_send_today(sent_today, max_per_day):
        for job in jobs:
            db.add(
                DeliveryLog(
                    id=job.job_id,
                    nudge_id=job.nudge_id,
                    channel=job.channel.value,
                    destination=job.destination,
                    status="suppressed",
                    error="rate_limited",
                    created_at=utc_now(),
                    sent_at=None,
                )
            )
        notification.status = "suppressed"
        await db.commit()
        return []

    results = []
    for job in jobs:
        publisher = get_publisher(job.channel)
        result = await publisher.send(db, job)
        results.append(result.status)

    if any(status == "sent" for status in results):
        notification.status = "sent"
    elif results and all(status == "suppressed" for status in results):
        notification.status = "suppressed"
    else:
        notification.status = "failed"

    await db.commit()
    return jobs
