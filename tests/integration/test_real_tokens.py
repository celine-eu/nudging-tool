"""Real tokens from a local Keycloak, verified for real, through the real app.

**Opt-in, and skipped by default** (ADR-0004): the default suite reaches no service
(ADR-0002). Set `NUDGING_REAL_TOKENS=1` to run it against the local realm the
`celine-dev` workspace runs; nothing here may point at a shared environment, and the
module refuses an issuer that is not on a local hostname.

What the unit and API layers cannot show, and this does: the claim shapes are the ones
Keycloak actually issues, the signature is checked against the realm's JWKS with the
service's own audience, and `require_admin` decides on the `JwtUser` that comes out.

| token | from | expected |
|---|---|---|
| `NUDGING_REAL_TOKENS_ADMIN` (default `admin`) | holds the `platform-admin` realm role | administrator |
| `NUDGING_REAL_TOKENS_ORG_ADMIN` (default `org-admin`) | an organisation's `admins` only | not an administrator |
| `NUDGING_LEGACY_ADMIN_TOKEN` | a token still carrying the retired realm group `/admins` | not an administrator |
| `svc-flexibility` client credentials | `nudging.ingest` | may ingest, not administer |

The legacy token cannot be minted from a converged realm (no mapper writes `groups` any
more), so it is passed in already minted. Its test is skipped, with that reason, when it
is not.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from celine.sdk.auth import JwtUser, is_platform_admin

from celine.nudging.config.settings import settings
from celine.nudging.db.session import get_db
from celine.nudging.security.policies import get_policy_engine, policy_input_dict

pytestmark = pytest.mark.skipif(
    os.getenv("NUDGING_REAL_TOKENS") != "1",
    reason="real-token layer is opt-in: set NUDGING_REAL_TOKENS=1 with a local Keycloak running",
)

_LOCAL_SUFFIXES = (".localhost", "localhost", "127.0.0.1")

_USER_CLIENT = os.getenv("NUDGING_REAL_TOKENS_CLIENT", "oauth2_proxy")
_USER_CLIENT_SECRET = os.getenv("NUDGING_REAL_TOKENS_CLIENT_SECRET", _USER_CLIENT)
_ADMIN = os.getenv("NUDGING_REAL_TOKENS_ADMIN", "admin")
_ORG_ADMIN = os.getenv("NUDGING_REAL_TOKENS_ORG_ADMIN", "org-admin")
_SERVICE = os.getenv("NUDGING_REAL_TOKENS_SERVICE", "svc-flexibility")


def _token_endpoint() -> str:
    issuer = settings.oidc.base_url.rstrip("/")
    host = urlparse(issuer).hostname or ""
    if not host.endswith(_LOCAL_SUFFIXES):
        pytest.fail(f"refusing a non-local issuer for real-token tests: {issuer}")
    return f"{issuer}/protocol/openid-connect/token"


def _mint(data: dict[str, str]) -> str:
    response = httpx.post(_token_endpoint(), data=data, timeout=10)
    assert response.status_code == 200, f"token request failed: {response.text}"
    return response.json()["access_token"]


def _user_token(username: str) -> str:
    """A user token as oauth2-proxy obtains it: every organisation requested."""
    return _mint(
        {
            "grant_type": "password",
            "client_id": _USER_CLIENT,
            "client_secret": _USER_CLIENT_SECRET,
            "username": username,
            "password": os.getenv(f"NUDGING_REAL_TOKENS_PASSWORD_{username}", username),
            "scope": "openid email profile organization:*",
        }
    )


def _service_token(client_id: str) -> str:
    return _mint(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": os.getenv("NUDGING_REAL_TOKENS_SERVICE_SECRET", client_id),
        }
    )


def _verified(token: str) -> JwtUser:
    """The same call the middleware makes: signature, issuer, audience `svc-nudging`."""
    return JwtUser.from_token(token, settings.oidc)


@pytest.fixture(scope="module")
def admin_token() -> str:
    return _user_token(_ADMIN)


@pytest.fixture(scope="module")
def org_admin_token() -> str:
    return _user_token(_ORG_ADMIN)


@pytest.fixture(scope="module")
def service_token() -> str:
    return _service_token(_SERVICE)


@pytest.fixture
def legacy_token() -> str:
    token = os.getenv("NUDGING_LEGACY_ADMIN_TOKEN")
    if not token:
        pytest.skip(
            "NUDGING_LEGACY_ADMIN_TOKEN not set: a converged realm cannot mint a token "
            "carrying the retired realm group, so it has to be minted by a fixture first"
        )
    return token


@pytest.fixture
async def real_app(db_sessionmaker, monkeypatch):
    """The application with its real middleware: no token registry stands in for Keycloak."""
    from celine.nudging.main import create_app

    async def _nothing(*_a, **_kw) -> None:
        return None

    monkeypatch.setattr("celine.nudging.main.auto_seed", _nothing)
    monkeypatch.setattr("celine.nudging.main.run_scheduler", _nothing)

    application = create_app()

    async def _get_db():
        async with db_sessionmaker() as session:
            yield session

    application.dependency_overrides[get_db] = _get_db
    async with LifespanManager(application):
        yield application


async def _get(app, token: str, path: str) -> httpx.Response:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        return await ac.get(path)


def _is_admin(user: JwtUser) -> bool:
    engine = get_policy_engine()
    raw = engine.evaluate(
        "data.celine.nudging.authz.is_admin", policy_input_dict(user, action="admin")
    )
    return raw["result"][0]["expressions"][0]["value"] is True


# ---------------------------------------------------------------------------
# The decision, on verified tokens
# ---------------------------------------------------------------------------


# @verifies REQ-0005
def test_a_real_platform_admin_token_is_an_administrator(admin_token):
    user = _verified(admin_token)
    assert is_platform_admin(user.claims)
    assert _is_admin(user) is True


# @verifies REQ-0082
def test_a_real_organisation_admins_token_is_not_an_administrator(org_admin_token):
    user = _verified(org_admin_token)
    orgs = user.claims.get("organization") or {}
    assert any(
        "/admins" in (org.get("groups") or []) for org in orgs.values()
    ), "fixture drift: the organisation admin no longer holds any organisation's admins"
    assert not is_platform_admin(user.claims)
    assert _is_admin(user) is False


# @verifies REQ-0082
def test_a_real_token_with_a_retired_realm_group_grants_nothing(legacy_token):
    user = _verified(legacy_token)
    groups = user.claims.get("groups") or []
    assert "/admins" in groups or "admins" in groups, (
        "fixture drift: the legacy token carries no realm group"
    )
    assert not is_platform_admin(user.claims)
    assert _is_admin(user) is False


# @verifies REQ-0006
def test_a_real_service_token_may_ingest_and_may_not_administer(service_token):
    user = _verified(service_token)
    assert user.is_service_account
    engine = get_policy_engine()
    raw = engine.evaluate(
        "data.celine.nudging.authz.is_ingest", policy_input_dict(user, action="ingest")
    )
    assert raw["result"][0]["expressions"][0]["value"] is True
    assert _is_admin(user) is False


# ---------------------------------------------------------------------------
# The same, through HTTP and the real middleware
# ---------------------------------------------------------------------------


# @verifies REQ-0005
async def test_a_real_platform_admin_reads_the_admin_list(real_app, admin_token):
    response = await _get(real_app, admin_token, "/admin/notifications")
    assert response.status_code == 200, response.text


# @verifies REQ-0082
async def test_a_real_organisation_admin_is_refused_the_admin_list(real_app, org_admin_token):
    response = await _get(real_app, org_admin_token, "/admin/notifications")
    assert response.status_code == 403, response.text
    assert "platform-admin" in response.json()["detail"]


# @verifies REQ-0082
async def test_a_real_retired_realm_group_token_is_refused_the_admin_list(
    real_app, legacy_token
):
    response = await _get(real_app, legacy_token, "/admin/notifications")
    assert response.status_code == 403, response.text
