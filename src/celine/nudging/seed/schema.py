from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict, model_validator


class RuleSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    enabled: bool = True
    family: str
    type: str
    severity: str
    version: int = 1
    definition: Dict[str, Any] = Field(default_factory=dict)
    # Optional top-level scenarios for authoring convenience. Will be merged into definition.
    scenarios: Optional[List[str]] = None

    @model_validator(mode="after")
    def _merge_scenarios(self) -> "RuleSeed":
        if self.scenarios:
            merged = dict(self.definition or {})
            if not merged.get("scenarios"):
                merged["scenarios"] = list(self.scenarios)
            self.definition = merged
        return self


class TemplateSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Optional[str] = None
    rule_id: str
    lang: str = "en"
    title_jinja: str
    body_jinja: str
    # Optional content version for editorial tracking (not persisted in DB yet).
    content_version: Optional[int] = None


class PreferenceSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    community_id: Optional[str] = None
    lang: Optional[str] = None

    channel_web: bool = True
    channel_email: bool = False
    channel_telegram: bool = False
    channel_whatsapp: bool = False

    email: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    whatsapp_phone: Optional[str] = None

    max_per_day: Optional[int] = None
    consents: Dict[str, Any] = Field(default_factory=dict)


class RuleOverrideSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    community_id: str
    enabled_override: Optional[bool] = None
    definition_override: Dict[str, Any] = Field(default_factory=dict)
