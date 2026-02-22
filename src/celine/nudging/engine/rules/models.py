from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class NudgeType(str, Enum):
    informative = "informative"
    opportunity = "opportunity"
    alert = "alert"


class NudgeSeverity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class DigitalTwinEvent(BaseModel):
    event_type: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    user_id: str

    # raw refs, optional
    payload: Dict[str, Any] = Field(default_factory=dict)
    # computed metrics ready for rules/templates
    facts: Dict[str, Any] = Field(
        default_factory=dict, description="Enriched facts computed by Digital Twin"
    )


class NudgeEvent(BaseModel):
    nudge_id: str
    rule_id: str
    family: str
    type: NudgeType
    severity: NudgeSeverity
    user_id: str
    facts: Dict[str, Any] = Field(default_factory=dict)
    render_context: Dict[str, Any] = Field(default_factory=dict)
    title: str
    body: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
