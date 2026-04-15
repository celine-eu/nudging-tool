from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.api.schemas import (
    NotificationKindPreferenceOut,
    UserPreferenceOut,
    UserPreferenceUpdateIn,
)
from celine.nudging.config.settings import settings
from celine.nudging.db.models import UserPreference
from celine.nudging.db.session import get_db
from celine.nudging.orchestrator.preferences import get_enabled_notification_kinds
from celine.nudging.security.policies import get_current_user
from celine.nudging.seed import load_seed_dir, localize_active_kinds
from celine.sdk.auth import JwtUser

router = APIRouter(prefix="/preferences", tags=["preferences"])
_ENABLED_KINDS_KEY = "enabled_notification_kinds"


def _canonical_user_id(user: JwtUser) -> str:
    preferred_username = user.preferred_username
    if preferred_username and preferred_username.strip():
        return preferred_username.strip()
    return user.sub


def _owned_user_ids(user: JwtUser) -> list[str]:
    candidate_ids = [user.sub]

    preferred_username = user.preferred_username
    if preferred_username and preferred_username not in candidate_ids:
        candidate_ids.append(preferred_username)

    return candidate_ids


async def _load_preference(user: JwtUser, db: AsyncSession) -> UserPreference | None:
    result = await db.execute(
        select(UserPreference)
        .where(
            UserPreference.user_id.in_(_owned_user_ids(user)),
        )
        .order_by(UserPreference.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _preferred_lang(pref: UserPreference | None, lang: str | None) -> str:
    if isinstance(lang, str) and lang.strip():
        return lang.strip().lower()
    if pref and isinstance(pref.lang, str) and pref.lang.strip():
        return pref.lang.strip().lower()
    return settings.DEFAULT_LANG


def _notification_catalog(pref: UserPreference | None, lang: str | None) -> list[dict]:
    seed = load_seed_dir(Path(settings.SEED_DIR or "./seed"))
    localized = localize_active_kinds(seed.active_kinds, _preferred_lang(pref, lang))
    enabled_kinds = get_enabled_notification_kinds(pref, localized)
    return [
        {
            "kind": item["kind"],
            "label": item["label"],
            "description": item["description"],
            "cadence": item["cadence"],
            "enabled": item["kind"] in enabled_kinds or item.get("editable") is False,
            "editable": bool(item.get("editable", True)),
        }
        for item in localized
    ]


@router.get("/me", response_model=UserPreferenceOut)
async def get_my_preferences(
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserPreferenceOut:
    pref = await _load_preference(user, db)
    catalog = _notification_catalog(pref, None)
    if pref is None:
        return UserPreferenceOut(
            max_per_day=3,
            channel_email=False,
            email=None,
            enabled_notification_kinds=[item["kind"] for item in catalog if item["enabled"]],
        )
    return UserPreferenceOut(
        max_per_day=pref.max_per_day,
        channel_email=pref.channel_email,
        email=pref.email,
        enabled_notification_kinds=[item["kind"] for item in catalog if item["enabled"]],
    )


@router.get("/catalog", response_model=list[NotificationKindPreferenceOut])
async def get_my_notification_catalog(
    lang: str | None = None,
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[NotificationKindPreferenceOut]:
    pref = await _load_preference(user, db)
    return [NotificationKindPreferenceOut.model_validate(item) for item in _notification_catalog(pref, lang)]


@router.put("/me", response_model=UserPreferenceOut)
async def update_my_preferences(
    body: UserPreferenceUpdateIn,
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserPreferenceOut:
    canonical = _canonical_user_id(user)
    pref = await _load_preference(user, db)
    active_catalog = _notification_catalog(pref, None)
    active_kind_ids = {item["kind"] for item in active_catalog}
    if pref is None:
        pref = UserPreference(user_id=canonical, community_id=None)
        db.add(pref)
    elif pref.user_id != canonical:
        # Keep preference aligned with the canonical ID used by orchestration.
        existing_canonical = await db.execute(
            select(UserPreference).where(
                UserPreference.user_id == canonical,
            )
        )
        canonical_pref = existing_canonical.scalar_one_or_none()
        if canonical_pref is None:
            pref.user_id = canonical
        else:
            pref = canonical_pref

    pref.max_per_day = body.max_per_day
    if body.channel_email is not None:
        pref.channel_email = body.channel_email
    if body.email is not None:
        pref.email = body.email.strip() or None
    if body.enabled_notification_kinds is not None:
        pref.consents = dict(pref.consents or {})
        required_kind_ids = {
            item["kind"] for item in active_catalog if item.get("editable") is False
        }
        pref.consents[_ENABLED_KINDS_KEY] = [
            kind for kind in body.enabled_notification_kinds if kind in active_kind_ids
        ]
        for kind in required_kind_ids:
            if kind not in pref.consents[_ENABLED_KINDS_KEY]:
                pref.consents[_ENABLED_KINDS_KEY].append(kind)
    await db.commit()
    await db.refresh(pref)
    catalog = _notification_catalog(pref, None)
    return UserPreferenceOut(
        max_per_day=pref.max_per_day,
        channel_email=pref.channel_email,
        email=pref.email,
        enabled_notification_kinds=[item["kind"] for item in catalog if item["enabled"]],
    )
