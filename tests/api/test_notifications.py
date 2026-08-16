"""`/notifications` — what a participant can see and do with their own messages.

Ownership here is not enforced by the policy bundle: the routes filter in SQL on the
identifiers in the token. The bundle publishes a `filters` rule that says the same thing
and nothing reads it (REQ-0010), so these tests are the whole of the access control.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from celine.nudging.db.models import Notification
from celine.nudging.notifications_tracking import sign_click_tracking_token
from tests.conftest import OTHER_SUB, USER_SUB
from tests.fakes import make_notification, make_user


async def _notification(db, **kwargs) -> Notification:
    notification = make_notification(**{"user_id": USER_SUB, **kwargs})
    db.add(notification)
    await db.commit()
    return notification


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


# @verifies REQ-0051
async def test_a_participant_sees_their_own_notifications_newest_first(user_client, db):
    now = datetime.now(timezone.utc)
    await _notification(db, notification_id="old", title="Old", created_at=now - timedelta(hours=2))
    await _notification(db, notification_id="new", title="New", created_at=now)
    await _notification(db, notification_id="theirs", user_id=OTHER_SUB)

    response = await user_client.get("/notifications")

    assert response.status_code == 200
    assert [n["id"] for n in response.json()] == ["new", "old"]


# @verifies REQ-0051
async def test_a_soft_deleted_notification_is_never_listed(user_client, db):
    await _notification(db, notification_id="visible")
    await _notification(
        db, notification_id="deleted", deleted_at=datetime.now(timezone.utc)
    )

    response = await user_client.get("/notifications")

    assert [n["id"] for n in response.json()] == ["visible"]


# @verifies REQ-0052
async def test_the_list_can_be_narrowed_to_the_unread_and_paged(user_client, db):
    """
    `limit` is capped at 200 and `offset` must not be negative, so a caller cannot ask
    for the whole table in one request or walk backwards off the start of it.
    """
    now = datetime.now(timezone.utc)
    for index in range(3):
        await _notification(
            db,
            notification_id=f"n{index}",
            created_at=now - timedelta(minutes=index),
            read_at=now if index == 1 else None,
        )

    assert [n["id"] for n in (await user_client.get("/notifications?unread_only=true")).json()] == [
        "n0",
        "n2",
    ]
    assert [n["id"] for n in (await user_client.get("/notifications?limit=1")).json()] == ["n0"]
    assert [n["id"] for n in (await user_client.get("/notifications?limit=1&offset=1")).json()] == [
        "n1"
    ]

    assert (await user_client.get("/notifications?limit=0")).status_code == 422
    assert (await user_client.get("/notifications?limit=201")).status_code == 422
    assert (await user_client.get("/notifications?offset=-1")).status_code == 422


# @verifies REQ-0051
async def test_a_notification_carries_its_rule_metadata_and_lifecycle_columns(
    user_client, db
):
    """
    The read path is flat by design: `family`, `type` and `severity` are copied onto the
    row so listing never joins the rules table. A client filtering by severity is
    filtering on a snapshot taken when the message was created.
    """
    await _notification(db, notification_id="n1", family="energy", type="alert", severity="warning")

    [item] = (await user_client.get("/notifications")).json()

    assert item["family"] == "energy"
    assert item["type"] == "alert"
    assert item["severity"] == "warning"
    assert item["status"] == "pending"
    assert item["read_at"] is None
    assert item["clicked_at"] is None
    assert item["deleted_at"] is None
    assert item["nudge_log_id"] is None


# @verifies REQ-0010
async def test_a_participant_is_known_by_their_subject_or_their_username(app, fake_jwt, db):
    """
    Notifications are addressed by whatever id the sender used, and the four senders do
    not agree: some carry the Keycloak `sub`, others the `preferred_username`. Both are
    accepted as the caller's own, which is what stops a participant's list being empty
    for reasons nobody can see.
    """
    from httpx import ASGITransport, AsyncClient

    user = fake_jwt.register(
        make_user(sub="uuid-4bd1", preferred_username="alice", email="alice@example.test")
    )
    await _notification(db, notification_id="by-sub", user_id="uuid-4bd1")
    await _notification(db, notification_id="by-username", user_id="alice")
    await _notification(db, notification_id="somebody-else", user_id="bob")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {user.token}"},
    ) as client:
        response = await client.get("/notifications")

    assert sorted(n["id"] for n in response.json()) == ["by-sub", "by-username"]


# ---------------------------------------------------------------------------
# Reading and deleting
# ---------------------------------------------------------------------------


# @verifies REQ-0053
async def test_marking_as_read_is_idempotent(user_client, db):
    await _notification(db, notification_id="n1")

    first = await user_client.put("/notifications/n1")
    read_at = first.json()["read_at"]
    second = await user_client.put("/notifications/n1")

    assert first.status_code == 200
    assert read_at is not None
    assert second.json()["read_at"] == read_at, "the first read is the one that counts"


# @verifies REQ-0053
async def test_reading_a_deleted_notification_is_410(user_client, db):
    """
    Gone, not missing: the row exists and the caller owns it, and saying so is safe
    because they are the one who deleted it.
    """
    await _notification(db, notification_id="n1", deleted_at=datetime.now(timezone.utc))

    response = await user_client.put("/notifications/n1")

    assert response.status_code == 410


# @verifies REQ-0054
async def test_deleting_is_a_soft_delete_and_is_idempotent(user_client, db):
    """
    The row stays: it is the only link between a delivery log and the message a person
    saw, and losing it would make an audit trail end in nothing.
    """
    await _notification(db, notification_id="n1")

    assert (await user_client.delete("/notifications/n1")).status_code == 204
    assert (await user_client.delete("/notifications/n1")).status_code == 204

    stored = (await db.execute(select(Notification))).scalar_one()
    assert stored.deleted_at is not None


# @verifies REQ-0054
async def test_deleting_twice_keeps_the_first_timestamp(user_client, db):
    await _notification(db, notification_id="n1")

    await user_client.delete("/notifications/n1")
    first = (await db.execute(select(Notification))).scalar_one().deleted_at
    await user_client.delete("/notifications/n1")

    assert (await db.execute(select(Notification))).scalar_one().deleted_at == first


# @verifies REQ-0055
async def test_another_participant_s_notification_is_not_found(other_user_client, db):
    """
    `404`, not `403`. A `403` would confirm that the id names a real notification, which
    is not something a stranger should be able to establish by guessing.
    """
    await _notification(db, notification_id="n1", user_id=USER_SUB)

    assert (await other_user_client.put("/notifications/n1")).status_code == 404
    assert (await other_user_client.delete("/notifications/n1")).status_code == 404

    stored = (await db.execute(select(Notification))).scalar_one()
    assert stored.read_at is None and stored.deleted_at is None


# @verifies REQ-0055
async def test_a_notification_that_does_not_exist_is_also_404(user_client):
    assert (await user_client.put("/notifications/absent")).status_code == 404
    assert (await user_client.delete("/notifications/absent")).status_code == 404


# ---------------------------------------------------------------------------
# Click tracking
# ---------------------------------------------------------------------------


# @verifies REQ-0056
async def test_a_signed_token_records_the_click_without_a_session(client, db):
    """
    The service worker handling the click has no token — the browser may not even have
    the app open. The signature is what stands in for authentication, and this is the
    only write in the service that an unauthenticated caller can perform.
    """
    await _notification(db, notification_id="n1")

    response = await client.post(
        "/notifications/track-click",
        json={"token": sign_click_tracking_token("n1"), "action": "open"},
    )

    assert response.status_code == 200
    stored = (await db.execute(select(Notification))).scalar_one()
    assert stored.clicked_at is not None
    assert stored.click_action == "open"


# @verifies REQ-0057
async def test_a_click_with_no_action_is_recorded_as_the_default(client, db):
    await _notification(db, notification_id="n1")

    await client.post(
        "/notifications/track-click", json={"token": sign_click_tracking_token("n1")}
    )

    assert (await db.execute(select(Notification))).scalar_one().click_action == "default"


# @verifies REQ-0057
async def test_the_first_click_is_the_one_that_is_kept(client, db):
    """
    A second click changes nothing — not the time and not the action — so the column
    answers "did this notification ever work?" rather than "what did they do last?".
    """
    await _notification(db, notification_id="n1")
    token = sign_click_tracking_token("n1")

    await client.post("/notifications/track-click", json={"token": token, "action": "open"})
    first = (await db.execute(select(Notification))).scalar_one().clicked_at
    await client.post("/notifications/track-click", json={"token": token, "action": "dismiss"})

    stored = (await db.execute(select(Notification))).scalar_one()
    assert (stored.clicked_at, stored.click_action) == (first, "open")


# @verifies REQ-0056
async def test_an_unsigned_or_forged_token_is_400(client, db):
    await _notification(db, notification_id="n1")

    for token in ("", "nonsense", "a.b"):
        response = await client.post("/notifications/track-click", json={"token": token})
        assert response.status_code == 400

    assert (await db.execute(select(Notification))).scalar_one().clicked_at is None


# @verifies REQ-0056
async def test_a_valid_token_for_a_notification_that_is_gone_is_404(client, db):
    """
    A notification deleted from the database while a push was in flight. The token is
    genuine and there is nothing to record, which is a `404` rather than an error — the
    service worker has nothing useful to do with either.
    """
    response = await client.post(
        "/notifications/track-click", json={"token": sign_click_tracking_token("absent")}
    )

    assert response.status_code == 404


# @verifies REQ-0057
async def test_a_click_can_be_recorded_on_a_deleted_notification(client, db):
    """
    Soft-deletion is not checked here, so a participant who deletes a message and then
    clicks the push still lands on the row. That is the right order of events for
    analytics — the click really happened — and it means `clicked_at` can be later than
    `deleted_at`.
    """
    await _notification(
        db, notification_id="n1", deleted_at=datetime.now(timezone.utc)
    )

    response = await client.post(
        "/notifications/track-click", json={"token": sign_click_tracking_token("n1")}
    )

    assert response.status_code == 200
    assert (await db.execute(select(Notification))).scalar_one().clicked_at is not None
