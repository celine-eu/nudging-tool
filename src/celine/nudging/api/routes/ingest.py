from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.db.session import get_db
from celine.nudging.engine.engine_service import EngineResultStatus, run_engine_batch
from celine.nudging.engine.rules.contract import validate_facts_contract
from celine.nudging.engine.rules.models import DigitalTwinEvent
from celine.nudging.orchestrator.orchestrator import orchestrate

router = APIRouter()


logger = logging.getLogger(__name__)


@router.post("/ingest-event")
async def ingest_event(evt: DigitalTwinEvent, db: AsyncSession = Depends(get_db)):
    # --- base contract ---
    facts = evt.facts or {}
    if not facts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing facts in DT event",
        )

    contract = validate_facts_contract(facts)
    if not contract.ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "invalid_facts_contract",
                "errors": contract.errors,
            },
        )

    # --- run engine (BATCH) ---
    results = await run_engine_batch(evt, db)

    # created nudges
    created = [r for r in results if r.status == EngineResultStatus.CREATED and r.nudge]
    if created:
        created_payload = []
        any_jobs = False

        for r in created:
            nudge = r.nudge

            if nudge is None:
                logger.warning(f"Skipping empty nudge")
                logger.debug(f"{r}")
                continue

            jobs = await orchestrate(db, nudge.nudge_id)

            if jobs:
                any_jobs = True

            created_payload.append(
                {
                    "nudge_id": nudge.nudge_id,
                    "rule_id": nudge.rule_id,
                    "deliveries": [j.model_dump() for j in jobs],
                }
            )

        # if nudges created but ALL deliveries suppressed by orchestrator
        if not any_jobs:
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={
                    "status": "accepted",
                    "delivery": "suppressed",
                    "created": created_payload,
                    "suppressed": [
                        {"status": r.status, "reason": r.reason, "details": r.details}
                        for r in results
                        if r.status != EngineResultStatus.CREATED
                    ],
                },
            )

        return {
            "status": "ok",
            "created": created_payload,
            "suppressed": [
                {"status": r.status, "reason": r.reason, "details": r.details}
                for r in results
                if r.status != EngineResultStatus.CREATED
            ],
        }

    # --- no nudges created -> aggregate suppression result ---
    statuses = {r.status for r in results}

    # All rules evaluated, none triggered
    if statuses == {EngineResultStatus.NOT_TRIGGERED}:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # All rules suppressed by dedup
    if statuses == {EngineResultStatus.SUPPRESSED_DEDUP}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "suppressed",
                "reason": "all_rules_dedup",
                "results": [
                    {"status": r.status, "reason": r.reason, "details": r.details}
                    for r in results
                ],
            },
        )

    # Any missing facts -> 422
    if EngineResultStatus.MISSING_FACTS in statuses:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "missing_required_facts",
                "results": [
                    {"status": r.status, "reason": r.reason, "details": r.details}
                    for r in results
                ],
            },
        )

    # Any unknown scenario / config -> 400
    if EngineResultStatus.UNKNOWN_SCENARIO in statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "unknown_scenario",
                "results": [
                    {"status": r.status, "reason": r.reason, "details": r.details}
                    for r in results
                ],
            },
        )

    # Fallback
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "error": "no_nudge_created",
            "results": [
                {"status": r.status, "reason": r.reason, "details": r.details}
                for r in results
            ],
        },
    )
