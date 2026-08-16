"""The `/admin` routes: what an operator can see and do, and what a participant cannot.

Two permissions guard these: `is_admin` for reading everyone's notifications, seeding and
test pushes, `is_ingest` for putting events in. Every route below is checked from both
sides, because an authorisation test that only asserts the allow proves half of nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from celine.nudging.db.models import Rule, ScheduledEvent, Template, WebPushSubscription
from tests.conftest import OTHER_SUB, USER_SUB
from tests.fakes import make_notification

FUTURE = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
FACTS = {"facts_version": "1", "scenario": "reminder", "time": "2026-08-15"}


# ---------------------------------------------------------------------------
# Listing everyone's notifications
# ---------------------------------------------------------------------------


# @verifies REQ-0005
async def test_an_administrator_sees_every_participant_s_notifications(admin_client, db):
    """
    There is no per-row authorisation here: an administrator sees the whole table. The
    only filters are the query parameters, and they are conveniences rather than
    boundaries.
    """
    db.add(make_notification(notification_id="alice", user_id=USER_SUB))
    db.add(make_notification(notification_id="bob", user_id=OTHER_SUB))
    await db.commit()

    response = await admin_client.get("/admin/notifications")

    assert response.status_code == 200
    assert sorted(n["id"] for n in response.json()) == ["alice", "bob"]


# @verifies REQ-0007
async def test_a_participant_may_not_read_the_admin_list(user_client, db):
    db.add(make_notification(notification_id="alice", user_id=USER_SUB))
    await db.commit()

    response = await user_client.get("/admin/notifications")

    assert response.status_code == 403
    assert "nudging.admin" in response.json()["detail"]


# @verifies REQ-0006
async def test_an_ingest_service_may_not_read_the_admin_list(ingest_client):
    assert (await ingest_client.get("/admin/notifications")).status_code == 403


# @verifies REQ-0005
async def test_the_admin_list_filters_by_participant_family_and_severity(admin_client, db):
    db.add(make_notification(notification_id="a", user_id=USER_SUB, family="energy", severity="info"))
    db.add(make_notification(notification_id="b", user_id=OTHER_SUB, family="energy", severity="critical"))
    db.add(make_notification(notification_id="c", user_id=OTHER_SUB, family="onboarding", severity="info"))
    await db.commit()

    async def ids(query: str) -> list[str]:
        return sorted(n["id"] for n in (await admin_client.get(f"/admin/notifications?{query}")).json())

    assert await ids(f"user_id={OTHER_SUB}") == ["b", "c"]
    assert await ids("family=energy") == ["a", "b"]
    assert await ids("severity=critical") == ["b"]


# @verifies REQ-0005
async def test_the_admin_list_hides_deleted_notifications_unless_asked(admin_client, db):
    """
    An operator investigating "why did nobody get this" needs the soft-deleted rows, and
    a participant's deletion is not meant to destroy the record — so they are reachable,
    behind a flag.
    """
    db.add(make_notification(notification_id="live", user_id=USER_SUB))
    db.add(
        make_notification(
            notification_id="gone", user_id=USER_SUB, deleted_at=datetime.now(timezone.utc)
        )
    )
    await db.commit()

    default = await admin_client.get("/admin/notifications")
    included = await admin_client.get("/admin/notifications?include_deleted=true")

    assert [n["id"] for n in default.json()] == ["live"]
    assert sorted(n["id"] for n in included.json()) == ["gone", "live"]


# ---------------------------------------------------------------------------
# Test pushes
# ---------------------------------------------------------------------------


# @verifies REQ-0005
async def test_an_administrator_can_push_to_any_participant(admin_client, db, webpush):
    """
    The only route where a caller names somebody else's `user_id` in the body. It is
    also the only send that writes no `delivery_log` row and no notification — it exists
    to prove the plumbing, and it leaves no trace in the participant's list.
    """
    db.add(
        WebPushSubscription(
            id="s1",
            user_id=USER_SUB,
            endpoint="https://push.test/alice",
            p256dh="p",
            auth="a",
            enabled=True,
        )
    )
    await db.commit()

    response = await admin_client.post(
        "/admin/webpush/send-test", json={"user_id": USER_SUB, "title": "Hi", "body": "There"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "sent": 1, "failed": 0, "notification_id": None}
    assert webpush.endpoints == ["https://push.test/alice"]


# @verifies REQ-0047
async def test_a_test_push_to_a_participant_with_no_browser_says_so(admin_client, webpush):
    response = await admin_client.post(
        "/admin/webpush/send-test", json={"user_id": "nobody"}
    )

    assert response.json() == {
        "status": "no_subscriptions",
        "sent": 0,
        "failed": 0,
        "notification_id": None,
    }
    assert webpush.calls == []


# @verifies REQ-0046
async def test_a_test_push_disables_a_subscription_the_service_has_forgotten(
    admin_client, db, webpush
):
    """
    The same 404/410 handling as a real delivery, which matters because an operator
    sending a test to a stale browser should not have to send the next real notification
    to clear it.
    """
    db.add(
        WebPushSubscription(
            id="s1",
            user_id=USER_SUB,
            endpoint="https://push.test/dead",
            p256dh="p",
            auth="a",
            enabled=True,
        )
    )
    await db.commit()
    webpush.fail("https://push.test/dead", status_code=410)

    response = await admin_client.post(
        "/admin/webpush/send-test", json={"user_id": USER_SUB}
    )

    assert response.json()["failed"] == 1
    assert (await db.execute(select(WebPushSubscription))).scalar_one().enabled is False


# @verifies REQ-0007
async def test_a_participant_may_not_send_a_test_push(user_client):
    """
    Otherwise any logged-in participant could push arbitrary text to any other
    participant's lock screen.
    """
    response = await user_client.post(
        "/admin/webpush/send-test", json={"user_id": OTHER_SUB, "title": "Anything"}
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Seeding over HTTP
# ---------------------------------------------------------------------------


# @verifies REQ-0072
async def test_an_administrator_can_apply_a_seed(admin_client, db):
    """
    The same upserts as the CLI and the startup path, so a rule can be changed without a
    deploy. The payload is **not validated** against the seed schema on this route —
    `validate_seed` is the CLI's job — so a definition this endpoint accepts may be one
    the engine cannot use.
    """
    response = await admin_client.post(
        "/admin/seed/apply",
        json={
            "rules": [
                {
                    "id": "price_up",
                    "name": "Price up",
                    "family": "energy",
                    "type": "alert",
                    "severity": "info",
                    "definition": {"kind": "price_up", "scenarios": ["price_up"]},
                }
            ],
            "templates": [
                {
                    "rule_id": "price_up",
                    "lang": "en",
                    "title_jinja": "T",
                    "body_jinja": "B",
                }
            ],
            "preferences": [{"user_id": USER_SUB, "max_per_day": 4}],
            "overrides": [
                {
                    "rule_id": "price_up",
                    "community_id": "c1",
                    "definition_override": {"threshold_pct": 5},
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "rules": 1,
        "templates": 1,
        "preferences": 1,
        "overrides": 1,
    }
    assert (await db.execute(select(Rule))).scalar_one().id == "price_up"
    assert (await db.execute(select(Template))).scalar_one().id == "tpl_price_up_en"


# @verifies REQ-0072
async def test_applying_the_same_seed_twice_changes_nothing(admin_client, db):
    payload = {
        "rules": [
            {
                "id": "price_up",
                "name": "Price up",
                "family": "energy",
                "type": "alert",
                "severity": "info",
                "definition": {"kind": "price_up"},
            }
        ]
    }

    await admin_client.post("/admin/seed/apply", json=payload)
    await admin_client.post("/admin/seed/apply", json=payload)

    assert len((await db.execute(select(Rule))).scalars().all()) == 1


# @verifies REQ-0007
async def test_a_participant_may_not_seed(user_client):
    """
    Seeding is rule authoring: a participant who could do it could write a rule that
    sends anything to anyone.
    """
    assert (await user_client.post("/admin/seed/apply", json={})).status_code == 403


# @verifies REQ-0006
async def test_an_ingest_service_may_not_seed(ingest_client):
    assert (await ingest_client.post("/admin/seed/apply", json={})).status_code == 403


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


# @verifies REQ-0063
async def test_a_sender_can_schedule_an_event_for_later(ingest_client, db):
    response = await ingest_client.post(
        "/admin/scheduled-events",
        json={
            "event_type": "flexibility.reminder",
            "user_id": USER_SUB,
            "trigger_at": FUTURE,
            "facts": FACTS,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["dispatched_at"] is None

    stored = (await db.execute(select(ScheduledEvent))).scalar_one()
    assert stored.facts == FACTS


# @verifies REQ-0063
async def test_a_scheduled_event_needs_the_same_facts_contract_as_an_ingest(ingest_client):
    """
    Checked now rather than at trigger time, because a violation discovered by the
    scheduler is a `failed` row nobody is watching — while a violation discovered here
    is an answer to the caller.
    """
    empty = await ingest_client.post(
        "/admin/scheduled-events",
        json={"event_type": "e", "user_id": USER_SUB, "trigger_at": FUTURE, "facts": {}},
    )
    assert empty.status_code == 422
    assert empty.json()["detail"] == "Missing facts in scheduled event"

    incomplete = await ingest_client.post(
        "/admin/scheduled-events",
        json={
            "event_type": "e",
            "user_id": USER_SUB,
            "trigger_at": FUTURE,
            "facts": {"time": "2026-08-15"},
        },
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["detail"]["error"] == "invalid_facts_contract"


# @verifies REQ-0064
async def test_an_external_key_makes_scheduling_idempotent(ingest_client, db):
    """
    A sender that retries — or that recomputes a reminder when a commitment changes —
    sends the same `external_key` and gets the existing row rewritten, moved and reset to
    `pending`. Without it, a retry would deliver twice.
    """
    payload = {
        "event_type": "flexibility.reminder",
        "user_id": USER_SUB,
        "external_key": "commitment-42",
        "trigger_at": FUTURE,
        "facts": FACTS,
    }
    first = await ingest_client.post("/admin/scheduled-events", json=payload)

    later = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    second = await ingest_client.post(
        "/admin/scheduled-events", json={**payload, "trigger_at": later}
    )

    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert len((await db.execute(select(ScheduledEvent))).scalars().all()) == 1
    assert (await db.execute(select(ScheduledEvent))).scalar_one().trigger_at.isoformat().startswith(
        later[:13]
    )


# @verifies REQ-0064
async def test_rescheduling_an_event_that_already_ran_makes_it_pending_again(
    ingest_client, db
):
    """
    A dispatched event is not terminal for the *sender*: re-posting the same external key
    resets `status`, `dispatched_at` and `last_error`, and the reminder goes out again.
    Deduplication is what stops that becoming a second notification for the same period.
    """
    payload = {
        "event_type": "flexibility.reminder",
        "user_id": USER_SUB,
        "external_key": "commitment-42",
        "trigger_at": FUTURE,
        "facts": FACTS,
    }
    await ingest_client.post("/admin/scheduled-events", json=payload)
    stored = (await db.execute(select(ScheduledEvent))).scalar_one()
    stored.status = "dispatched"
    stored.dispatched_at = datetime.now(timezone.utc)
    stored.last_error = "an error from last time"
    await db.commit()

    await ingest_client.post("/admin/scheduled-events", json=payload)

    # The request used its own session; this one still holds the row it wrote above.
    db.expunge_all()
    refreshed = (await db.execute(select(ScheduledEvent))).scalar_one()
    assert refreshed.status == "pending"
    assert refreshed.dispatched_at is None
    assert refreshed.last_error is None


# @verifies REQ-0064
async def test_two_events_without_an_external_key_are_two_rows(ingest_client, db):
    """
    The idempotence is opt-in. A sender that omits the key and retries schedules the
    reminder twice, and only deduplication at delivery time stops two notifications.
    """
    payload = {
        "event_type": "flexibility.reminder",
        "user_id": USER_SUB,
        "trigger_at": FUTURE,
        "facts": FACTS,
    }

    await ingest_client.post("/admin/scheduled-events", json=payload)
    await ingest_client.post("/admin/scheduled-events", json=payload)

    assert len((await db.execute(select(ScheduledEvent))).scalars().all()) == 2


# @verifies REQ-0007
async def test_a_participant_may_not_schedule(user_client):
    response = await user_client.post(
        "/admin/scheduled-events",
        json={
            "event_type": "e",
            "user_id": USER_SUB,
            "trigger_at": FUTURE,
            "facts": FACTS,
        },
    )

    assert response.status_code == 403


# @verifies REQ-0006
async def test_an_administrator_may_schedule_too(admin_client):
    response = await admin_client.post(
        "/admin/scheduled-events",
        json={
            "event_type": "e",
            "user_id": USER_SUB,
            "trigger_at": FUTURE,
            "facts": FACTS,
        },
    )

    assert response.status_code == 201


# @verifies REQ-0002
async def test_every_admin_route_needs_a_token(client):
    assert (await client.get("/admin/notifications")).status_code == 401
    assert (await client.post("/admin/seed/apply", json={})).status_code == 401
    assert (await client.post("/admin/webpush/send-test", json={"user_id": "u"})).status_code == 401
    assert (await client.post("/admin/scheduled-events", json={})).status_code == 401
