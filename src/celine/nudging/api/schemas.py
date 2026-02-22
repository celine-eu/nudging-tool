"""Shared Pydantic schemas for the nudging API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    status: str = Field(..., examples=["ok"])


# ---------------------------------------------------------------------------
# Notifications (user-facing)
# ---------------------------------------------------------------------------


class NotificationOut(BaseModel):
    """User-facing notification. Fields are proper columns, not buried JSON."""

    id: str
    nudge_log_id: str | None = Field(None, description="Originating engine audit row")
    rule_id: str
    user_id: str
    community_id: str | None = None
    family: str = Field(..., description="energy | onboarding | seasonal | …")
    type: str = Field(..., description="informative | opportunity | alert")
    severity: str = Field(..., description="info | warning | critical")
    title: str
    body: str
    status: str = Field(..., description="pending | sent | suppressed | failed")
    read_at: datetime | None = Field(None, description="Null if unread")
    deleted_at: datetime | None = Field(None, description="Null if not soft-deleted")
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Notifications (admin)
# ---------------------------------------------------------------------------


class AdminNotificationOut(BaseModel):
    """Admin view – same fields, explicit class for OpenAPI separation."""

    id: str
    nudge_log_id: str | None = None
    rule_id: str
    user_id: str
    community_id: str | None = None
    family: str
    type: str
    severity: str
    title: str
    body: str
    status: str
    read_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class DeliveryJobOut(BaseModel):
    job_id: str
    user_id: str
    rule_id: str
    nudge_id: str
    channel: str
    destination: str
    title: str
    body: str
    dedup_key: str
    created_at: datetime


class EngineResultOut(BaseModel):
    status: str
    reason: str | None = None
    details: dict[str, Any] | None = None


class NudgeCreatedItem(BaseModel):
    nudge_id: str
    rule_id: str
    deliveries: list[DeliveryJobOut]


class IngestOkResponse(BaseModel):
    status: str = Field("ok", examples=["ok"])
    created: list[NudgeCreatedItem]
    suppressed: list[EngineResultOut]


class IngestAcceptedResponse(BaseModel):
    status: str = Field("accepted", examples=["accepted"])
    delivery: str = Field("suppressed", examples=["suppressed"])
    created: list[NudgeCreatedItem]
    suppressed: list[EngineResultOut]


class IngestErrorDetail(BaseModel):
    error: str
    reason: str | None = None
    errors: list[str] | None = None
    results: list[EngineResultOut] | None = None


# ---------------------------------------------------------------------------
# WebPush
# ---------------------------------------------------------------------------


class WebPushKeysIn(BaseModel):
    p256dh: str
    auth: str


class WebPushSubscriptionIn(BaseModel):
    endpoint: str
    keys: WebPushKeysIn


class SubscribeRequest(BaseModel):
    """user_id is derived from the JWT – not accepted from the caller."""

    community_id: str | None = None
    subscription: WebPushSubscriptionIn


class UnsubscribeRequest(BaseModel):
    """user_id is derived from the JWT – not accepted from the caller."""

    endpoint: str


class VapidPublicKeyResponse(BaseModel):
    public_key: str


class SendTestRequest(BaseModel):
    """Admin-only: user_id is explicit because an admin targets any user."""

    user_id: str = Field(..., description="Target user ID")
    title: str = Field("Test", examples=["Test"])
    body: str = Field("Hello!", examples=["Hello!"])
    url: str = Field("/", examples=["/"])


class SendTestResponse(BaseModel):
    status: str
    sent: int | None = None
    failed: int | None = None
