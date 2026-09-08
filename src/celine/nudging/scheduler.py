from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, update

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
        # Copy every field out of the ORM rows *before* the engine runs. The engine
        # commits and, on a dedup collision, rolls the shared session back — and a
        # rollback expires every loaded instance. Reading an expired attribute
        # afterwards is a synchronous refresh from inside the event loop, which
        # SQLAlchemy's asyncio layer refuses (MissingGreenlet). That is what kept the
        # staging scheduler stuck on one event from 2026-07-20 to 2026-09-07.
        due = [
            (
                event.id,
                DigitalTwinEvent(
                    event_type=event.event_type,
                    user_id=event.user_id,
                    community_id=event.community_id,
                    facts=dict(event.facts or {}),
                ),
            )
            for event in result.scalars().all()
        ]

        for event_id, evt in due:
            try:
                results = await run_engine_batch(evt, db)
                created = [
                    row
                    for row in results
                    if row.status == EngineResultStatus.CREATED and row.nudge
                ]
                for row in created:
                    if row.nudge is not None:
                        await orchestrate(db, row.nudge.nudge_id)

                outcome = {
                    "status": "dispatched",
                    "dispatched_at": utc_now(),
                    "last_error": None,
                }
            except Exception as exc:
                logger.exception(
                    "Scheduled event dispatch failed for id=%s", event_id
                )
                outcome = {"status": "failed", "last_error": str(exc)}

            # Mark the row by id rather than through the (possibly expired) instance,
            # and commit at once: the next event's engine run may roll the session
            # back, and an outcome still sitting in the transaction would go with it —
            # leaving the row `pending` and re-dispatched on the next poll.
            await db.execute(
                update(ScheduledEvent)
                .where(ScheduledEvent.id == event_id)
                .values(**outcome)
            )
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
