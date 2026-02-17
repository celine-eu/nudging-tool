import asyncio

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from celine.nudging.config.settings import settings
from celine.nudging.db.models import Base
from celine.nudging.db.seed_db import (
    main as seed_main,
)  # adjust import path to where seed_db.py lives
from celine.nudging.db.session import engine


async def create_database_if_not_exists(db_url: str) -> None:
    url = make_url(db_url)
    db_name = url.database
    admin_url = url.set(database="postgres")

    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": db_name},
        )
        exists = result.scalar()

        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))


async def main():

    await create_database_if_not_exists(settings.DATABASE_URL)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    print("Schema created (tables + constraints).")

    # Seed YAML into DB (rules/templates/preferences)
    await seed_main()

    print("DB initialized + seeded.")


if __name__ == "__main__":
    asyncio.run(main())
