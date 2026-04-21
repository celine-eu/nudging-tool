from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.sdk.auth import JwtUser
from celine.nudging.api.schemas import ScheduledEventCreateRequest, ScheduledEventOut
from celine.nudging.db.models import ScheduledEvent
from celine.nudging.db.session import get_db
from celine.nudging.engine.rules.contract import validate_facts_contract
from celine.nudging.security.policies import require_ingest

router = APIRouter(tags=["admin"])


@router.post(
    "/scheduled-events",
    response_model=ScheduledEventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a future nudging event",
)
async def create_scheduled_event(
    body: ScheduledEventCreateRequest,
    db: AsyncSession = Depends(get_db),
    _user: JwtUser = Depends(require_ingest),
) -> ScheduledEventOut:
    if not body.facts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing facts in scheduled event",
        )

    contract = validate_facts_contract(body.facts)
    if not contract.ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "invalid_facts_contract",
                "errors": contract.errors,
            },
        )

    existing: ScheduledEvent | None = None
    if body.external_key:
        existing = (
            await db.execute(
                select(ScheduledEvent).where(
                    ScheduledEvent.external_key == body.external_key
                )
            )
        ).scalar_one_or_none()

    if existing is not None:
        existing.event_type = body.event_type
        existing.user_id = body.user_id
        existing.community_id = body.community_id
        existing.trigger_at = body.trigger_at
        existing.facts = dict(body.facts)
        existing.status = "pending"
        existing.last_error = None
        existing.dispatched_at = None
        await db.commit()
        await db.refresh(existing)
        return ScheduledEventOut.model_validate(existing)

    event = ScheduledEvent(
        event_type=body.event_type,
        user_id=body.user_id,
        community_id=body.community_id,
        external_key=body.external_key,
        trigger_at=body.trigger_at,
        facts=dict(body.facts),
        status="pending",
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return ScheduledEventOut.model_validate(event)
