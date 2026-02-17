from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.db.models import UserPreference


async def get_user_pref(db: AsyncSession, user_id: str) -> UserPreference | None:
    res = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    return res.scalar_one_or_none()
