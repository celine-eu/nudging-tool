"""Dev automation: create database → run migrations → seed.

This script is intentionally NOT used in production startup.
In production, run `alembic upgrade head` as a separate step before
launching the application.

Usage:
    uv run python -m celine.nudging.db.init_db
    # or
    python src/celine/nudging/db/init_db.py
"""

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from celine.nudging.config.settings import settings
from celine.nudging.db.seed_db import main as seed_main

logger = logging.getLogger(__name__)

# Resolve alembic.ini relative to the project root (three levels up from this file:
# src/celine/nudging/db/ → src/celine/nudging/ → src/celine/ → src/ → project root)
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[4]
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"


async def create_database_if_not_exists(db_url: str) -> None:
    """Connect to the `postgres` maintenance DB and create the target DB if absent."""
    url = make_url(db_url)
    db_name = url.database
    admin_url = url.set(database="postgres")

    engine = create_async_engine(str(admin_url), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            )
            exists = result.scalar()

            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
                logger.info("Created database: %s", db_name)
            else:
                logger.info("Database already exists: %s", db_name)
    finally:
        await engine.dispose()


def run_migrations() -> None:
    """Run `alembic upgrade head` synchronously (Alembic's API is sync)."""
    if not _ALEMBIC_INI.exists():
        raise FileNotFoundError(
            f"alembic.ini not found at {_ALEMBIC_INI}. "
            "Run this script from the project root or check _PROJECT_ROOT."
        )

    alembic_cfg = Config(str(_ALEMBIC_INI))
    logger.info("Running alembic upgrade head...")
    command.upgrade(alembic_cfg, "head")
    logger.info("Migrations complete.")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # 1. Ensure the database exists
    await create_database_if_not_exists(settings.DATABASE_URL)

    # 2. Apply all migrations (creates schema on a clean DB, incremental on existing)
    run_migrations()

    # 3. Seed reference data (rules, templates, preferences) – upsert-safe
    await seed_main()

    print("DB initialized, migrated, and seeded.")


if __name__ == "__main__":
    asyncio.run(main())
