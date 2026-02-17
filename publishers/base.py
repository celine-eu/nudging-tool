from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Any

from sqlalchemy.ext.asyncio import AsyncSession

try:
    from orchestrator.models import DeliveryJob
except Exception:  # pragma: no cover
    DeliveryJob = Any  # type: ignore


class Publisher(Protocol):
    async def send(self, db: AsyncSession, job: DeliveryJob) -> PublishResult:
        ...


@dataclass(frozen=True)
class PublishResult:
    status: str  # "sent" | "failed" | "suppressed"
    sent_at: datetime | None = None
    error: str | None = None
