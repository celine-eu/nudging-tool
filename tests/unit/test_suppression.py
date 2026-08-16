"""The orchestrator: preferences, the daily cap, and what gets built into a delivery.

This is the file the suite exists for. Everything here decides **not** to send, and a
wrong decision in either direction is invisible: the sender does not wait, the recipient
cannot know what did not arrive, and nothing downstream is code.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from celine.nudging.db.models import DeliveryLog, Notification
from celine.nudging.orchestrator.models import Channel
from celine.nudging.orchestrator.orchestrator import (
    _build_delivery_jobs,
    _explicit_email_recipients,
    _is_email_only_ingest,
    orchestrate,
)
from celine.nudging.orchestrator.policies import can_send_today
from celine.nudging.orchestrator.preferences import (
    get_enabled_notification_kinds,
    get_rule_kind,
    get_user_pref,
)
from tests.fakes import (
    make_notification,
    make_nudge_log,
    make_preference,
    make_rule,
)

KINDS = [
    {"kind": "flexibility_opportunity", "editable": True},
    {"kind": "meter_anomaly", "editable": True},
    {"kind": "extr_event", "editable": False},
]


async def _pending(db, *, user_id="user-alice", community_id=None, rule_id="price_up", facts=None):
    """A created nudge and its pending notification, as the engine leaves them."""
    log = make_nudge_log(
        rule_id=rule_id,
        user_id=user_id,
        community_id=community_id,
        payload={"facts": facts} if facts is not None else {},
    )
    db.add(log)
    db.add(make_notification(nudge_log_id=log.id, rule_id=rule_id, user_id=user_id))
    await db.commit()
    return log


# ---------------------------------------------------------------------------
# The daily cap
# ---------------------------------------------------------------------------


# @verifies REQ-0039
def test_the_cap_is_a_strict_ceiling():
    """
    `max_per_day` is the number sent, not the number of further sends allowed: at three
    sent and a cap of three, the fourth is suppressed.
    """
    assert can_send_today(0, 3) is True
    assert can_send_today(2, 3) is True
    assert can_send_today(3, 3) is False
    assert can_send_today(4, 3) is False


# @verifies REQ-0039
def test_a_cap_of_zero_stops_everything():
    """
    Nothing validates `max_per_day` at the database, so `0` is storable — through the
    seed, not through the API, which requires 1..10 — and it silences the participant
    completely.
    """
    assert can_send_today(0, 0) is False


# @verifies REQ-0038
async def test_a_participant_with_no_preference_row_gets_the_built_in_default(db):
    """
    The default is a literal `3` in `orchestrate`, **not** `MAX_PER_DAY_DEFAULT`. Setting
    that environment variable changes what `seed_db` writes into new preference rows and
    does not change what a participant with no row gets. Filed as
    https://github.com/celine-eu/nudging-tool/issues/36.
    """
    from celine.nudging.config.settings import settings

    log = await _pending(db)
    for index in range(3):
        db.add(
            DeliveryLog(
                id=f"sent-{index}",
                nudge_id="other",
                channel="webpush",
                destination="web:user-alice",
                status="sent",
                sent_at=datetime.now(timezone.utc),
            )
        )
    await db.commit()

    jobs = await orchestrate(db, log.id)

    assert jobs == [], "the fourth of the day is suppressed at the built-in default of 3"
    assert settings.MAX_PER_DAY_DEFAULT == 3, (
        "the two happen to agree today; the point is that nothing keeps them agreeing"
    )


# @verifies REQ-0039
async def test_the_cap_counts_only_what_was_sent_today_to_this_destination(db):
    """
    Three things narrow the count, and each is a way for the cap to be wrong: status
    `sent` (a suppressed or failed attempt does not consume the allowance), the date of
    `sent_at`, and a `LIKE` on the destination prefix.
    """
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    db.add_all(
        [
            DeliveryLog(id="1", nudge_id="n", channel="webpush", destination="web:user-alice", status="sent", sent_at=yesterday),
            DeliveryLog(id="2", nudge_id="n", channel="webpush", destination="web:user-alice", status="failed", sent_at=datetime.now(timezone.utc)),
            DeliveryLog(id="3", nudge_id="n", channel="webpush", destination="web:user-bob", status="sent", sent_at=datetime.now(timezone.utc)),
            DeliveryLog(id="4", nudge_id="n", channel="webpush", destination="web:user-alice", status="sent", sent_at=datetime.now(timezone.utc)),
        ]
    )
    db.add(make_preference("user-alice", max_per_day=2))
    log = await _pending(db)

    jobs = await orchestrate(db, log.id)

    assert jobs, "one send today against a cap of two still leaves room"


# @verifies REQ-0039
async def test_the_destination_prefix_is_per_community(db):
    """
    A participant in two communities has two allowances, because the destination the cap
    counts is `web:<user>:<community>`. The community-less prefix `web:<user>` is a
    prefix of both, so a participant with a *global* notification and a community one is
    counted differently in each direction.
    """
    db.add(make_preference("user-alice", community_id="c1", max_per_day=1))
    db.add(
        DeliveryLog(
            id="1",
            nudge_id="n",
            channel="webpush",
            destination="web:user-alice:c2",
            status="sent",
            sent_at=datetime.now(timezone.utc),
        )
    )
    log = await _pending(db, community_id="c1")

    jobs = await orchestrate(db, log.id)

    assert jobs, "c2's delivery does not consume c1's allowance"


# @verifies REQ-0040
async def test_over_the_cap_every_job_is_logged_suppressed_and_the_notification_with_it(db):
    """
    Suppression is recorded per job, with the reason, and the notification is marked
    `suppressed` rather than left `pending`. Without both, a participant's list would
    show a message that was never delivered as though it were waiting.
    """
    db.add(make_preference("user-alice", max_per_day=1, channel_email=True, email="alice@example.test"))
    db.add(
        DeliveryLog(
            id="1",
            nudge_id="n",
            channel="webpush",
            destination="web:user-alice",
            status="sent",
            sent_at=datetime.now(timezone.utc),
        )
    )
    log = await _pending(db)

    jobs = await orchestrate(db, log.id)

    assert jobs == []
    logs = (await db.execute(select(DeliveryLog).where(DeliveryLog.nudge_id == log.id))).scalars().all()
    assert len(logs) == 2, "one per job that would have been sent — web and email"
    assert {row.status for row in logs} == {"suppressed"}
    assert {row.error for row in logs} == {"rate_limited"}
    assert all(row.sent_at is None for row in logs)

    notification = (await db.execute(select(Notification))).scalar_one()
    assert notification.status == "suppressed"


# @verifies REQ-0039
async def test_the_cap_counts_web_deliveries_only_so_email_is_unbounded(db):
    """
    The `LIKE 'web:%'` on the destination means an email delivery never consumes the
    allowance and never checks it. A participant who opted into email can therefore
    receive any number of messages a day while their web channel is capped at three.

    Filed as https://github.com/celine-eu/nudging-tool/issues/37. Stated here as
    behaviour because that is what a reader needs to know today.
    """
    db.add(make_preference("user-alice", max_per_day=1, channel_email=True, email="alice@example.test"))
    for index in range(5):
        db.add(
            DeliveryLog(
                id=f"mail-{index}",
                nudge_id="n",
                channel="email",
                destination="alice@example.test",
                status="sent",
                sent_at=datetime.now(timezone.utc),
            )
        )
    log = await _pending(db)

    jobs = await orchestrate(db, log.id)

    assert [job.channel for job in jobs] == [Channel.web, Channel.email]


# ---------------------------------------------------------------------------
# Which preference row applies
# ---------------------------------------------------------------------------


# @verifies REQ-0037
async def test_the_community_specific_preference_wins_over_the_generic_one(db):
    db.add(make_preference("user-alice", community_id=None, max_per_day=1))
    db.add(make_preference("user-alice", community_id="c1", max_per_day=9))
    await db.commit()

    community = await get_user_pref(db, "user-alice", "c1")
    generic = await get_user_pref(db, "user-alice", None)

    assert community.max_per_day == 9
    assert generic.max_per_day == 1


# @verifies REQ-0037
async def test_the_generic_preference_applies_when_the_community_has_none(db):
    db.add(make_preference("user-alice", community_id=None, max_per_day=7))
    await db.commit()

    assert (await get_user_pref(db, "user-alice", "c1")).max_per_day == 7


# @verifies REQ-0038
async def test_a_participant_with_no_row_at_all_has_no_preference(db):
    assert await get_user_pref(db, "nobody", None) is None


# ---------------------------------------------------------------------------
# Kinds the participant switched off
# ---------------------------------------------------------------------------


# @verifies REQ-0038
def test_a_participant_who_has_chosen_nothing_receives_every_kind():
    """
    Opting in is the default. A missing preference row, a row with no `consents`, and a
    row whose consents are not the expected shape all mean "everything" — which is the
    right default for a service whose first message is a welcome, and which also means a
    corrupted consents blob silently re-enables what a participant switched off.
    """
    every = [k["kind"] for k in KINDS]

    assert get_enabled_notification_kinds(None, KINDS) == every
    assert get_enabled_notification_kinds(make_preference(), KINDS) == every
    assert get_enabled_notification_kinds(
        make_preference(consents={"enabled_notification_kinds": "not-a-list"}), KINDS
    ) == every


# @verifies REQ-0036
def test_a_participant_keeps_only_the_kinds_they_chose():
    pref = make_preference(
        consents={"enabled_notification_kinds": ["flexibility_opportunity"]}
    )

    enabled = get_enabled_notification_kinds(pref, KINDS)

    assert "flexibility_opportunity" in enabled
    assert "meter_anomaly" not in enabled


# @verifies REQ-0037
def test_a_kind_that_is_not_editable_is_added_back_whatever_the_participant_chose():
    """
    Weather alerts are `editable: false` in `active_kinds.yaml`. A participant can
    remove them from the list and they are put back — the opt-out is not offered because
    a civil-protection alert is not a preference.
    """
    pref = make_preference(consents={"enabled_notification_kinds": []})

    assert get_enabled_notification_kinds(pref, KINDS) == ["extr_event"]


# @verifies REQ-0036
def test_a_kind_the_catalog_no_longer_lists_is_dropped():
    """
    A stored consent naming a kind that has since been removed from
    `active_kinds.yaml` is ignored rather than carried forward, so retiring a kind does
    not leave rows enabling something that no longer exists.
    """
    pref = make_preference(
        consents={"enabled_notification_kinds": ["flexibility_opportunity", "retired"]}
    )

    assert "retired" not in get_enabled_notification_kinds(pref, KINDS)


# @verifies REQ-0036
async def test_the_kind_comes_from_the_rule_s_definition(db):
    """
    A rule declares its `kind`, and that is what is matched against the participant's
    choices. A rule with no kind — or an empty one — is matched against nothing and is
    therefore **never suppressed by preferences**.
    """
    db.add(make_rule("with_kind", definition={"kind": "meter_anomaly"}))
    db.add(make_rule("blank_kind", definition={"kind": "   "}))
    db.add(make_rule("no_kind", definition={}))
    await db.commit()

    assert await get_rule_kind(db, "with_kind") == "meter_anomaly"
    assert await get_rule_kind(db, "blank_kind") is None
    assert await get_rule_kind(db, "no_kind") is None
    assert await get_rule_kind(db, "absent") is None


# @verifies REQ-0036
async def test_a_notification_of_a_disabled_kind_is_suppressed_and_recorded(db):
    """
    @verifies REQ-0041
    """
    db.add(
        make_rule("meter_anomaly_rule", definition={"kind": "meter_anomaly"})
    )
    db.add(
        make_preference(
            "user-alice", consents={"enabled_notification_kinds": ["flexibility_opportunity"]}
        )
    )
    log = await _pending(db, rule_id="meter_anomaly_rule")

    jobs = await orchestrate(db, log.id)

    assert jobs == []
    delivery = (await db.execute(select(DeliveryLog))).scalars().all()
    assert [row.error for row in delivery] == ["kind_disabled"]
    assert (await db.execute(select(Notification))).scalar_one().status == "suppressed"


# @verifies REQ-0036
async def test_a_rule_with_no_kind_is_never_suppressed_by_preferences(db):
    db.add(make_rule("price_up", definition={}))
    db.add(make_preference("user-alice", consents={"enabled_notification_kinds": []}))
    log = await _pending(db)

    jobs = await orchestrate(db, log.id)

    assert [job.channel for job in jobs] == [Channel.web]


# ---------------------------------------------------------------------------
# What a delivery is built from
# ---------------------------------------------------------------------------


# @verifies REQ-0042
def test_web_is_always_a_job_and_email_only_when_asked_for():
    """
    The web job is unconditional — `channel_web` on the preference is **not consulted**,
    so a participant cannot turn the web channel off through this path. Email is added
    only when the participant opted in *and* left an address.
    """
    log = make_nudge_log()
    notification = make_notification()

    assert [j.channel for j in _build_delivery_jobs(log, notification, None)] == [Channel.web]

    opted_in = make_preference(channel_email=True, email="alice@example.test")
    assert [j.channel for j in _build_delivery_jobs(log, notification, opted_in)] == [
        Channel.web,
        Channel.email,
    ]

    no_address = make_preference(channel_email=True, email=None)
    assert [j.channel for j in _build_delivery_jobs(log, notification, no_address)] == [Channel.web]

    web_off = make_preference(channel_email=False, email="alice@example.test")
    assert [j.channel for j in _build_delivery_jobs(log, notification, web_off)] == [Channel.web]


# @verifies REQ-0042
def test_the_web_destination_carries_the_community_when_there_is_one():
    """
    The destination string is what the daily cap counts, so its shape is load-bearing
    rather than cosmetic.
    """
    notification = make_notification()

    plain = _build_delivery_jobs(make_nudge_log(), notification, None)[0]
    scoped = _build_delivery_jobs(make_nudge_log(community_id="c1"), notification, None)[0]

    assert plain.destination == "web:user-alice"
    assert scoped.destination == "web:user-alice:c1"


# @verifies REQ-0043
def test_explicit_recipients_are_validated_deduplicated_and_kept_in_order():
    """
    A sender may address a message to email addresses instead of a participant. The list
    is filtered rather than rejected: an unparseable address is dropped **silently**, so
    a sender whose entire list is a typo is told nothing and the message goes nowhere.
    """
    log = make_nudge_log(
        payload={
            "facts": {
                "email_recipients": [
                    "  Second@Example.test ",
                    "first@example.test",
                    "SECOND@example.test",
                    "not-an-email",
                    "",
                    None,
                    42,
                ]
            }
        }
    )

    assert _explicit_email_recipients(log) == ["Second@Example.test", "first@example.test"]


# @verifies REQ-0043
def test_recipients_are_read_only_from_a_facts_dictionary():
    assert _explicit_email_recipients(make_nudge_log(payload={})) == []
    assert _explicit_email_recipients(make_nudge_log(payload={"facts": "no"})) == []
    assert _explicit_email_recipients(make_nudge_log(payload={"facts": {"email_recipients": "no"}})) == []


# @verifies REQ-0043
def test_explicit_recipients_replace_the_participant_s_own_address():
    log = make_nudge_log(payload={"facts": {"email_recipients": ["ops@example.test"]}})
    pref = make_preference(channel_email=True, email="alice@example.test")

    jobs = _build_delivery_jobs(log, make_notification(), pref)

    assert [j.destination for j in jobs] == ["web:user-alice", "ops@example.test"]


# @verifies REQ-0014
def test_an_email_only_ingest_gets_no_web_job():
    """
    @verifies REQ-0042

    An event with no `user_id` is given a synthetic one derived from its recipients, and
    that prefix is what suppresses the web job — there is no browser subscribed to
    `email-ingest:…`. Both halves have to agree: a synthetic id without recipients, or
    recipients under a real user id, both still get the web job.
    """
    recipients = ["ops@example.test"]
    synthetic = make_nudge_log(
        user_id="email-ingest:abc123", payload={"facts": {"email_recipients": recipients}}
    )
    assert _is_email_only_ingest(synthetic, recipients) is True
    assert [j.channel for j in _build_delivery_jobs(synthetic, make_notification(), None)] == [
        Channel.email
    ]

    real_user = make_nudge_log(payload={"facts": {"email_recipients": recipients}})
    assert _is_email_only_ingest(real_user, recipients) is False
    assert _is_email_only_ingest(synthetic, []) is False


# @verifies REQ-0042
def test_every_job_of_one_notification_shares_its_dedup_key_and_ids():
    """
    Web and email are two deliveries of one notification, not two notifications. They
    carry the same `nudge_id`, `notification_id` and `dedup_key` and differ only in
    channel, destination and their own job id.
    """
    log = make_nudge_log(payload={"facts": {"email_recipients": ["ops@example.test"]}})
    notification = make_notification(nudge_log_id=log.id)

    web, email = _build_delivery_jobs(log, notification, None)

    assert web.nudge_id == email.nudge_id == log.id
    assert web.notification_id == email.notification_id == notification.id
    assert web.dedup_key == email.dedup_key == log.dedup_key
    assert web.job_id != email.job_id
    assert (web.title, web.body) == (email.title, email.body)


# ---------------------------------------------------------------------------
# The status a notification ends up with
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["sent"], "sent"),
        (["sent", "failed"], "sent"),
        (["sent", "suppressed"], "sent"),
        (["suppressed"], "suppressed"),
        (["suppressed", "suppressed"], "suppressed"),
        (["failed"], "failed"),
        (["failed", "suppressed"], "failed"),
    ],
)
# @verifies REQ-0041
async def test_the_notification_reports_the_best_outcome_of_its_deliveries(
    db, monkeypatch, statuses, expected
):
    """
    One `sent` makes the notification sent, however many channels failed. Only an
    all-suppressed set is suppressed; anything else that did not send is failed. So a
    participant whose email bounced but whose push arrived sees nothing wrong, which is
    right — and an operator reading `notifications.status` cannot see the bounce either.
    """
    from celine.nudging.orchestrator import orchestrator as orchestrator_module
    from celine.nudging.publishers.base import PublishResult

    log = await _pending(db)
    results = iter(statuses)

    class _Publisher:
        async def send(self, _db, _job):
            return PublishResult(status=next(results))

    monkeypatch.setattr(orchestrator_module, "get_publisher", lambda _c: _Publisher())
    monkeypatch.setattr(
        orchestrator_module,
        "_build_delivery_jobs",
        lambda n, notification, pref: [
            orchestrator_module.DeliveryJob(
                user_id=n.user_id,
                job_id=f"job-{index}",
                rule_id=n.rule_id,
                nudge_id=n.id,
                notification_id=notification.id,
                channel=Channel.web,
                destination=f"web:{n.user_id}",
                title=notification.title,
                body=notification.body,
                dedup_key=n.dedup_key,
            )
            for index in range(len(statuses))
        ],
    )

    await orchestrate(db, log.id)

    assert (await db.execute(select(Notification))).scalar_one().status == expected
