"""Alembic environment – async SQLAlchemy + asyncpg.

Reads DATABASE_URL from the application settings so there is a single
source of truth; no need to keep alembic.ini in sync with .env.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# Import Base so Alembic can diff against the full metadata
from celine.nudging.db.models import Base
from celine.nudging.config.settings import settings
from sqlalchemy import NullPool, create_engine, make_url, text

# ---------------------------------------------------------------------------
# Alembic Config object (provides access to values in alembic.ini)
# ---------------------------------------------------------------------------
config = context.config

# Wire up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url with the live setting so alembic.ini is never stale
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata

_url = make_url(settings.DATABASE_URL).set(drivername="postgresql")


def create_database_if_not_exists() -> None:
    admin_engine = create_engine(
        _url.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    with admin_engine.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": _url.database}
        )
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{_url.database}"'))
            print(f"Created: {_url.database}")
    admin_engine.dispose()


# ---------------------------------------------------------------------------
# Offline mode – emit SQL to stdout (no live DB connection)
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode – real async connection
# ---------------------------------------------------------------------------
def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    create_database_if_not_exists()

    engine = create_engine(
        _url.render_as_string(hide_password=False),
        poolclass=NullPool,
    )
    with engine.connect() as conn:
        context.configure(
            connection=conn, target_metadata=target_metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
