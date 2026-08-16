"""Shared fixtures.

**No test reaches a real service — with one deliberate exception.** This repository
talks to PostgreSQL, Keycloak's JWKS, a web-push service and an SMTP server; all four
are faked here at the narrowest boundary that still exercises our code.

The Rego bundle is the exception, and it is on purpose. `celine.sdk.policies` evaluates
Rego **in process** through `regorus` — no server, no socket — so the real
`policies/celine/nudging/authz.rego` is what the suite evaluates. See ADR-0002.

Unlike its siblings, this service **fails closed**: `_extract_bool` defaults to `False`
and `init_policy_engine()` raises without a bundle, so an *allow* here is evidence that
the policy ran. The `policy_engine_is_loaded` guard is kept anyway — see
`.agents/knowledge/the-policy-engine-fails-closed.md`.

The environment is set *before* `celine.nudging` is imported anywhere: `settings.py`
builds its `Settings()` at import time and `db/session.py` builds the engine from it, so
by the time a test module is collected the wiring has happened and a fixture cannot undo
it. That is also why `DATABASE_URL` is pinned to an unroutable DSN rather than left to
`.env` — the committed `.env` is read at import and would otherwise decide what the
suite points at.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Must run before the first `celine.nudging` import. Do not move below them.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Parsed by SQLAlchemy at import to build the engine; never connected to, because every
# test that needs a database gets the SQLite session from the `db` fixture. An
# environment variable beats the `.env` file in pydantic-settings, which is what keeps a
# developer's local `.env` out of the suite.
os.environ["DATABASE_URL"] = "postgresql+asyncpg://test:test@127.0.0.1:1/test"

# `policies_dir` defaults to the relative `./policies`, so the bundle only loads when
# pytest runs from the repository root. Pin it by absolute path so the suite does not
# depend on the working directory, and so a missing bundle is a collection error rather
# than a suite that proves nothing.
os.environ["CELINE_POLICIES_POLICIES_DIR"] = str(_REPO_ROOT / "policies")

# Read on every catalog call, relative by default. Same reason.
os.environ["SEED_DIR"] = str(_REPO_ROOT / "seed")

# Click-tracking signs with this or falls back to the VAPID private key; pinning it keeps
# a token minted in one test verifiable in another regardless of what `.env` holds.
os.environ["CLICK_TRACKING_SECRET"] = "test-click-tracking-secret"
os.environ["VAPID_PUBLIC_KEY"] = "test-vapid-public-key"
os.environ["VAPID_PRIVATE_KEY"] = "test-vapid-private-key"
os.environ["VAPID_SUBJECT"] = "mailto:test@example.test"

# Emptied rather than pointed somewhere: the email publisher raises before it opens a
# socket when either is unset, so a test that reaches it by accident fails locally
# instead of dialling a developer's SMTP relay. The `smtp` fixture sets both when a test
# means to exercise the send.
os.environ["SMTP_HOST"] = ""
os.environ["EMAIL_FROM"] = ""

import pytest  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from celine.nudging.db.models import Base  # noqa: E402
from celine.nudging.db.session import get_db  # noqa: E402
from celine.nudging.security import policies as policies_module  # noqa: E402

from tests.fakes import (  # noqa: E402
    FakeSmtp,
    FakeWebPush,
    install_fake_jwt,
    make_admin,
    make_ingest_service,
    make_user,
)

USER_SUB = "user-alice"
OTHER_SUB = "user-bob"
COMMUNITY = "community-1"


# ---------------------------------------------------------------------------
# The policy engine must be real, and must be loaded
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def policy_engine_is_loaded():
    """Load the real bundle once, and refuse to run the suite without it.

    `init_policy_engine()` is what `main.py` calls at lifespan startup. Calling it here
    means every test — including the pure-unit ones — runs against the same engine the
    service runs against, and that a bundle that stops parsing is a collection failure
    rather than a suite of `403`s that pass for the wrong reason.
    """
    policies_module.init_policy_engine()
    engine = policies_module.get_policy_engine()
    assert engine is not None, "the Rego bundle did not load"
    assert "celine.nudging.authz" in engine.get_packages(), (
        "the authz package is missing from the loaded bundle, so every authorisation "
        "assertion in this suite would be measuring the fallback rather than the policy"
    )
    return engine


# ---------------------------------------------------------------------------
# Database — real SQLAlchemy, real SQL, SQLite instead of PostgreSQL
# ---------------------------------------------------------------------------

# `_run_single_rule` recognises a duplicate by finding the constraint *name* in the
# driver's error text. PostgreSQL puts it there; SQLite says
# `UNIQUE constraint failed: nudges_log.dedup_key` and never names the constraint, so
# without this the dedup branch is unreachable on SQLite and every duplicate escapes as
# a 500. The rewrite is scoped to that one constraint, and
# `tests/unit/test_engine_dedup.py` pins the literal against the model and the migration
# so a rename fails the suite rather than being papered over here.
# See `.agents/knowledge/dedup-is-matched-on-a-postgres-error-string.md`.
_PG_DEDUP_MESSAGE = (
    'duplicate key value violates unique constraint "uq_nudges_dedup_key"'
)


def _speak_postgres_about_dedup(context):
    original = context.original_exception
    if "UNIQUE constraint failed: nudges_log.dedup_key" not in str(original):
        return None
    return IntegrityError(
        context.statement, context.parameters, Exception(_PG_DEDUP_MESSAGE)
    )


@pytest.fixture
async def db_sessionmaker():
    """A SQLite engine carrying the real `Base.metadata` schema.

    One engine per test, so a test never sees another test's rows. `StaticPool` is what
    makes an in-memory SQLite database usable at all: the default pool hands each
    connection its own private database, which would lose the tables between
    `create_all` and the first query.

    This is not PostgreSQL and the schema is built from the models rather than from the
    migrations. What that costs is stated in ADR-0003.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine.sync_engine, "handle_error", _speak_postgres_about_dedup)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    await engine.dispose()


@pytest.fixture
async def db(db_sessionmaker):
    """A session on the per-test database."""
    async with db_sessionmaker() as session:
        yield session


# ---------------------------------------------------------------------------
# Outbound boundaries — the two places this service talks to the world
# ---------------------------------------------------------------------------


@pytest.fixture
def webpush(monkeypatch) -> FakeWebPush:
    """Replace `pywebpush.webpush` where it is imported, in both call sites.

    The admin module is fetched through `importlib`: `admin/__init__.py` rebinds the
    name `webpush` to a router, so both `from … import webpush` and a dotted
    `monkeypatch.setattr` path reach the router instead of the module.
    """
    import importlib

    from celine.nudging.publishers.web import worker as web_worker

    admin_webpush_module = importlib.import_module(
        "celine.nudging.api.routes.admin.webpush"
    )

    fake = FakeWebPush()
    monkeypatch.setattr(web_worker, "webpush", fake)
    monkeypatch.setattr(admin_webpush_module, "webpush", fake)
    return fake


@pytest.fixture
def smtp(monkeypatch) -> FakeSmtp:
    """Replace `smtplib.SMTP` / `SMTP_SSL` and give the publisher a usable config.

    Without `SMTP_HOST` and `EMAIL_FROM` the publisher raises before it reaches the
    library, which would make every email test pass on a failure that is not the one
    being tested.
    """
    fake = FakeSmtp()
    monkeypatch.setattr("celine.nudging.publishers.email.worker.smtplib", fake)
    monkeypatch.setattr(
        "celine.nudging.publishers.email.worker.settings.SMTP_HOST", "smtp.test"
    )
    monkeypatch.setattr(
        "celine.nudging.publishers.email.worker.settings.EMAIL_FROM",
        "nudging@example.test",
    )
    return fake


# ---------------------------------------------------------------------------
# The application
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_jwt(monkeypatch):
    """Decode the Authorization header without Keycloak.

    The middleware is left in place and still runs on every request: what is replaced is
    `JwtUser.from_token`, which is the only part that needs a JWKS. A test therefore
    still exercises the open-path list, the missing-header branch and the rejection
    branch. See `tests/fakes.py`.
    """
    return install_fake_jwt(monkeypatch)


@pytest.fixture
async def app(db_sessionmaker, fake_jwt, monkeypatch):
    """The real application, with the database and the startup seeding replaced.

    `lifespan` also runs `auto_seed()` against `AsyncSessionLocal` (the PostgreSQL one)
    and starts the scheduler loop. Both are disabled: seeding is covered directly in
    `tests/unit/test_seed_db.py`, and a background poller inside an API test is a race,
    not coverage. `tests/unit/test_scheduler.py` drives the scheduler by hand instead.
    """
    from celine.nudging.main import create_app

    async def _no_auto_seed() -> None:
        return None

    async def _no_scheduler(stop_event) -> None:
        return None

    monkeypatch.setattr("celine.nudging.main.auto_seed", _no_auto_seed)
    monkeypatch.setattr("celine.nudging.main.run_scheduler", _no_scheduler)

    application = create_app()

    async def _get_db():
        async with db_sessionmaker() as session:
            yield session

    application.dependency_overrides[get_db] = _get_db

    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app):
    """An unauthenticated client. Every request is rejected by the middleware."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


def _authed(app, user):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {user.token}"},
    )


@pytest.fixture
async def user_client(app, fake_jwt):
    """A participant: authenticated, no admin and no ingest scope."""
    user = make_user(sub=USER_SUB)
    fake_jwt.register(user)
    async with _authed(app, user) as ac:
        yield ac


@pytest.fixture
async def other_user_client(app, fake_jwt):
    """A second participant, used to prove one cannot read the other's rows."""
    user = make_user(sub=OTHER_SUB)
    fake_jwt.register(user)
    async with _authed(app, user) as ac:
        yield ac


@pytest.fixture
async def admin_client(app, fake_jwt):
    user = make_admin()
    fake_jwt.register(user)
    async with _authed(app, user) as ac:
        yield ac


@pytest.fixture
async def ingest_client(app, fake_jwt):
    """The service account the four senders use: ingest, but not admin."""
    user = make_ingest_service()
    fake_jwt.register(user)
    async with _authed(app, user) as ac:
        yield ac
