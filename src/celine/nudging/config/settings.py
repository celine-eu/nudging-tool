import os
from typing import Dict, List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from celine.sdk.posture import PostureGuard
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

    # Keyword arguments beat the environment in pydantic-settings, so each value is
    # read from it explicitly: a literal here would silently override
    # CELINE_OIDC_CLIENT_SECRET in a deployment. The defaults are the local realm's
    # (secret == client id), which the posture guard refuses outside CELINE_ENV=dev.
    oidc: OidcSettings = OidcSettings(
        client_id=os.getenv("CELINE_OIDC_CLIENT_ID", "svc-nudging"),
        client_secret=os.getenv("CELINE_OIDC_CLIENT_SECRET", "svc-nudging"),
        audience=os.getenv("CELINE_OIDC_AUDIENCE", "svc-nudging"),
    )
    policies: PoliciesSettings = PoliciesSettings()

    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_SUBJECT: str = "mailto:dev@example.com"
    CLICK_TRACKING_SECRET: str = ""
    # Web-push destinations (REQ-0083): comma-separated host suffixes, `*` for any
    # public host. An entry matches itself and its subdomains. The default is the
    # browser push services: FCM (Chrome, Edge on Android), Mozilla autopush (Firefox),
    # web.push.apple.com (Safari), *.notify.windows.com (Edge on Windows).
    WEBPUSH_ALLOWED_HOSTS: str = (
        "fcm.googleapis.com,updates.push.services.mozilla.com,push.apple.com,notify.windows.com"
    )
    # Lifts every endpoint rule, for a local push-service stand-in. Honoured only with
    # CELINE_ENV=dev; the posture guard refuses it anywhere else.
    WEBPUSH_ENDPOINT_RELAXED: bool = False
    WEBPUSH_TIMEOUT_SECONDS: float = 10.0
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



def posture_guard(settings: Settings, env: str | None = None) -> PostureGuard:
    """Every development default this service ships, registered for refusal.

    `celine.sdk.posture`: only `CELINE_ENV=dev` relaxes. Anywhere else `enforce()`
    raises with the complete list; in dev it logs one warning. VAPID and the
    click-tracking secret otherwise fail only when first used, so a deployment
    without them would start and then drop every push; outside dev they are
    required up front. The click-tracking secret has no fallback to the VAPID key;
    in dev an unset one uses a fixed development secret (`notifications_tracking`).
    """
    guard = PostureGuard("nudging-api", env=env)
    guard.forbid_dev_database_url("DATABASE_URL", settings.DATABASE_URL)
    guard.forbid_secret_equal_to_client_id(
        "CELINE_OIDC_CLIENT_SECRET", settings.oidc.client_id, settings.oidc.client_secret
    )
    guard.require_explicit_oidc(settings.oidc, require_audience=True)
    guard.require_set(
        "VAPID_PUBLIC_KEY",
        settings.VAPID_PUBLIC_KEY,
        "Generate a key pair (`nudging-cli vapid gen`) and set both halves.",
    )
    guard.require_set(
        "VAPID_PRIVATE_KEY",
        settings.VAPID_PRIVATE_KEY,
        "Generate a key pair (`nudging-cli vapid gen`) and set both halves.",
    )
    guard.require_set(
        "CLICK_TRACKING_SECRET",
        settings.CLICK_TRACKING_SECRET,
        "Set a dedicated random secret for signing click-tracking tokens.",
    )
    guard.forbid_true(
        "WEBPUSH_ENDPOINT_RELAXED",
        settings.WEBPUSH_ENDPOINT_RELAXED,
        "Unset it; outside dev every push endpoint is checked (REQ-0083).",
    )
    return guard


settings = Settings()
