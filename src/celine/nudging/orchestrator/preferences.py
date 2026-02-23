from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.db.models import UserPreference


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
