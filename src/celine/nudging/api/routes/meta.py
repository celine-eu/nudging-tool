from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.api.schemas import (
    StatusResponse,
    SubscribeRequest,
    UnsubscribeRequest,
    VapidPublicKeyResponse,
)
from celine.nudging.db.models import WebPushSubscription
from celine.nudging.db.session import get_db
from celine.nudging.config.settings import settings
from celine.nudging.security.policies import get_current_user
from celine.sdk.auth.jwt import JwtUser

logger = logging.getLogger(__name__)

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health():
    return {"status": "ok"}
