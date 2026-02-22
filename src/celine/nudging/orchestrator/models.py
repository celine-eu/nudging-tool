from enum import Enum
from pydantic import BaseModel, Field
from datetime import datetime

from celine.nudging.db.models import utc_now


class Channel(str, Enum):
    web = "web"
    email = "email"
    telegram = "telegram"
    whatsapp = "whatsapp"


class DeliveryJob(BaseModel):
    user_id: str
    job_id: str
    rule_id: str
    nudge_id: str
    channel: Channel
    destination: str
    title: str
    body: str
    dedup_key: str
    created_at: datetime = Field(default_factory=utc_now)
