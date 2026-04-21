import os
from typing import Dict, List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from celine.sdk.settings.models import OidcSettings, PoliciesSettings


class Settings(BaseSettings):
    """
    Application settings.
    Loaded from environment variables and .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    oidc: OidcSettings = OidcSettings(
        client_id="svc-nudging",
        client_secret="svc-nudging",
        audience="svc-nudging",
    )
    policies: PoliciesSettings = PoliciesSettings()

    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_SUBJECT: str = "mailto:dev@example.com"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    EMAIL_FROM: str = ""

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:securepassword123@host.docker.internal:15432/nudging"
    )

    # General
    SEED_DIR: Optional[str] = "./seed"
    DEFAULT_LANG: str = "en"
    ORCHESTRATOR_URL: str = "http://api.celine.localhost/nudging"

    # Rate limiting defaults
    MAX_PER_DAY_DEFAULT: int = 3
    SCHEDULER_POLL_SECONDS: float = 30.0

    # Scenario → Rules mapping (legacy fallback). Prefer rule.definition.scenarios.
    SCENARIO_TO_RULE_IDS: Dict[str, List[str]] = {}


settings = Settings()
