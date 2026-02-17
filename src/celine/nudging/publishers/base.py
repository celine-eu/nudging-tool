from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from celine.nudging.orchestrator.models import DeliveryJob


class Publisher(Protocol):
    async def send(self, db: AsyncSession, job: DeliveryJob) -> PublishResult: ...


@dataclass(frozen=True)
class PublishResult:
    status: str  # "sent" | "failed" | "suppressed"
    sent_at: datetime | None = None
    error: str | None = None
