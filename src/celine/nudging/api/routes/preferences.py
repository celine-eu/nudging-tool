from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.api.schemas import UserPreferenceOut, UserPreferenceUpdateIn
from celine.nudging.db.models import UserPreference
from celine.nudging.db.session import get_db
from celine.nudging.security.policies import get_current_user
from celine.sdk.auth import JwtUser

router = APIRouter(prefix="/preferences", tags=["preferences"])


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


@router.get("/me", response_model=UserPreferenceOut)
async def get_my_preferences(
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserPreferenceOut:
    pref = await _load_preference(user, db)
    if pref is None:
        return UserPreferenceOut(max_per_day=3, channel_email=False, email=None)
    return UserPreferenceOut(
        max_per_day=pref.max_per_day,
        channel_email=pref.channel_email,
        email=pref.email,
    )


@router.put("/me", response_model=UserPreferenceOut)
async def update_my_preferences(
    body: UserPreferenceUpdateIn,
    user: JwtUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserPreferenceOut:
    canonical = _canonical_user_id(user)
    pref = await _load_preference(user, db)
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
    await db.commit()
    await db.refresh(pref)
    return UserPreferenceOut(
        max_per_day=pref.max_per_day,
        channel_email=pref.channel_email,
        email=pref.email,
    )
