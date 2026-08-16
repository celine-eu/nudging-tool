"""What the middleware lets past, and what it turns into a 401.

The middleware runs before every dependency, so it — not the policy — is what decides
whether a request is ever evaluated. The open-path list is a literal `frozenset` plus a
`/static/` prefix, which means it is exact: `/health/` with a trailing slash is not
`/health`, and no route pattern is consulted.
"""

from __future__ import annotations

import pytest

from celine.nudging.security.auth import _OPEN_PATHS, _is_open


@pytest.mark.parametrize("path", sorted(_OPEN_PATHS))
# @verifies REQ-0001
def test_every_declared_open_path_is_open(path):
    assert _is_open(path) is True


# @verifies REQ-0001
def test_static_assets_are_open_by_prefix():
    assert _is_open("/static/") is True
    assert _is_open("/static/sw.js") is True
    assert _is_open("/static") is False, "the prefix is '/static/', not '/static'"


# @verifies REQ-0001
def test_the_open_list_is_exact_and_carries_the_click_tracker():
    """
    `/notifications/track-click` is open on purpose: a service worker handling a push
    click has no token. It is the one place where a caller reaches a notification
    without proving who they are, and what stands in for authentication there is the
    signed token in the body (REQ-0056).

    Everything else is closed, including the sibling routes under `/notifications`.
    """
    assert _is_open("/notifications/track-click") is True
    assert _is_open("/notifications") is False
    assert _is_open("/notifications/abc") is False
    assert _is_open("/health/") is False
    assert _is_open("/admin/ingest-event") is False
    assert _is_open("/preferences/me") is False


# @verifies REQ-0001
async def test_health_needs_no_token(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# @verifies REQ-0002
async def test_a_protected_route_without_a_header_is_401(client):
    response = await client.get("/notifications")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Authorization header"
    assert response.headers["WWW-Authenticate"] == "Bearer"


# @verifies REQ-0002
async def test_a_token_that_does_not_verify_is_401(app):
    """
    The reason is deliberately not reported: an expired token, a token for another
    realm and a string of nonsense are one answer, because distinguishing them tells an
    attacker which half of the guess was right.
    """
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer not-a-real-token"},
    ) as ac:
        response = await ac.get("/notifications")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired token"
    assert response.headers["WWW-Authenticate"] == "Bearer"


# @verifies REQ-0002
async def test_an_authenticated_participant_reaches_the_route(user_client):
    response = await user_client.get("/notifications")
    assert response.status_code == 200
    assert response.json() == []


# @verifies REQ-0001
async def test_the_click_tracker_is_reachable_without_a_token(client):
    """
    Reaching it is the point; what it does with an unsigned token is REQ-0056. A 400
    here proves the request got past the middleware, which a 401 would not.
    """
    response = await client.post(
        "/notifications/track-click", json={"token": "not.a.token"}
    )
    assert response.status_code == 400
