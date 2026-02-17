import asyncio

from db.session import engine
from db.models import Base
from db.seed_db import main as seed_main  # adjust import path to where seed_db.py lives

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    print("Schema created (tables + constraints).")

    # Seed YAML into DB (rules/templates/preferences)
    await seed_main()

    print("DB initialized + seeded.")

if __name__ == "__main__":
    asyncio.run(main())