from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.config.settings import settings
from celine.nudging.db.models import Rule
from celine.nudging.db.models import UserPreference
from celine.nudging.seed import load_seed_dir, localize_active_kinds

_ENABLED_KINDS_KEY = "enabled_notification_kinds"


async def get_user_pref(
    db: AsyncSession, user_id: str, community_id: str | None
) -> UserPreference | None:
    # Prefer community-specific preference, fallback to generic (community_id NULL).
    res = await db.execute(
        select(UserPreference)
        .where(
            UserPreference.user_id == user_id,
            or_(
                UserPreference.community_id == community_id,
                UserPreference.community_id.is_(None),
            ),
        )
        .order_by(UserPreference.community_id.is_(None).asc())
    )
    return res.scalars().first()


def get_active_notification_kinds(lang: str | None = None) -> list[dict]:
    seed = load_seed_dir(Path(settings.SEED_DIR or "./seed"))
    return localize_active_kinds(seed.active_kinds, (lang or settings.DEFAULT_LANG).lower())


def _required_active_kind_ids(active_kinds: list[dict]) -> list[str]:
    return [item["kind"] for item in active_kinds if item.get("editable") is False]


def get_enabled_notification_kinds(
    pref: UserPreference | None, active_kinds: list[dict] | None = None
) -> list[str]:
    if active_kinds is None:
        active_kinds = get_active_notification_kinds()
    active_kind_ids = [item["kind"] for item in active_kinds]
    required_kind_ids = _required_active_kind_ids(active_kinds)
    if pref is None or not isinstance(pref.consents, dict):
        return active_kind_ids
    raw = pref.consents.get(_ENABLED_KINDS_KEY)
    if not isinstance(raw, list):
        return active_kind_ids
    enabled = [kind for kind in raw if kind in active_kind_ids]
    for kind in required_kind_ids:
        if kind not in enabled:
            enabled.append(kind)
    return enabled


async def get_rule_kind(db: AsyncSession, rule_id: str) -> str | None:
    result = await db.execute(select(Rule.definition).where(Rule.id == rule_id))
    definition = result.scalar_one_or_none()
    if not isinstance(definition, dict):
        return None
    kind = definition.get("kind")
    return kind if isinstance(kind, str) and kind.strip() else None
