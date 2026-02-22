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

    oidc: OidcSettings = OidcSettings(audience="svc-nudging")
    policies: PoliciesSettings = PoliciesSettings()

    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_SUBJECT: str = "mailto:test@celine.localhost"

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:securepassword123@172.17.0.1:15432/nudging"
    )

    # General
    SEED_DIR: Optional[str] = None
    DEFAULT_LANG: str = "en"
    ORCHESTRATOR_URL: str = "http://api.celine.localhost/nudging"

    # Rate limiting defaults
    MAX_PER_DAY_DEFAULT: int = 3

    # Scenario → Rules mapping
    # One scenario can trigger multiple rules (daily, monthly, etc.)
    SCENARIO_TO_RULE_IDS: Dict[str, List[str]] = {
        # static / onboarding / seasonal
        "botanswer": ["botanswer"],
        "welcome": ["welcome"],
        "form": ["form"],
        "survey": ["survey"],
        "spring": ["spring"],
        "summer": ["summer"],
        "autumn": ["autumn"],
        "winter": ["winter"],
        # event-based energy/weather
        "imported_down": ["imported_down"],
        "imported_up": ["imported_up"],
        "price_up": ["price_up"],
        "price_down": ["price_down"],
        "sunny_pros": ["sunny_pros"],
        "sunny_cons": ["sunny_cons"],
        "extr_event": ["extr_event"],
        # KPI / ratio-based (conditions-driven)
        "generation_down_monthly_v1": ["generation_down_monthly_v1"],
        "selfcons_down_weekly_v1": ["selfcons_down_weekly_v1"],
        "selfcons_down_monthly_v1": ["selfcons_down_monthly_v1"],
        "inverter_error_critical_daily_v1": ["inverter_error_critical_daily_v1"],
        "inverter_error_minor_weekly_v1": ["inverter_error_minor_weekly_v1"],
        "price_up_weekly_v1": ["price_up_weekly_v1"],
        "generation_down_monthly_not_radiation_v1": [
            "generation_down_monthly_not_radiation_v1"
        ],
        "bill_up_monthly_v1": ["bill_up_monthly_v1"],
        "bill_down_monthly_v1": ["bill_down_monthly_v1"],
        "generation_min_down_two_weeks_not_radiation_v1": [
            "generation_min_down_two_weeks_not_radiation_v1"
        ],
        "bill_up_monthly_rootcause_v1": ["bill_up_monthly_rootcause_v1"],
        "specific_yield_low_two_weeks_v1": ["specific_yield_low_two_weeks_v1"],
        # INFO placeholders (event_name driven)
        "imported_energy_info_monthly_v1": ["imported_energy_info_monthly_v1"],
        "exported_energy_info_monthly_v1": ["exported_energy_info_monthly_v1"],
        "ticket_opened_info_v1": ["ticket_opened_info_v1"],
        "maintenance_scheduled_info_v1": ["maintenance_scheduled_info_v1"],
        "contract_renewal_info_v1": ["contract_renewal_info_v1"],
    }


settings = Settings()
