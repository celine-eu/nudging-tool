"""The two ways a message leaves this service, tested at the library boundary.

`pywebpush.webpush` and `smtplib` are replaced; everything on this side of them is real —
which subscriptions are selected, what the payload contains, which failures disable a
subscription, and what ends up in `delivery_log`. That log is the service's only record
that a delivery was attempted, so its contents are as much the subject here as the send.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from celine.nudging.db.models import DeliveryLog, WebPushSubscription
from celine.nudging.notifications_tracking import unsign_click_tracking_token
from celine.nudging.orchestrator.models import Channel, DeliveryJob
from celine.nudging.publishers.email.worker import send_email
from celine.nudging.publishers.registry import get_publisher
from celine.nudging.publishers.web.worker import send_webpush


def _job(
    *,
    channel: Channel = Channel.web,
    destination: str = "web:user-alice",
    user_id: str = "user-alice",
    community_id: str | None = None,
    notification_id: str | None = "notification-1",
    title: str = "Title",
    body: str = "Body",
) -> DeliveryJob:
    return DeliveryJob(
        user_id=user_id,
        community_id=community_id,
        job_id="job-1",
        rule_id="price_up",
        nudge_id="nudge-1",
        notification_id=notification_id,
        channel=channel,
        destination=destination,
        title=title,
        body=body,
        dedup_key="price_up:user-alice::2026-08-15",
    )


def _second_job() -> DeliveryJob:
    """A second job for the same notification. `delivery_log.id` is the job id, so a
    test that sends twice needs two of them."""
    return _job(channel=Channel.email, destination="alice@example.test").model_copy(
        update={"job_id": "job-2"}
    )


def _subscription(
    endpoint: str, *, user_id: str = "user-alice", community_id=None, enabled: bool = True
) -> WebPushSubscription:
    return WebPushSubscription(
        id=endpoint,
        user_id=user_id,
        community_id=community_id,
        endpoint=endpoint,
        p256dh="p256dh-key",
        auth="auth-key",
        enabled=enabled,
    )


async def _delivery_logs(db) -> list[DeliveryLog]:
    return list((await db.execute(select(DeliveryLog))).scalars().all())


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


# @verifies REQ-0042
def test_only_web_and_email_can_be_published():
    """
    `Channel` declares four values and two of them have no publisher. Telegram and
    WhatsApp are storable on a preference row and raise here, so a delivery job for
    either would take down the request that built it — which is why nothing builds one.
    """
    assert get_publisher(Channel.web) is not None
    assert get_publisher(Channel.email) is not None

    for missing in (Channel.telegram, Channel.whatsapp):
        with pytest.raises(ValueError, match="No publisher registered"):
            get_publisher(missing)


# ---------------------------------------------------------------------------
# Web push
# ---------------------------------------------------------------------------


# @verifies REQ-0044
async def test_a_push_goes_to_every_enabled_subscription_of_that_participant(db, webpush):
    """
    One person, several browsers, one notification each. A disabled subscription and
    another participant's subscription are both excluded by the query rather than by the
    push service rejecting them.
    """
    db.add_all(
        [
            _subscription("https://push.test/alice-laptop"),
            _subscription("https://push.test/alice-phone"),
            _subscription("https://push.test/alice-old", enabled=False),
            _subscription("https://push.test/bob", user_id="user-bob"),
        ]
    )
    await db.commit()

    result = await send_webpush(db, _job())

    assert sorted(webpush.endpoints) == [
        "https://push.test/alice-laptop",
        "https://push.test/alice-phone",
    ]
    assert result.status == "sent"
    assert result.sent_at is not None


# @verifies REQ-0044
async def test_a_community_scoped_push_also_reaches_the_participant_s_global_subscription(
    db, webpush
):
    """
    A subscription registered without a community is treated as belonging to all of
    them, so a browser that subscribed before the participant joined a community keeps
    receiving. A subscription belonging to *another* community does not.
    """
    db.add_all(
        [
            _subscription("https://push.test/global", community_id=None),
            _subscription("https://push.test/c1", community_id="c1"),
            _subscription("https://push.test/c2", community_id="c2"),
        ]
    )
    await db.commit()

    await send_webpush(db, _job(community_id="c1", destination="web:user-alice:c1"))

    assert sorted(webpush.endpoints) == ["https://push.test/c1", "https://push.test/global"]


# @verifies REQ-0044
async def test_a_job_with_no_community_reaches_every_subscription_of_the_participant(
    db, webpush
):
    """
    With no community on the job the community filter is not applied at all, so a
    community-scoped subscription receives a message addressed to nobody's community.
    That is the asymmetry: scoping down is filtered, scoping up is not.
    """
    db.add_all(
        [
            _subscription("https://push.test/global", community_id=None),
            _subscription("https://push.test/c1", community_id="c1"),
        ]
    )
    await db.commit()

    await send_webpush(db, _job())

    assert len(webpush.endpoints) == 2


# @verifies REQ-0045
async def test_the_payload_carries_the_message_the_ids_and_a_signed_click_token(db, webpush):
    """
    The service worker needs the ids to report a click, and the token is what lets it do
    so from a page that has no session. The token is verifiable and names exactly the
    notification it was minted for.
    """
    db.add(_subscription("https://push.test/alice"))
    await db.commit()

    await send_webpush(db, _job(title="Price up", body="By 12%"))

    payload = json.loads(webpush.calls[0]["data"])
    assert payload["title"] == "Price up"
    assert payload["body"] == "By 12%"
    assert payload["data"]["nudge_id"] == "nudge-1"
    assert payload["data"]["rule_id"] == "price_up"
    assert payload["data"]["notification_id"] == "notification-1"
    assert payload["data"]["url"] == "/"
    assert unsign_click_tracking_token(payload["data"]["click_tracking_token"]) == "notification-1"

    assert webpush.calls[0]["vapid_private_key"] == "test-vapid-private-key"
    assert webpush.calls[0]["vapid_claims"] == {"sub": "mailto:test@example.test"}


# @verifies REQ-0045
async def test_a_push_without_a_notification_id_carries_no_token(db, webpush):
    db.add(_subscription("https://push.test/alice"))
    await db.commit()

    await send_webpush(db, _job(notification_id=None))

    assert json.loads(webpush.calls[0]["data"])["data"]["click_tracking_token"] is None


@pytest.mark.parametrize("status_code", [404, 410])
# @verifies REQ-0046
async def test_a_subscription_the_push_service_has_forgotten_is_disabled(
    db, webpush, status_code
):
    """
    404 and 410 mean the endpoint is gone for good. Leaving it enabled would retry it
    for every notification for ever, and each retry is a request to somebody else's
    server.
    """
    db.add_all([_subscription("https://push.test/dead"), _subscription("https://push.test/live")])
    await db.commit()
    webpush.fail("https://push.test/dead", status_code=status_code)

    result = await send_webpush(db, _job())

    rows = {s.endpoint: s.enabled for s in (await db.execute(select(WebPushSubscription))).scalars()}
    assert rows["https://push.test/dead"] is False
    assert rows["https://push.test/live"] is True
    assert result.status == "sent", "one surviving subscription is a successful delivery"


# @verifies REQ-0046
async def test_a_transient_failure_leaves_the_subscription_alone(db, webpush):
    """
    A 500 from the push service, or an exception carrying no response at all, is not
    evidence that the browser has gone. Disabling on those would silently unsubscribe
    participants during an outage.
    """
    db.add(_subscription("https://push.test/alice"))
    await db.commit()
    webpush.fail("https://push.test/alice", status_code=503)

    result = await send_webpush(db, _job())

    assert (await db.execute(select(WebPushSubscription))).scalar_one().enabled is True
    assert result.status == "failed"
    assert "boom" in result.error


# @verifies REQ-0046
async def test_an_exception_with_no_response_is_survived(db, webpush):
    db.add(_subscription("https://push.test/alice"))
    await db.commit()
    webpush.fail("https://push.test/alice", status_code=None)

    result = await send_webpush(db, _job())

    assert result.status == "failed"
    assert (await db.execute(select(WebPushSubscription))).scalar_one().enabled is True


# @verifies REQ-0047
async def test_a_participant_with_no_subscription_is_a_failed_delivery(db, webpush):
    """
    This is the ordinary case for anyone who has not allowed notifications in their
    browser, and it is recorded as a **failure** — so `delivery_log` reports failures for
    every participant who simply never subscribed, and a genuine outage looks the same.
    """
    result = await send_webpush(db, _job())

    assert result.status == "failed"
    assert result.error == "no_subscriptions"
    assert webpush.calls == []
    assert (await _delivery_logs(db))[0].status == "failed"


# @verifies REQ-0047
async def test_a_missing_vapid_key_stops_the_send_before_the_subscriptions(
    db, webpush, monkeypatch
):
    """
    Without a private key nothing can be signed. The reason is recorded rather than
    raised, so a service deployed with no VAPID configuration keeps accepting events and
    silently delivers nothing.
    """
    from celine.nudging.publishers.web import worker
    from celine.nudging.utils import Vapid

    monkeypatch.setattr(
        worker, "get_vapid", lambda: Vapid(private_key="", public_key="p", subject="s")
    )
    db.add(_subscription("https://push.test/alice"))
    await db.commit()

    result = await send_webpush(db, _job())

    assert (result.status, result.error) == ("failed", "Missing VAPID_PRIVATE_KEY")
    assert webpush.calls == []


# @verifies REQ-0048
async def test_one_delivery_log_row_per_attempt_naming_the_webpush_channel(db, webpush):
    """
    The row is written whatever the outcome, and its channel is **`webpush`** while the
    job's channel is `web`. The daily cap counts by destination prefix rather than by
    channel, which is the only reason the mismatch does not break it.
    """
    db.add(_subscription("https://push.test/alice"))
    await db.commit()

    await send_webpush(db, _job())

    rows = await _delivery_logs(db)
    assert len(rows) == 1
    assert rows[0].channel == "webpush"
    assert rows[0].id == "job-1"
    assert rows[0].nudge_id == "nudge-1"
    assert rows[0].destination == "web:user-alice"
    assert rows[0].status == "sent"
    assert rows[0].sent_at is not None


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


# @verifies REQ-0049
async def test_an_email_carries_the_title_as_its_subject_and_the_body_as_text(db, smtp):
    """
    The same rendered strings as the push notification, as `text/plain`. Nothing is
    escaped and nothing is wrapped in a template, so what the rule author wrote is what
    the recipient reads.
    """
    result = await send_email(db, _job(channel=Channel.email, destination="alice@example.test"))

    assert result.status == "sent"
    assert [(m.to, m.subject, m.body.strip()) for m in smtp.sent] == [
        ("alice@example.test", "Title", "Body")
    ]


# @verifies REQ-0049
async def test_starttls_is_used_unless_ssl_is_configured(db, smtp, monkeypatch):
    """
    Two mutually exclusive transports, chosen by configuration, and the default is
    STARTTLS on port 587. An operator who sets `SMTP_USE_SSL` gets an implicit-TLS
    connection instead and `SMTP_USE_TLS` is then ignored.
    """
    from celine.nudging.publishers.email import worker

    await send_email(db, _job(channel=Channel.email, destination="alice@example.test"))
    assert (smtp.sent[-1].used_ssl, smtp.sent[-1].used_tls) == (False, True)

    monkeypatch.setattr(worker.settings, "SMTP_USE_SSL", True)
    await send_email(db, _second_job())
    assert (smtp.sent[-1].used_ssl, smtp.sent[-1].used_tls) == (True, False)


# @verifies REQ-0049
async def test_a_username_is_used_to_authenticate_and_its_absence_is_not_an_error(
    db, smtp, monkeypatch
):
    """
    An unauthenticated relay is a supported configuration: with no `SMTP_USERNAME` the
    login step is skipped rather than attempted with an empty string.
    """
    from celine.nudging.publishers.email import worker

    await send_email(db, _job(channel=Channel.email, destination="alice@example.test"))
    assert smtp.sent[-1].logged_in is None

    monkeypatch.setattr(worker.settings, "SMTP_USERNAME", "relay-user")
    monkeypatch.setattr(worker.settings, "SMTP_PASSWORD", "relay-password")
    await send_email(db, _second_job())
    assert smtp.sent[-1].logged_in == "relay-user"


# @verifies REQ-0049
async def test_a_failed_send_is_recorded_rather_than_raised(db, smtp):
    """
    Every exception is caught, including the ones that are not the recipient's fault. The
    orchestrator therefore always gets a result, and one channel failing never stops the
    other from being tried.
    """
    smtp.fail_with = OSError("connection refused")

    result = await send_email(db, _job(channel=Channel.email, destination="alice@example.test"))

    assert result.status == "failed"
    assert result.sent_at is None
    assert "connection refused" in result.error


# @verifies REQ-0047
async def test_an_unconfigured_relay_is_a_failure_not_a_crash(db):
    """
    `SMTP_HOST` and `EMAIL_FROM` are empty by default. A service deployed without them
    accepts events, builds email jobs for anyone who opted in, and records a failure for
    every one of them.
    """
    result = await send_email(db, _job(channel=Channel.email, destination="alice@example.test"))

    assert result.status == "failed"
    assert "Missing SMTP_HOST" in result.error


# @verifies REQ-0048
async def test_the_email_delivery_log_records_the_attempt_either_way(db, smtp):
    await send_email(db, _job(channel=Channel.email, destination="alice@example.test"))
    smtp.fail_with = OSError("nope")
    await send_email(
        db,
        _job(channel=Channel.email, destination="bob@example.test").model_copy(
            update={"job_id": "job-2"}
        ),
    )

    rows = {row.id: row for row in await _delivery_logs(db)}
    assert rows["job-1"].channel == "email"
    assert rows["job-1"].destination == "alice@example.test"
    assert (rows["job-1"].status, rows["job-1"].error) == ("sent", None)
    assert rows["job-1"].sent_at is not None
    assert rows["job-2"].status == "failed"
    assert rows["job-2"].sent_at is None
    assert "nope" in rows["job-2"].error
