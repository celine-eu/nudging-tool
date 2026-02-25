"""Admin seed endpoint.

POST /admin/seed/apply

Accepts the three seed collections as JSON and upserts them into the database.
Protected by require_admin (same policy as other admin routes).

This endpoint is intentionally idempotent — safe to call at startup or from CI.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.db.seed_db import (
    upsert_preference,
    upsert_rule,
    upsert_rule_override,
    upsert_template,
)
from celine.nudging.db.session import get_db
from celine.nudging.security.policies import require_admin
from celine.sdk.auth import JwtUser

router = APIRouter(tags=["admin"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SeedApplyRequest(BaseModel):
    rules: list[dict] = []
    templates: list[dict] = []
    preferences: list[dict] = []
    overrides: list[dict] = []


class SeedApplyResponse(BaseModel):
    status: str = "ok"
    rules: int
    templates: int
    preferences: int
    overrides: int


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/seed/apply",
    summary="Apply seed data",
    description=(
        "Upserts rules, templates and preferences from the provided payload. "
        "Idempotent — safe to call multiple times or at application startup."
    ),
    response_model=SeedApplyResponse,
    status_code=200,
)
async def seed_apply(
    body: SeedApplyRequest,
    db: AsyncSession = Depends(get_db),
    _user: JwtUser = Depends(require_admin),
) -> SeedApplyResponse:
    for r in body.rules:
        await upsert_rule(db, r)

    for t in body.templates:
        await upsert_template(db, t)

    for p in body.preferences:
        await upsert_preference(db, p)

    for o in body.overrides:
        await upsert_rule_override(db, o)

    await db.commit()

    logger.info(
        "Seed applied: %d rules, %d templates, %d preferences",
        len(body.rules),
        len(body.templates),
        len(body.preferences),
        len(body.overrides),
    )

    return SeedApplyResponse(
        rules=len(body.rules),
        templates=len(body.templates),
        preferences=len(body.preferences),
        overrides=len(body.overrides),
    )
