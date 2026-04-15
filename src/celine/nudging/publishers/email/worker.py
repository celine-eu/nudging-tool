from __future__ import annotations

import asyncio
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from ssl import create_default_context

from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.config.settings import settings
from celine.nudging.db.models import DeliveryLog
from celine.nudging.orchestrator.models import DeliveryJob
from celine.nudging.publishers.base import PublishResult, Publisher


class EmailPublisher(Publisher):
    async def send(self, db: AsyncSession, job: DeliveryJob) -> PublishResult:
        return await send_email(db, job)


def _send_email_sync(job: DeliveryJob) -> None:
    if not settings.SMTP_HOST:
        raise RuntimeError("Missing SMTP_HOST")
    if not settings.EMAIL_FROM:
        raise RuntimeError("Missing EMAIL_FROM")

    msg = EmailMessage()
    msg["Subject"] = job.title
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = job.destination
    msg.set_content(job.body)

    if settings.SMTP_USE_SSL:
        with smtplib.SMTP_SSL(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            context=create_default_context(),
        ) as smtp:
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(msg)
        return

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
        if settings.SMTP_USE_TLS:
            smtp.starttls(context=create_default_context())
        if settings.SMTP_USERNAME:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(msg)


async def send_email(db: AsyncSession, job: DeliveryJob) -> PublishResult:
    now = datetime.now(timezone.utc)
    status = "sent"
    sent_at = now
    error: str | None = None

    try:
        await asyncio.to_thread(_send_email_sync, job)
    except Exception as exc:
        status = "failed"
        sent_at = None
        error = str(exc)

    db.add(
        DeliveryLog(
            id=job.job_id,
            nudge_id=job.nudge_id,
            channel="email",
            destination=job.destination,
            status=status,
            error=error,
            created_at=now,
            sent_at=sent_at,
        )
    )
    await db.commit()
    return PublishResult(status=status, sent_at=sent_at, error=error)
