from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.api.schemas import (
    IngestAcceptedResponse,
    IngestErrorDetail,
    IngestOkResponse,
)
from celine.nudging.db.session import get_db
from celine.nudging.engine.engine_service import EngineResultStatus, run_engine_batch
from celine.nudging.engine.rules.contract import validate_facts_contract
from celine.nudging.engine.rules.models import DigitalTwinEvent
from celine.nudging.orchestrator.orchestrator import orchestrate

router = APIRouter(tags=["admin"])

logger = logging.getLogger(__name__)


@router.post(
    "/ingest-event",
    summary="Ingest a Digital Twin event",
    description=(
        "Accepts an enriched Digital Twin event, evaluates nudging rules, "
        "and dispatches deliveries for any triggered nudges."
    ),
    response_model=IngestOkResponse,
    responses={
        202: {
            "description": "Nudges created but all deliveries suppressed by the orchestrator",
            "model": IngestAcceptedResponse,
        },
        204: {"description": "No rules triggered"},
        400: {"description": "Unknown scenario", "model": IngestErrorDetail},
        409: {
            "description": "All rules suppressed by dedup",
            "model": IngestErrorDetail,
        },
        422: {"description": "Invalid or missing facts", "model": IngestErrorDetail},
        500: {"description": "Unexpected engine failure", "model": IngestErrorDetail},
    },
    status_code=200,
)
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

    created = [r for r in results if r.status == EngineResultStatus.CREATED and r.nudge]
    if created:
        created_payload = []
        any_jobs = False

        for r in created:
            nudge = r.nudge

            if nudge is None:
                logger.warning("Skipping empty nudge")
                logger.debug("%s", r)
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

        suppressed_payload = [
            {"status": r.status, "reason": r.reason, "details": r.details}
            for r in results
            if r.status != EngineResultStatus.CREATED
        ]

        if not any_jobs:
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={
                    "status": "accepted",
                    "delivery": "suppressed",
                    "created": created_payload,
                    "suppressed": suppressed_payload,
                },
            )

        return {
            "status": "ok",
            "created": created_payload,
            "suppressed": suppressed_payload,
        }

    # --- no nudges created ---
    statuses = {r.status for r in results}

    if statuses == {EngineResultStatus.NOT_TRIGGERED}:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

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
