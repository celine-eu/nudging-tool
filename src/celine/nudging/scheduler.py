from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from celine.nudging.config.settings import settings
from celine.nudging.db.models import ScheduledEvent, utc_now
from celine.nudging.db.session import AsyncSessionLocal
from celine.nudging.engine.engine_service import EngineResultStatus, run_engine_batch
from celine.nudging.engine.rules.models import DigitalTwinEvent
from celine.nudging.orchestrator.orchestrator import orchestrate

logger = logging.getLogger(__name__)


async def process_due_scheduled_events(batch_size: int = 20) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ScheduledEvent)
            .where(
                ScheduledEvent.status == "pending",
                ScheduledEvent.trigger_at <= utc_now(),
            )
            .order_by(ScheduledEvent.trigger_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        events = result.scalars().all()

        for event in events:
            try:
                evt = DigitalTwinEvent(
                    event_type=event.event_type,
                    user_id=event.user_id,
                    community_id=event.community_id,
                    facts=dict(event.facts or {}),
                )
                results = await run_engine_batch(evt, db)
                created = [
                    row
                    for row in results
                    if row.status == EngineResultStatus.CREATED and row.nudge
                ]
                for row in created:
                    if row.nudge is not None:
                        await orchestrate(db, row.nudge.nudge_id)

                event.status = "dispatched"
                event.dispatched_at = utc_now()
                event.last_error = None
            except Exception as exc:
                logger.exception(
                    "Scheduled event dispatch failed for id=%s", event.id
                )
                event.status = "failed"
                event.last_error = str(exc)

        if events:
            await db.commit()


async def run_scheduler(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await process_due_scheduled_events()
        except Exception:
            logger.exception("Scheduled event polling failed")

        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.SCHEDULER_POLL_SECONDS
            )
        except TimeoutError:
            continue
