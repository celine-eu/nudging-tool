"""`/webpush` — how a browser tells this service where to push.

A subscription is a capability: whoever holds the endpoint and keys can be sent
notifications. The identity is therefore taken from the token and never from the body,
which is what these tests are mostly about.
"""

from __future__ import annotations

from sqlalchemy import select

from celine.nudging.db.models import WebPushSubscription
from tests.conftest import USER_SUB
from tests.fakes import make_user

SUBSCRIPTION = {
    "endpoint": "https://push.test/alice-laptop",
    "keys": {"p256dh": "p256dh-key", "auth": "auth-key"},
}


async def _rows(db) -> list[WebPushSubscription]:
    return list((await db.execute(select(WebPushSubscription))).scalars().all())


# @verifies REQ-0061
async def test_the_public_key_is_what_a_browser_needs_to_subscribe(user_client):
    """
    Readable by any authenticated participant, which is right — it is public by
    definition — and it is served from configuration rather than derived, so a service
    whose key pair does not match itself hands out a key that every subscription will
    later fail to verify.
    """
    response = await user_client.get("/webpush/vapid-public-key")

    assert response.status_code == 200
    assert response.json() == {"public_key": "test-vapid-public-key"}


# @verifies REQ-0061
async def test_a_subscription_is_stored_against_the_caller(user_client, db):
    response = await user_client.post("/webpush/subscribe", json={"subscription": SUBSCRIPTION})

    assert response.status_code == 200
    [row] = await _rows(db)
    assert row.user_id == USER_SUB
    assert row.endpoint == SUBSCRIPTION["endpoint"]
    assert row.p256dh == "p256dh-key"
    assert row.enabled is True
    assert row.community_id is None


# @verifies REQ-0061
async def test_the_caller_cannot_subscribe_on_behalf_of_anybody_else(user_client, db):
    """
    The request body has no `user_id` field at all, so this is structural rather than
    checked: an extra key is ignored by the model and the row is written under the
    token's identity.
    """
    await user_client.post(
        "/webpush/subscribe",
        json={"subscription": SUBSCRIPTION, "user_id": "user-bob"},
    )

    assert {row.user_id for row in await _rows(db)} == {USER_SUB}


# @verifies REQ-0061
async def test_subscribing_again_refreshes_the_keys_rather_than_duplicating(
    user_client, db
):
    """
    A browser rotates its keys without changing its endpoint. Storing a second row would
    mean every notification being pushed twice, once with keys that no longer decrypt.
    """
    await user_client.post("/webpush/subscribe", json={"subscription": SUBSCRIPTION})
    rotated = {**SUBSCRIPTION, "keys": {"p256dh": "new-p256dh", "auth": "new-auth"}}

    await user_client.post("/webpush/subscribe", json={"subscription": rotated})

    [row] = await _rows(db)
    assert (row.p256dh, row.auth) == ("new-p256dh", "new-auth")


# @verifies REQ-0061
async def test_re_subscribing_re_enables_a_subscription_that_was_switched_off(
    user_client, db
):
    """
    The path back for a participant who unsubscribed, and for a browser whose endpoint
    was disabled after a 404 from the push service (REQ-0046).
    """
    await user_client.post("/webpush/subscribe", json={"subscription": SUBSCRIPTION})
    await user_client.post("/webpush/unsubscribe", json={"endpoint": SUBSCRIPTION["endpoint"]})
    assert (await _rows(db))[0].enabled is False

    await user_client.post("/webpush/subscribe", json={"subscription": SUBSCRIPTION})

    assert (await _rows(db))[0].enabled is True


# @verifies REQ-0061
async def test_a_community_scoped_subscription_is_a_separate_row(user_client, db):
    """
    The unique key is (user, endpoint, community), so one browser can hold a global
    subscription and a community-scoped one at once — and a push for that community
    reaches both (REQ-0044).
    """
    await user_client.post("/webpush/subscribe", json={"subscription": SUBSCRIPTION})
    await user_client.post(
        "/webpush/subscribe", json={"subscription": SUBSCRIPTION, "community_id": "c1"}
    )

    assert sorted(row.community_id or "" for row in await _rows(db)) == ["", "c1"]


# @verifies REQ-0010
async def test_a_subscription_is_written_for_every_identifier_the_caller_answers_to(
    app, fake_jwt, db
):
    """
    @verifies REQ-0061

    A participant known by both a `sub` and a `preferred_username` gets **two rows**, one
    per identifier. That is deliberate: a nudge addressed by either id then finds a
    subscription. It also means one browser receives two pushes for a notification that
    happens to be addressed to both, and that disabling one row leaves the other live.
    """
    from httpx import ASGITransport, AsyncClient

    user = fake_jwt.register(
        make_user(sub="uuid-4bd1", preferred_username="alice", email="alice@example.test")
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {user.token}"},
    ) as client:
        await client.post("/webpush/subscribe", json={"subscription": SUBSCRIPTION})

    assert sorted(row.user_id for row in await _rows(db)) == ["alice", "uuid-4bd1"]


# @verifies REQ-0062
async def test_unsubscribing_disables_rather_than_deletes(user_client, db):
    """
    The row stays, so `delivery_log` rows pointing at that destination keep meaning
    something, and a participant who re-subscribes from the same browser reuses it.
    """
    await user_client.post("/webpush/subscribe", json={"subscription": SUBSCRIPTION})

    response = await user_client.post(
        "/webpush/unsubscribe", json={"endpoint": SUBSCRIPTION["endpoint"]}
    )

    assert response.status_code == 200
    [row] = await _rows(db)
    assert row.enabled is False


# @verifies REQ-0062
async def test_unsubscribing_something_that_was_never_subscribed_is_fine(user_client, db):
    """
    Idempotent and silent: a browser that lost its local state and unsubscribes twice,
    or unsubscribes an endpoint the server never saw, gets the same `ok`.
    """
    response = await user_client.post(
        "/webpush/unsubscribe", json={"endpoint": "https://push.test/never-seen"}
    )

    assert response.status_code == 200
    assert await _rows(db) == []


# @verifies REQ-0062
async def test_one_participant_cannot_unsubscribe_another_s_browser(
    user_client, other_user_client, db
):
    """
    The filter is on the caller's identifiers, so the endpoint alone is not enough. It
    answers `ok` either way — a caller cannot learn whether the endpoint exists.
    """
    await user_client.post("/webpush/subscribe", json={"subscription": SUBSCRIPTION})

    response = await other_user_client.post(
        "/webpush/unsubscribe", json={"endpoint": SUBSCRIPTION["endpoint"]}
    )

    assert response.status_code == 200
    assert (await _rows(db))[0].enabled is True


# @verifies REQ-0062
async def test_unsubscribing_is_scoped_to_the_community_it_names(user_client, db):
    await user_client.post("/webpush/subscribe", json={"subscription": SUBSCRIPTION})
    await user_client.post(
        "/webpush/subscribe", json={"subscription": SUBSCRIPTION, "community_id": "c1"}
    )

    await user_client.post(
        "/webpush/unsubscribe",
        json={"endpoint": SUBSCRIPTION["endpoint"], "community_id": "c1"},
    )

    rows = {row.community_id: row.enabled for row in await _rows(db)}
    assert rows == {None: True, "c1": False}


# @verifies REQ-0002
async def test_every_webpush_route_needs_a_token(client):
    assert (await client.get("/webpush/vapid-public-key")).status_code == 401
    assert (
        await client.post("/webpush/subscribe", json={"subscription": SUBSCRIPTION})
    ).status_code == 401
    assert (
        await client.post("/webpush/unsubscribe", json={"endpoint": SUBSCRIPTION["endpoint"]})
    ).status_code == 401
