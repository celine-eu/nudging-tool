"""Shared Pydantic schemas for the nudging API.

All request bodies and response models live here so they appear correctly
in the FastAPI/OpenAPI docs and can be reused across routers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    """Generic operation acknowledgement."""

    status: str = Field(..., examples=["ok"])


# ---------------------------------------------------------------------------
# Notifications (user-facing)
# ---------------------------------------------------------------------------


class NotificationPayload(BaseModel):
    """The rendered nudge content stored inside NudgeLog.payload."""

    title: str
    body: str
    scenario: str | None = None
    facts_version: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)


class NotificationOut(BaseModel):
    """A single notification as returned to the end user."""

    id: str = Field(..., description="Nudge ID (hex UUID)")
    rule_id: str
    user_id: str
    status: str = Field(..., description="created | sent | suppressed | failed")
    payload: NotificationPayload
    created_at: datetime
    read_at: datetime | None = Field(None, description="Null if unread")
    deleted_at: datetime | None = Field(None, description="Null if not soft-deleted")

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Notifications (admin)
# ---------------------------------------------------------------------------


class AdminNotificationOut(BaseModel):
    """A notification as returned to admin callers (same shape, explicit class)."""

    id: str
    rule_id: str
    user_id: str
    status: str
    payload: NotificationPayload
    created_at: datetime
    read_at: datetime | None = None
    deleted_at: datetime | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class DeliveryJobOut(BaseModel):
    """A delivery job returned inside an ingest response."""

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
    """A single suppressed/non-triggered engine result."""

    status: str
    reason: str | None = None
    details: dict[str, Any] | None = None


class NudgeCreatedItem(BaseModel):
    nudge_id: str
    rule_id: str
    deliveries: list[DeliveryJobOut]


class IngestOkResponse(BaseModel):
    """All nudges created and at least one delivery dispatched."""

    status: str = Field("ok", examples=["ok"])
    created: list[NudgeCreatedItem]
    suppressed: list[EngineResultOut]


class IngestAcceptedResponse(BaseModel):
    """Nudges created but all deliveries suppressed by the orchestrator."""

    status: str = Field("accepted", examples=["accepted"])
    delivery: str = Field("suppressed", examples=["suppressed"])
    created: list[NudgeCreatedItem]
    suppressed: list[EngineResultOut]


class IngestErrorDetail(BaseModel):
    """Shared error envelope for 409 / 422 / 400 / 500 ingest responses."""

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
    """
    user_id is intentionally absent – it is derived from the JWT (user.sub).
    community_id is the only caller-supplied identity field.
    """

    community_id: str | None = None
    subscription: WebPushSubscriptionIn


class UnsubscribeRequest(BaseModel):
    """
    user_id is intentionally absent – derived from the JWT.
    Only the endpoint is needed to identify which subscription to disable.
    """

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
